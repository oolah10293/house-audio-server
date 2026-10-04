#!/usr/bin/env python3
"""house-audio-server.

Current scope:
- read MPD health/state/queue/library
- basic MPD transport and queue control over HTTP
- live Snapserver renderer presence and leased controller/output state
- passive sessions: final-track drain, fresh-idle startup, and a new default shuffle
- persisted runtime choice of the passive default folder

The service intentionally uses only the Python standard library.
"""

from __future__ import annotations

import copy
import json
import os
import random
import re
import secrets
import socket
import tempfile
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

SERVICE_NAME = "house-audio-server"
SERVICE_VERSION = "0.9.2"

HTTP_BIND = os.environ.get("HOUSE_AUDIO_BIND", "0.0.0.0")
HTTP_PORT = int(os.environ.get("HOUSE_AUDIO_PORT", "8787"))

MPD_HOST = os.environ.get("MPD_HOST", "127.0.0.1")
MPD_PORT = int(os.environ.get("MPD_PORT", "6600"))
MPD_TIMEOUT = float(os.environ.get("MPD_TIMEOUT", "2.0"))

SNAPSERVER_HOST = os.environ.get("SNAPSERVER_HOST", "127.0.0.1")
SNAPSERVER_CONTROL_PORT = int(os.environ.get("SNAPSERVER_CONTROL_PORT", "1705"))
SNAPSERVER_CONNECT_TIMEOUT = float(
    os.environ.get("SNAPSERVER_CONNECT_TIMEOUT", "2.0")
)
SNAPSERVER_RETRY_SECONDS = float(
    os.environ.get("SNAPSERVER_RETRY_SECONDS", "2.0")
)
SNAPSERVER_POLL_SECONDS = float(
    os.environ.get("SNAPSERVER_POLL_SECONDS", "1.0")
)
SNAPSERVER_STALE_AFTER_SECONDS = float(
    os.environ.get("SNAPSERVER_STALE_AFTER_SECONDS", "5.0")
)

PASSIVE_SESSION_POLICY_ENABLED = os.environ.get(
    "PASSIVE_SESSION_POLICY_ENABLED", "true"
).strip().lower() in {"1", "true", "yes", "on"}
PASSIVE_SESSION_POLICY_POLL_SECONDS = float(
    os.environ.get("PASSIVE_SESSION_POLICY_POLL_SECONDS", "0.5")
)
PASSIVE_DEFAULT_FOLDER = os.environ.get("PASSIVE_DEFAULT_FOLDER", "MP3s")
SETTINGS_FILE = os.environ.get(
    "HOUSE_AUDIO_SETTINGS_FILE", "/var/lib/house-audio-server/settings.json"
)
PASSIVE_DEFAULT_FOLDERS = ("MP3s", "Rap")
CONTROLLER_BINDINGS_FILE = os.environ.get(
    "HOUSE_AUDIO_CONTROLLERS_FILE", "/var/lib/house-audio-server/controllers.json"
)
CONTROLLER_HEARTBEAT_SECONDS = float(os.environ.get("CONTROLLER_HEARTBEAT_SECONDS", "5"))
CONTROLLER_LEASE_SECONDS = float(os.environ.get("CONTROLLER_LEASE_SECONDS", "15"))

MAX_JSON_BODY = 2 * 1024 * 1024
MPD_WRITE_LOCK = threading.RLock()


class MpdError(RuntimeError):
    pass


class ApiError(RuntimeError):
    def __init__(self, status: int, code: str, detail: str):
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail


class PassiveDefaultSettings:
    """One server-owned setting; no queue, transport, or shuffle persistence."""

    def __init__(self, path: str, fallback: str = PASSIVE_DEFAULT_FOLDER) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        try:
            with self.path.open(encoding="utf-8") as source:
                saved = json.load(source)
            folder = saved.get("passiveDefaultFolder") if isinstance(saved, dict) else None
        except FileNotFoundError:
            folder = fallback
        # A damaged/unreadable saved setting is a startup error. Do not silently
        # revert to a different folder or overwrite the user's saved choice.
        self._validate(folder)
        self._folder = folder

    @staticmethod
    def _validate(folder: object) -> None:
        if folder not in PASSIVE_DEFAULT_FOLDERS:
            raise ApiError(
                400, "invalid_passive_default_folder",
                "passiveDefaultFolder must be exactly MP3s or Rap",
            )

    def snapshot(self) -> Dict[str, object]:
        with self._lock:
            return {
                "passiveDefaultFolder": self._folder,
                "allowedPassiveDefaultFolders": list(PASSIVE_DEFAULT_FOLDERS),
            }

    def set_default_folder(self, folder: object) -> Dict[str, object]:
        self._validate(folder)
        with self._lock:
            temporary = None
            try:
                # The systemd StateDirectory already supplies the writable
                # parent. Keep the temporary file on the same filesystem.
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=self.path.parent,
                    prefix=f".{self.path.name}.", delete=False,
                ) as target:
                    temporary = Path(target.name)
                    json.dump({"passiveDefaultFolder": folder}, target)
                    target.write("\n")
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, self.path)
                self._folder = folder
                # Keep memory consistent with the replaced file even if the
                # directory durability check fails and the API returns 503.
                directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except OSError as exc:
                raise ApiError(
                    503, "settings_write_failed",
                    "Could not confirm settings persistence; read /settings "
                    "before retrying: " + str(exc),
                ) from exc
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        # Preserve the original settings error if cleanup also
                        # fails; never report it as an MPD availability failure.
                        pass
            return self.snapshot()


class ControllerRegistry:
    """Leased control presence plus durable ownership of Snapcast renderer ids.

    Leases and mute reports are transient. Renderer ownership survives expiry,
    detach, and service restart so a phone cannot become a passive auto-starter.
    """

    def __init__(self, path: Optional[str] = None, clock=time.monotonic,
                 heartbeat_seconds=CONTROLLER_HEARTBEAT_SECONDS,
                 lease_seconds=CONTROLLER_LEASE_SECONDS):
        if not 0 < heartbeat_seconds < lease_seconds < float("inf"):
            raise ValueError("Require 0 < controller heartbeat < lease expiry")
        self.path = Path(path) if path is not None else None
        self.clock = clock
        self.heartbeat_seconds = heartbeat_seconds
        self.lease_seconds = lease_seconds
        self._lock = threading.RLock()
        self._leases: Dict[str, dict] = {}
        self._bindings: Dict[str, str] = {}
        if self.path is not None:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                data = {}
            if not isinstance(data, dict):
                raise ValueError("Controller bindings must be an object")
            for renderer_id, controller_id in data.items():
                self._id(renderer_id, "rendererId")
                self._id(controller_id, "controllerId")
            self._bindings = data

    @staticmethod
    def _id(value, field):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
            raise ApiError(400, "invalid_controller_request", f"Invalid {field}")
        return value

    def _save_binding(self, renderer_id, controller_id):
        if self._bindings.get(renderer_id) == controller_id:
            return
        updated = {**self._bindings, renderer_id: controller_id}
        temporary = None
        try:
            if self.path is not None:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                        dir=self.path.parent, prefix=f".{self.path.name}.", delete=False) as target:
                    temporary = Path(target.name)
                    json.dump(updated, target)
                    target.write("\n")
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, self.path)
                self._bindings = updated
                directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            else:
                self._bindings = updated
        except OSError as exc:
            raise ApiError(503, "controller_storage_failed", str(exc)) from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _view(self, controller_id, lease, now):
        return {
            "controllerId": controller_id, "rendererId": lease["rendererId"],
            "outputMuted": lease["outputMuted"],
            "outputReady": lease["outputReady"],
            "present": not lease["detached"] and now < lease["expires"],
            "leaseRemainingSeconds": round(max(0, lease["expires"] - now), 3),
            "sequence": lease["sequence"],
        }

    def attach(self, payload):
        if not set(payload) <= {"controllerId", "rendererId", "outputMuted", "outputReady"}:
            raise ApiError(400, "invalid_controller_request", "Unknown attach field")
        controller_id = self._id(payload.get("controllerId"), "controllerId")
        renderer_id = payload.get("rendererId")
        if renderer_id is not None:
            self._id(renderer_id, "rendererId")
        output_muted = payload.get("outputMuted", True)
        output_ready = payload.get("outputReady", False)
        if not isinstance(output_muted, bool) or not isinstance(output_ready, bool):
            raise ApiError(400, "invalid_controller_request", "outputMuted and outputReady must be boolean")
        if renderer_id is None and (not output_muted or output_ready):
            raise ApiError(400, "renderer_required", "A controller without a renderer must be muted and not ready")
        with self._lock:
            if renderer_id is not None:
                owner = self._bindings.get(renderer_id)
                if owner is not None and owner != controller_id:
                    raise ApiError(409, "renderer_already_owned", "Renderer belongs to another controller")
                self._save_binding(renderer_id, controller_id)
            now = self.clock()
            lease = {"leaseId": secrets.token_urlsafe(24), "rendererId": renderer_id,
                     "outputMuted": output_muted, "expires": now + self.lease_seconds,
                     "outputReady": output_ready,
                     "sequence": 0, "detached": False}
            self._leases[controller_id] = lease
            return self._response(controller_id, lease, now)

    def _response(self, controller_id, lease, now):
        return {"controller": self._view(controller_id, lease, now),
                "leaseId": lease["leaseId"], "heartbeatSeconds": self.heartbeat_seconds,
                "leaseSeconds": self.lease_seconds}

    def _lookup(self, payload, allow_detached=False):
        controller_id = self._id(payload.get("controllerId"), "controllerId")
        lease = self._leases.get(controller_id)
        if lease is None or payload.get("leaseId") != lease["leaseId"]:
            raise ApiError(409, "stale_controller_lease", "Attach again and use the new lease")
        if not allow_detached and (lease["detached"] or self.clock() >= lease["expires"]):
            raise ApiError(409, "expired_controller_lease", "Attach again; expired leases cannot be renewed")
        return controller_id, lease

    def heartbeat(self, payload):
        if set(payload) != {"controllerId", "leaseId", "sequence", "outputMuted", "outputReady"}:
            raise ApiError(400, "invalid_controller_request", "Heartbeat needs controllerId, leaseId, sequence, outputMuted, outputReady")
        sequence = payload["sequence"]
        if (type(sequence) is not int or sequence <= 0 or not isinstance(payload["outputMuted"], bool)
                or not isinstance(payload["outputReady"], bool)):
            raise ApiError(400, "invalid_controller_request", "Positive integer sequence and boolean output fields required")
        with self._lock:
            controller_id, lease = self._lookup(payload)
            if sequence <= lease["sequence"]:
                raise ApiError(409, "stale_controller_sequence", "Use a newer sequence; do not replay old output state")
            if lease["rendererId"] is None and (not payload["outputMuted"] or payload["outputReady"]):
                raise ApiError(400, "renderer_required", "A controller without a renderer must be muted and not ready")
            now = self.clock()
            lease.update(sequence=sequence, outputMuted=payload["outputMuted"],
                         outputReady=payload["outputReady"], expires=now + self.lease_seconds)
            return self._response(controller_id, lease, now)

    def detach(self, payload):
        if set(payload) != {"controllerId", "leaseId"}:
            raise ApiError(400, "invalid_controller_request", "Detach needs controllerId and leaseId")
        with self._lock:
            controller_id, lease = self._lookup(payload, allow_detached=True)
            lease.update(detached=True, expires=0, outputMuted=True, outputReady=False)
            return {"controllerId": controller_id, "detached": True}

    def snapshot(self, snap):
        with self._lock:
            now = self.clock()
            controllers = [self._view(key, lease, now) for key, lease in sorted(self._leases.items())]
            live = {item["controllerId"] for item in controllers if item["present"]}
            passive, passive_audible, controlled_audible = set(), set(), set()
            renderers = []
            for client in snap.get("clients", []):
                renderer_id = str(client.get("id", ""))
                owner = self._bindings.get(renderer_id)
                present = bool(client.get("present", False))
                audible = present and bool(client.get("audible", False))
                if owner is None:
                    if present:
                        passive.add(renderer_id)
                    if audible:
                        passive_audible.add(renderer_id)
                else:
                    lease = self._leases.get(owner)
                    # Local mute/detach overrides a temporarily stale Snapcast
                    # report. A still-audible renderer can outlive its control
                    # lease, but it never becomes a passive radio.
                    if lease is not None:
                        audible = audible and not lease["outputMuted"] and lease["outputReady"] and not lease["detached"]
                        audible = audible and lease["rendererId"] == renderer_id
                    if audible:
                        controlled_audible.add(owner)
                renderers.append({"rendererId": renderer_id, "controllerId": owner,
                                  "passive": owner is None, "present": present, "audible": audible})
            return {
                "heartbeatSeconds": self.heartbeat_seconds, "leaseSeconds": self.lease_seconds,
                "controllers": controllers, "renderers": renderers,
                "controllerCount": len(live), "passiveCount": len(passive),
                "presentCount": len(passive) + len(live | controlled_audible),
                "audibleCount": len(passive_audible) + len(controlled_audible),
                "passiveIds": sorted(passive),
                "snapserverReachable": bool(snap.get("reachable", False)),
            }


@dataclass
class MpdResponse:
    lines: List[str]


def mpd_quote(value: str) -> str:
    """Quote one MPD protocol argument."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def validate_relative_path(value: str) -> str:
    if not isinstance(value, str):
        raise ApiError(400, "invalid_path", "Path must be a string")

    value = value.strip()
    if value in ("", "."):
        return ""

    if "\\" in value:
        raise ApiError(400, "invalid_path", "Use forward slashes in library paths")

    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ApiError(400, "invalid_path", "Path must stay inside the MPD library")

    normalized = str(path)
    return "" if normalized == "." else normalized


class MpdClient:
    """Small MPD protocol client using only the Python standard library."""

    def __init__(
        self,
        host: str = MPD_HOST,
        port: int = MPD_PORT,
        timeout: float = MPD_TIMEOUT,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    def _readline(self, stream) -> str:
        raw = stream.readline()
        if not raw:
            raise MpdError("MPD closed the connection")
        return raw.decode("utf-8", errors="replace").rstrip("\r\n")

    def _command_on_stream(self, stream, command: str) -> MpdResponse:
        stream.write((command + "\n").encode("utf-8"))
        stream.flush()

        lines: List[str] = []
        while True:
            line = self._readline(stream)
            if line == "OK":
                return MpdResponse(lines)
            if line.startswith("ACK "):
                raise MpdError(line)
            lines.append(line)

    def run(self, *commands: str) -> Tuple[str, List[MpdResponse]]:
        with socket.create_connection(
            (self.host, self.port), timeout=self.timeout
        ) as sock:
            sock.settimeout(self.timeout)
            stream = sock.makefile("rwb", buffering=0)
            greeting = self._readline(stream)
            if not greeting.startswith("OK MPD "):
                raise MpdError(f"Unexpected MPD greeting: {greeting!r}")

            responses = [
                self._command_on_stream(stream, command) for command in commands
            ]
            return greeting.removeprefix("OK MPD ").strip(), responses

    def command(self, command: str) -> None:
        self.run(command)

    def ping(self) -> str:
        protocol_version, _ = self.run("ping")
        return protocol_version

    def state(self) -> Dict[str, object]:
        protocol_version, responses = self.run("status", "currentsong")
        status = parse_mpd_fields(responses[0].lines)
        song = parse_mpd_fields(responses[1].lines)

        return {
            "protocolVersion": protocol_version,
            "transport": status.get("state", "unknown"),
            "elapsedSeconds": to_float(status.get("elapsed")),
            "durationSeconds": to_float(status.get("duration")),
            "volume": to_int(status.get("volume")),
            "repeat": status.get("repeat") == "1",
            "random": status.get("random") == "1",
            "single": status.get("single") == "1",
            "singleMode": status.get("single", "0"),
            "consume": status.get("consume") == "1",
            "queueLength": to_int(status.get("playlistlength")) or 0,
            "queueVersion": to_int(status.get("playlist")),
            "songPosition": to_int(status.get("song")),
            "songId": to_int(status.get("songid")),
            "song": normalize_song(song),
        }

    def queue(self) -> List[Dict[str, object]]:
        _, responses = self.run("playlistinfo")
        records = parse_mpd_records(responses[0].lines, {"file"})
        return [normalize_song(record) for record in records if record.get("file")]

    def reorder_queue(self, song_ids, queue_version):
        """Reorder existing MPD IDs without clearing, playing, pausing, or seeking.

        The API write lock serializes normal house writers. The revision/ID
        checks reject a controller sorting a queue another controller replaced.
        Out-of-band native MPD writes are outside this API's concurrency boundary.
        """
        if (not isinstance(song_ids, list) or any(type(value) is not int or value < 0 for value in song_ids)
                or len(set(song_ids)) != len(song_ids) or type(queue_version) is not int or queue_version < 0):
            raise ApiError(400, "invalid_request", "Provide unique nonnegative songIds and queueVersion")
        with MPD_WRITE_LOCK:
            if self.state().get("queueVersion") != queue_version:
                raise ApiError(409, "stale_queue", "Queue changed; refresh before sorting")
            current = [song["id"] for song in self.queue()]
            if self.state().get("queueVersion") != queue_version:
                raise ApiError(409, "stale_queue", "Queue changed; refresh before sorting")
            if len(current) != len(song_ids) or set(current) != set(song_ids):
                raise ApiError(409, "stale_queue", "songIds must contain every current queue entry exactly once")
            commands = []
            for destination, song_id in enumerate(song_ids):
                source = current.index(song_id)
                if source != destination:
                    commands.append(f"moveid {song_id} {destination}")
                    current.insert(destination, current.pop(source))
            if commands:
                self.run(*commands)
            return self.state()

    def browse(self, path: str) -> List[Dict[str, object]]:
        command = "lsinfo" if not path else f"lsinfo {mpd_quote(path)}"
        _, responses = self.run(command)
        records = parse_mpd_records(
            responses[0].lines, {"file", "directory", "playlist"}
        )
        return [normalize_library_entry(record) for record in records]

    def library_status(self):
        _, responses = self.run("status")
        job = to_int(parse_mpd_fields(responses[0].lines).get("updating_db"))
        return {"updating": job is not None, "jobId": job}

    def update_library(self):
        # Incremental index update only: never touch the queue or transport.
        _, responses = self.run("update")
        job = to_int(parse_mpd_fields(responses[0].lines).get("updating_db"))
        return {"updating": job is not None, "jobId": job}

    def library_files(self, path: str) -> List[str]:
        """List indexed files recursively, matching MPD's folder-add scope."""
        path = validate_relative_path(path)
        _, responses = self.run(f"listall {mpd_quote(path)}")
        records = parse_mpd_records(responses[0].lines, {"file", "directory"})
        return [record["file"] for record in records if record.get("file")]

    def replace_queue(
        self,
        tracks: List[str],
        start_index: int = 0,
        play: bool = True,
        position_seconds: float = 0.0,
        before_write: Optional[Callable[[], None]] = None,
    ) -> Dict[str, object]:
        if not tracks:
            raise ApiError(400, "empty_queue", "tracks must contain at least one item")
        if start_index < 0 or start_index >= len(tracks):
            raise ApiError(400, "invalid_start_index", "startIndex is outside tracks")
        if position_seconds < 0:
            raise ApiError(400, "invalid_position", "positionSeconds cannot be negative")

        safe_tracks = [validate_relative_path(item) for item in tracks]
        if any(not item for item in safe_tracks):
            raise ApiError(400, "invalid_track", "Track paths cannot be empty")

        with MPD_WRITE_LOCK:
            if before_write is not None:
                before_write()
            commands = ["clear"]
            commands.extend(f"add {mpd_quote(track)}" for track in safe_tracks)
            if play:
                commands.append(f"play {start_index}")
                if position_seconds > 0:
                    commands.append(f"seekcur {position_seconds:.3f}")
            self.run(*commands)
            return self.state()



class SnapcastError(RuntimeError):
    pass


class SnapcastMonitor:
    """Poll Snapserver status and derive effective renderer presence.

    Snapserver can temporarily keep a hard-powered-off client marked
    connected while the underlying TCP stream socket is still ESTABLISHED.
    Snapcast clients also carry a lastSeen timestamp that is updated by their
    periodic time-sync traffic. Policy should therefore use `present`, not
    Snapserver's raw `connected` flag alone.

    A short local Server.GetStatus poll keeps lastSeen fresh for healthy
    clients and lets the service age out abruptly powered-off renderers even
    before the kernel/Snapserver finally tears down the stale TCP socket.
    """

    def __init__(
        self,
        host: str = SNAPSERVER_HOST,
        port: int = SNAPSERVER_CONTROL_PORT,
        connect_timeout: float = SNAPSERVER_CONNECT_TIMEOUT,
        retry_seconds: float = SNAPSERVER_RETRY_SECONDS,
        poll_seconds: float = SNAPSERVER_POLL_SECONDS,
        stale_after_seconds: float = SNAPSERVER_STALE_AFTER_SECONDS,
    ) -> None:
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.retry_seconds = retry_seconds
        self.poll_seconds = poll_seconds
        self.stale_after_seconds = stale_after_seconds
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._request_id = 0
        self._snapshot: Dict[str, object] = {
            "reachable": False,
            "host": self.host,
            "controlPort": self.port,
            "connectedCount": 0,
            "presentCount": 0,
            "audibleCount": 0,
            "clients": [],
            "streams": [],
            "staleAfterSeconds": self.stale_after_seconds,
            "error": "not connected yet",
        }

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run,
                name="snapcast-monitor",
                daemon=True,
            )
            self._thread.start()

    def snapshot(self) -> Dict[str, object]:
        with self._lock:
            return copy.deepcopy(self._snapshot)

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _fetch_status(self) -> Dict[str, object]:
        request_id = self._next_id()
        request = {
            "id": request_id,
            "jsonrpc": "2.0",
            "method": "Server.GetStatus",
        }

        with socket.create_connection(
            (self.host, self.port), timeout=self.connect_timeout
        ) as sock:
            sock.settimeout(self.connect_timeout)
            stream = sock.makefile("rwb", buffering=0)
            stream.write(
                (json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8")
            )
            stream.flush()

            while True:
                raw = stream.readline()
                if not raw:
                    raise SnapcastError("Snapserver closed the control connection")

                try:
                    message = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue

                if not isinstance(message, dict) or message.get("id") != request_id:
                    continue

                if "error" in message:
                    raise SnapcastError(str(message["error"]))

                result = message.get("result")
                if not isinstance(result, dict):
                    raise SnapcastError("Invalid Server.GetStatus response")

                status = result.get("server")
                if not isinstance(status, dict):
                    raise SnapcastError("Server.GetStatus omitted server data")
                return status

    def _set_disconnected(self, error: str) -> None:
        with self._lock:
            previous = self._snapshot
            self._snapshot = {
                "reachable": False,
                "host": self.host,
                "controlPort": self.port,
                "connectedCount": 0,
                "presentCount": 0,
                "audibleCount": 0,
                "clients": [],
                "streams": [],
                "serverVersion": previous.get("serverVersion"),
                "controlProtocolVersion": previous.get("controlProtocolVersion"),
                "staleAfterSeconds": self.stale_after_seconds,
                "error": error,
            }

    @staticmethod
    def _last_seen_epoch(client: Dict[str, object]) -> Optional[float]:
        last_seen = client.get("lastSeen")
        if not isinstance(last_seen, dict):
            return None
        sec = last_seen.get("sec")
        usec = last_seen.get("usec", 0)
        if not isinstance(sec, (int, float)) or isinstance(sec, bool):
            return None
        if not isinstance(usec, (int, float)) or isinstance(usec, bool):
            usec = 0
        return float(sec) + float(usec) / 1_000_000.0

    def _apply_status(self, status: Dict[str, object]) -> None:
        groups = status.get("groups")
        streams = status.get("streams")
        server_info = status.get("server")

        if not isinstance(groups, list):
            groups = []
        if not isinstance(streams, list):
            streams = []
        if not isinstance(server_info, dict):
            server_info = {}

        snapserver_info = server_info.get("snapserver")
        if not isinstance(snapserver_info, dict):
            snapserver_info = {}

        now = time.time()
        clients: List[Dict[str, object]] = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            group_id = group.get("id")
            group_name = group.get("name")
            group_muted = bool(group.get("muted", False))
            stream_id = group.get("stream_id")

            raw_clients = group.get("clients")
            if not isinstance(raw_clients, list):
                continue

            for client in raw_clients:
                if not isinstance(client, dict):
                    continue
                config = client.get("config")
                host = client.get("host")
                snapclient = client.get("snapclient")
                if not isinstance(config, dict):
                    config = {}
                if not isinstance(host, dict):
                    host = {}
                if not isinstance(snapclient, dict):
                    snapclient = {}
                volume = config.get("volume")
                if not isinstance(volume, dict):
                    volume = {}

                connected = bool(client.get("connected", False))
                last_seen_epoch = self._last_seen_epoch(client)
                last_seen_age = (
                    max(0.0, now - last_seen_epoch)
                    if last_seen_epoch is not None
                    else None
                )
                fresh = (
                    last_seen_age is not None
                    and last_seen_age <= self.stale_after_seconds
                )
                present = connected and fresh

                client_muted = bool(volume.get("muted", False))
                audible = present and not client_muted and not group_muted

                client_id = client.get("id")
                configured_name = config.get("name")
                host_name = host.get("name")
                display_name = configured_name or host_name or client_id

                clients.append(
                    {
                        "id": client_id,
                        "name": display_name,
                        "connected": connected,
                        "present": present,
                        "audible": audible,
                        "muted": client_muted,
                        "volumePercent": volume.get("percent"),
                        "latencyMs": config.get("latency"),
                        "lastSeenEpoch": last_seen_epoch,
                        "lastSeenAgeSeconds": (
                            round(last_seen_age, 3)
                            if last_seen_age is not None
                            else None
                        ),
                        "groupId": group_id,
                        "groupName": group_name,
                        "groupMuted": group_muted,
                        "streamId": stream_id,
                        "host": {
                            "name": host_name,
                            "ip": host.get("ip"),
                            "mac": host.get("mac"),
                            "os": host.get("os"),
                            "arch": host.get("arch"),
                        },
                        "snapclient": {
                            "name": snapclient.get("name"),
                            "version": snapclient.get("version"),
                            "protocolVersion": snapclient.get("protocolVersion"),
                        },
                    }
                )

        connected_count = sum(1 for client in clients if client["connected"])
        present_count = sum(1 for client in clients if client["present"])
        audible_count = sum(1 for client in clients if client["audible"])

        normalized_streams: List[Dict[str, object]] = []
        for stream in streams:
            if not isinstance(stream, dict):
                continue
            uri = stream.get("uri")
            if not isinstance(uri, dict):
                uri = {}
            normalized_streams.append(
                {
                    "id": stream.get("id"),
                    "status": stream.get("status"),
                    "uri": uri.get("raw"),
                }
            )

        with self._lock:
            self._snapshot = {
                "reachable": True,
                "host": self.host,
                "controlPort": self.port,
                "serverVersion": snapserver_info.get("version"),
                "controlProtocolVersion": snapserver_info.get(
                    "controlProtocolVersion"
                ),
                "connectedCount": connected_count,
                "presentCount": present_count,
                "audibleCount": audible_count,
                "clients": clients,
                "streams": normalized_streams,
                "staleAfterSeconds": self.stale_after_seconds,
            }

    def _run(self) -> None:
        wait_seconds = self.poll_seconds
        while not self._stop.is_set():
            try:
                self._apply_status(self._fetch_status())
                wait_seconds = self.poll_seconds
            except (OSError, SnapcastError) as exc:
                self._set_disconnected(str(exc))
                wait_seconds = self.retry_seconds
            self._stop.wait(wait_seconds)


SNAPCAST_MONITOR = SnapcastMonitor()


class MpdStartupBoundary:
    """Forget the old MPD session once per service process, before new writes.

    A delayed/partially failed reset is retried. Once ready, routine dependency
    reconnects must never reset a session created during this process.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._ready = False
        self._last_error = None

    def snapshot(self):
        with self._lock:
            return {"ready": self._ready, "lastError": self._last_error}

    def require_ready(self):
        if not self.snapshot()["ready"]:
            raise ApiError(503, "startup_pending",
                           "MPD fresh-idle startup reset is not complete; retry after startup.ready")

    def ensure_ready(self, mpd):
        with MPD_WRITE_LOCK:
            if self.snapshot()["ready"]:
                return
            try:
                # Stop first, abandon the old queue, and remove leftover drain
                # modes. Passive startup sets Shuffle/Repeat All for its new queue.
                mpd.run("stop", "clear", "single 0", "consume 0", "repeat 0", "random 0")
                state = mpd.state()
                if (state.get("transport") != "stop" or state.get("queueLength") != 0
                        or state.get("singleMode") != "0"
                        or any(state.get(mode) is not False for mode in ("consume", "repeat", "random"))):
                    raise MpdError("MPD did not reach fresh idle at service startup")
            except (OSError, MpdError) as exc:
                with self._lock:
                    self._last_error = str(exc)
                raise
            with self._lock:
                self._ready = True
                self._last_error = None


MPD_STARTUP = MpdStartupBoundary()


class PassiveSessionPolicy:
    """One house session policy for passive radios and controlling nodes.

    Current behavior:
    - fresh idle + passive renderer present/arrives -> start the default folder;
    - renderer joining active playback -> leave the existing queue untouched;
    - final passive renderer leaves during playback -> finish current track,
      then stop;
    - renderer returns before that track ends -> cancel the pending stop.
    - controllers without audible outputs hold an automatic retained pause;
    - last controller leaving that automatic pause ends the session immediately.

    Each genuinely fresh session gets a new default-folder shuffle. Completed
    drains end the old session, even when MPD lands paused on its next track.
    An ordinary paused session, or a return before the drain ends, is retained.
    """

    def __init__(
        self,
        monitor: SnapcastMonitor,
        mpd_factory=MpdClient,
        enabled: bool = PASSIVE_SESSION_POLICY_ENABLED,
        poll_seconds: float = PASSIVE_SESSION_POLICY_POLL_SECONDS,
        default_folder: str = PASSIVE_DEFAULT_FOLDER,
        shuffle: Optional[Callable[[List[str]], None]] = None,
        settings: Optional[PassiveDefaultSettings] = None,
        controllers: Optional[ControllerRegistry] = None,
        startup: Optional[MpdStartupBoundary] = None,
    ) -> None:
        self.monitor = monitor
        self.mpd_factory = mpd_factory
        self.enabled = enabled
        self.poll_seconds = poll_seconds
        self.settings = settings
        self.startup = startup
        self.controllers = controllers if controllers is not None else ControllerRegistry()
        self.default_folder = (
            str(settings.snapshot()["passiveDefaultFolder"])
            if settings is not None else validate_relative_path(default_folder)
        )
        # OS entropy; never reset a deterministic seed or save a cross-session
        # rotation. Injection lets tests exercise chance repeats without luck.
        self._shuffle = (
            shuffle if shuffle is not None else random.SystemRandom().shuffle
        )
        if not self.default_folder:
            raise ValueError("PASSIVE_DEFAULT_FOLDER must not be empty")
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._previous_present_count: Optional[int] = None
        self._previous_presence: Optional[dict] = None
        self._auto_paused_state: Optional[tuple] = None
        self._pending_final_stop = False
        self._pending_song_id: Optional[int] = None
        self._saved_repeat = False
        self._saved_single_mode = "0"
        self._last_action = "waiting_for_presence_baseline"
        self._last_action_epoch = time.time()

    def start(self) -> None:
        if not self.enabled and self.startup is None:
            with self._lock:
                self._last_action = "disabled"
                self._last_action_epoch = time.time()
            return

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run,
                name="passive-session-policy",
                daemon=True,
            )
            self._thread.start()

    def snapshot(self) -> Dict[str, object]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "mode": "controllers_and_renderers",
                "defaultFolder": self._default_folder(),
                "presentCount": self._previous_present_count,
                "controllerCount": (self._previous_presence or {}).get("controllerCount"),
                "passiveCount": (self._previous_presence or {}).get("passiveCount"),
                "audibleCount": (self._previous_presence or {}).get("audibleCount"),
                "autoPaused": self._auto_paused_state is not None,
                "pauseReason": "no_audible_output" if self._auto_paused_state is not None else None,
                "pendingFinalStop": self._pending_final_stop,
                "pendingSongId": self._pending_song_id,
                "lastAction": self._last_action,
                "lastActionEpoch": self._last_action_epoch,
                "freshIdleAutoStartImplemented": True,
                "freshSessionShuffleImplemented": True,
                "defaultShufflePolicy": "new_each_fresh_session",
                "runtimeDefaultFolderImplemented": self.settings is not None,
                "controllerPresenceImplemented": True,
                "startup": self.startup.snapshot() if self.startup is not None else None,
            }

    def _record(self, action: str) -> None:
        with self._lock:
            self._last_action = action
            self._last_action_epoch = time.time()

    @staticmethod
    def _safe_single_mode(state: Dict[str, object]) -> str:
        value = state.get("singleMode")
        if value in ("0", "1", "oneshot"):
            return str(value)
        return "1" if state.get("single") is True else "0"

    def _default_folder(self) -> str:
        if self.settings is not None:
            return str(self.settings.snapshot()["passiveDefaultFolder"])
        return self.default_folder

    def _start_default_session(self, mpd: MpdClient) -> None:
        # Take one selection at the start of this fresh session. A setting
        # change after this point belongs to the next fresh session.
        folder = self._default_folder()
        tracks = mpd.library_files(folder)
        if not tracks:
            raise MpdError(f"Default folder {folder!r} is empty")
        self._shuffle(tracks)
        # Explicitly randomize the actual queue as well as enabling MPD Random.
        # Starting item zero is unbiased because the entire list was shuffled.
        # A chance repeat of the first song (or order) is allowed, never rerolled.
        commands = [
            "random 0",
            "clear",
            *(f"add {mpd_quote(track)}" for track in tracks),
            "repeat 1",
            "single 0",
            "consume 0",
            "random 1",
            "play 0",
        ]
        with MPD_WRITE_LOCK:
            mpd.run(*commands)

        state = mpd.state()
        if state.get("queueLength", 0) == 0:
            raise MpdError(
                f"Default folder {folder!r} produced an empty queue"
            )
        if state.get("transport") != "play":
            raise MpdError(
                f"MPD did not start default folder {folder!r}"
            )
        self._record("started_default_session")

    def _handle_renderer_arrival(self, mpd: MpdClient, baseline: bool = False) -> None:
        state = mpd.state()
        transport = state.get("transport")

        if transport == "stop":
            self._start_default_session(mpd)
        elif transport == "play":
            self._record(
                "presence_baseline_active_session"
                if baseline
                else "renderer_joined_existing_session"
            )
        elif transport == "pause":
            # Passive-radio arrival is an explicit request for audible music.
            # Resume the existing paused house queue/position; do not replace
            # it with the default MP3s queue.
            with MPD_WRITE_LOCK:
                mpd.command("play")
            self._record(
                "presence_baseline_resumed_paused_session"
                if baseline
                else "renderer_resumed_paused_session"
            )
        else:
            self._record("renderer_arrival_unknown_mpd_state")

    def _begin_final_stop(self, mpd: MpdClient, state: Dict[str, object]) -> None:
        saved_repeat = bool(state.get("repeat", False))
        saved_single = self._safe_single_mode(state)
        commands: List[str] = []

        # Repeat + Single repeats the same song, so Repeat must be temporarily
        # disabled for "finish this song, then stop".
        if saved_repeat:
            commands.append("repeat 0")
        commands.append("single oneshot")

        with MPD_WRITE_LOCK:
            mpd.run(*commands)

        with self._lock:
            self._saved_repeat = saved_repeat
            self._saved_single_mode = saved_single
            self._pending_final_stop = True
            song_id = state.get("songId")
            self._pending_song_id = song_id if isinstance(song_id, int) else None
        self._record("armed_finish_current_track")

    def _restore_options(self, mpd: MpdClient, action: str) -> None:
        with self._lock:
            saved_repeat = self._saved_repeat
            saved_single = self._saved_single_mode

        commands = [
            f"single {saved_single}",
            f"repeat {1 if saved_repeat else 0}",
        ]
        with MPD_WRITE_LOCK:
            mpd.run(*commands)

        with self._lock:
            self._pending_final_stop = False
            self._pending_song_id = None
        self._record(action)

    def _tick(self) -> None:
        # Keep the state decision and its writes together relative to API writes.
        # A failed MPD command must not kill the policy thread or lose a drain.
        try:
            with MPD_WRITE_LOCK:
                self._tick_locked()
        except (OSError, MpdError) as exc:
            # Keep the previous successful sample so a failed departure/pause
            # write is retried, not lost by treating the next poll as a baseline.
            self._record(f"policy_waiting_for_mpd: {exc}")

    def _complete_final_stop(self, mpd: MpdClient) -> None:
        # Normalize MPD's pause-at-next-track artifact to stopped BEFORE restoring
        # options. A later arrival (including after a service restart) must take
        # the fresh-default path, not resume yesterday's controller-selected queue.
        mpd.command("stop")
        self._restore_options(mpd, "final_track_completed_session_idle")

    @staticmethod
    def _session_identity(state):
        return state.get("songId"), state.get("queueVersion")

    def explicit_transport(self, mpd):
        """Called under MPD_WRITE_LOCK before deliberate play/pause/stop/queue edits."""
        if self._pending_final_stop:
            self._restore_options(mpd, "pending_stop_cancelled_explicit_command")
        with self._lock:
            self._auto_paused_state = None

    def _apply_controller_pause(self, mpd, presence):
        state = mpd.state()
        transport = state.get("transport")
        with self._lock:
            auto_paused = self._auto_paused_state
        if auto_paused is not None and (
            transport != "pause" or self._session_identity(state) != auto_paused
        ):
            # A deliberate/out-of-band transport or queue edit supersedes our
            # automatic pause. Controller arrival must not override manual Pause.
            with self._lock:
                self._auto_paused_state = None
            auto_paused = None
        if auto_paused is not None:
            if presence["presentCount"] == 0:
                mpd.command("stop")
                with self._lock:
                    self._auto_paused_state = None
                self._record("last_muted_controller_left_session_idle")
            elif presence["audibleCount"] > 0:
                mpd.command("play")
                with self._lock:
                    self._auto_paused_state = None
                self._record("audible_node_resumed_retained_session")
        elif (transport == "play" and presence["controllerCount"] > 0
              and presence["audibleCount"] == 0):
            mpd.command("pause 1")
            with self._lock:
                self._auto_paused_state = self._session_identity(state)
            self._record("paused_muted_controllers_only")

    def _tick_locked(self) -> None:
        # A process restart ends the old session even if Snapserver is offline
        # or automatic presence policy is disabled. Never infer old pause/drain
        # ownership. New controller registrations are allowed while this retries.
        if self.startup is not None:
            self.startup.ensure_ready(self.mpd_factory())
        if not self.enabled:
            self._record("disabled")
            return
        snap = self.monitor.snapshot()
        if not bool(snap.get("reachable", False)):
            # Unknown renderer state is not proof of departure or inaudibility.
            with self._lock:
                self._previous_present_count = None
                self._previous_presence = None
            self._record("waiting_for_snapserver")
            return
        presence = self.controllers.snapshot(snap)
        present_count = presence["presentCount"]
        with self._lock:
            previous = self._previous_presence
            pending = self._pending_final_stop
        mpd = self.mpd_factory()
        passive_arrival = bool(set(presence["passiveIds"]) - set((previous or {}).get("passiveIds", [])))

        # Resolve the known drain before interpreting any kind of arrival.
        if pending:
            state = mpd.state()
            transport = state.get("transport")
            boundary_pause = (transport == "pause" and self._pending_song_id is not None
                              and state.get("songId") != self._pending_song_id)
            if transport == "stop" or boundary_pause:
                self._complete_final_stop(mpd)
                if presence["passiveCount"] > 0:
                    self._start_default_session(mpd)
                # A late controller, even with audible output, cannot restart
                # the completed old queue or auto-start the fresh default.
            elif present_count > 0 and transport in ("play", "pause"):
                self._restore_options(mpd, "pending_stop_cancelled_renderer_returned"
                                      if presence["passiveCount"] else "pending_stop_cancelled_controller_returned")
                if transport == "pause" and presence["passiveCount"] > 0:
                    mpd.command("play")
                self._apply_controller_pause(mpd, presence)
        else:
            if passive_arrival:
                self._handle_renderer_arrival(mpd, baseline=previous is None)
            elif previous is None:
                self._record("presence_baseline_established")
            self._apply_controller_pause(mpd, presence)
            if previous is not None and previous["presentCount"] > 0 and present_count == 0:
                state = mpd.state()
                if state.get("transport") == "play":
                    self._begin_final_stop(mpd, state)
                elif state.get("transport") != "stop":
                    self._record("last_renderer_left_transport_not_playing")
        with self._lock:
            self._previous_present_count = present_count
            self._previous_presence = presence

    def _run(self) -> None:
        while not self._stop.is_set():
            self._tick()
            self._stop.wait(self.poll_seconds)


class LibraryUpdates:
    """Coalesce browser scans across phones; explicit Refresh bypasses cooldown."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.last_started = None

    def request(self, mpd, force=False):
        with MPD_WRITE_LOCK:
            current = mpd.library_status()
            if current["updating"]:
                return current
            now = self.clock()
            if not force and self.last_started is not None and now - self.last_started < 60:
                return current
            result = mpd.update_library()
            self.last_started = now
            return result


LIBRARY_UPDATES = LibraryUpdates()
PASSIVE_DEFAULT_SETTINGS = PassiveDefaultSettings(SETTINGS_FILE)
CONTROLLERS = ControllerRegistry(CONTROLLER_BINDINGS_FILE)
PASSIVE_SESSION_POLICY = PassiveSessionPolicy(
    SNAPCAST_MONITOR, settings=PASSIVE_DEFAULT_SETTINGS, controllers=CONTROLLERS,
    startup=MPD_STARTUP,
)


def parse_mpd_fields(lines: List[str]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for line in lines:
        key, sep, value = line.partition(": ")
        if sep:
            result[key.lower()] = value
    return result


def parse_mpd_records(
    lines: List[str], record_start_keys: set[str]
) -> List[Dict[str, str]]:
    records: List[Dict[str, str]] = []
    current: Dict[str, str] = {}

    for line in lines:
        key, sep, value = line.partition(": ")
        if not sep:
            continue
        lower_key = key.lower()

        if lower_key in record_start_keys and current:
            records.append(current)
            current = {}

        current[lower_key] = value

    if current:
        records.append(current)

    return records


def normalize_song(fields: Dict[str, str]) -> Optional[Dict[str, object]]:
    if not fields:
        return None

    return {
        "file": fields.get("file"),
        "title": fields.get("title"),
        "artist": fields.get("artist"),
        "albumArtist": fields.get("albumartist"),
        "album": fields.get("album"),
        "track": fields.get("track"),
        "date": fields.get("date"),
        "lastModified": fields.get("last-modified"),
        "durationSeconds": (
            to_float(fields.get("duration"))
            if fields.get("duration") is not None
            else to_float(fields.get("time"))
        ),
        "pos": to_int(fields.get("pos")),
        "id": to_int(fields.get("id")),
    }


def normalize_library_entry(fields: Dict[str, str]) -> Dict[str, object]:
    if "directory" in fields:
        kind = "directory"
        path = fields["directory"]
    elif "file" in fields:
        kind = "file"
        path = fields["file"]
    elif "playlist" in fields:
        kind = "playlist"
        path = fields["playlist"]
    else:
        kind = "unknown"
        path = ""

    entry: Dict[str, object] = {
        "type": kind,
        "path": path,
        "name": path.rsplit("/", 1)[-1] if path else "",
        "lastModified": fields.get("last-modified"),
    }

    if kind == "file":
        entry.update(
            {
                "title": fields.get("title"),
                "artist": fields.get("artist"),
                "albumArtist": fields.get("albumartist"),
                "album": fields.get("album"),
                "track": fields.get("track"),
                "date": fields.get("date"),
                "durationSeconds": (
                    to_float(fields.get("duration"))
                    if fields.get("duration") is not None
                    else to_float(fields.get("time"))
                ),
            }
        )

    return entry


def to_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def to_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def bool_field(payload: Dict[str, object], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ApiError(400, "invalid_request", f"{key} must be true or false")
    return value


class ApiHandler(BaseHTTPRequestHandler):
    server_version = f"HouseAudioServer/{SERVICE_VERSION}"

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def _json(self, status: int, payload: Dict[str, object]) -> None:
        body = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Dict[str, object]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return {}

        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ApiError(400, "invalid_content_length", "Invalid Content-Length") from exc

        if length < 0 or length > MAX_JSON_BODY:
            raise ApiError(413, "request_too_large", "JSON request body is too large")

        raw = self.rfile.read(length)
        if not raw:
            return {}

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(400, "invalid_json", "Request body must be valid UTF-8 JSON") from exc

        if not isinstance(payload, dict):
            raise ApiError(400, "invalid_json", "JSON body must be an object")
        return payload

    def _mpd_state_response(self) -> None:
        snap = SNAPCAST_MONITOR.snapshot()
        self._json(
            200,
            {
                "service": SERVICE_NAME,
                "version": SERVICE_VERSION,
                "mpd": MpdClient().state(),
                "renderers": {
                    "snapserverReachable": snap.get("reachable", False),
                    "connectedCount": snap.get("connectedCount", 0),
                    "presentCount": snap.get("presentCount", 0),
                    "audibleCount": snap.get("audibleCount", 0),
                },
                "sessionPolicy": PASSIVE_SESSION_POLICY.snapshot(),
            },
        )

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path

        try:
            if path == "/controllers":
                self._json(200, {
                    "service": SERVICE_NAME, "version": SERVICE_VERSION,
                    "presence": CONTROLLERS.snapshot(SNAPCAST_MONITOR.snapshot()),
                })
                return

            if path == "/settings":
                self._json(200, {
                    "service": SERVICE_NAME,
                    "version": SERVICE_VERSION,
                    "settings": PASSIVE_DEFAULT_SETTINGS.snapshot(),
                })
                return

            if path == "/health":
                snap = SNAPCAST_MONITOR.snapshot()
                try:
                    protocol_version = MpdClient().ping()
                    mpd_status = {
                        "reachable": True,
                        "protocolVersion": protocol_version,
                    }
                except (OSError, MpdError) as exc:
                    mpd_status = {
                        "reachable": False,
                        "error": str(exc),
                    }

                startup = MPD_STARTUP.snapshot()
                healthy = startup["ready"] and bool(mpd_status["reachable"]) and bool(
                    snap.get("reachable", False)
                )
                self._json(
                    200,
                    {
                        "service": SERVICE_NAME,
                        "version": SERVICE_VERSION,
                        "status": "ok" if healthy else "degraded",
                        "mpd": mpd_status,
                        "snapserver": snap,
                        "startup": startup,
                    },
                )
                return

            if path == "/state":
                self._mpd_state_response()
                return

            if path == "/renderers":
                snap = SNAPCAST_MONITOR.snapshot()
                self._json(
                    200,
                    {
                        "service": SERVICE_NAME,
                        "version": SERVICE_VERSION,
                        "snapserver": snap,
                    },
                )
                return

            if path == "/session":
                self._json(
                    200,
                    {
                        "service": SERVICE_NAME,
                        "version": SERVICE_VERSION,
                        "sessionPolicy": PASSIVE_SESSION_POLICY.snapshot(),
                    },
                )
                return

            if path == "/queue":
                queue = MpdClient().queue()
                self._json(
                    200,
                    {
                        "service": SERVICE_NAME,
                        "version": SERVICE_VERSION,
                        "count": len(queue),
                        "queue": queue,
                    },
                )
                return

            if path == "/browse":
                query = parse_qs(parsed.query, keep_blank_values=True)
                browse_path = validate_relative_path(query.get("path", [""])[0])
                mpd = MpdClient()
                library = mpd.library_status()
                entries = mpd.browse(browse_path)
                self._json(
                    200,
                    {
                        "service": SERVICE_NAME,
                        "version": SERVICE_VERSION,
                        "path": browse_path,
                        "count": len(entries),
                        "entries": entries,
                        "library": library,
                    },
                )
                return

            self._json(
                404,
                {
                    "service": SERVICE_NAME,
                    "version": SERVICE_VERSION,
                    "error": "not_found",
                },
            )
        except ApiError as exc:
            self._json(
                exc.status,
                {
                    "service": SERVICE_NAME,
                    "version": SERVICE_VERSION,
                    "error": exc.code,
                    "detail": exc.detail,
                },
            )
        except (OSError, MpdError) as exc:
            self._json(
                503,
                {
                    "service": SERVICE_NAME,
                    "version": SERVICE_VERSION,
                    "error": "mpd_unavailable",
                    "detail": str(exc),
                },
            )

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path

        try:
            payload = self._read_json()

            with MPD_WRITE_LOCK:
                if path == "/library/update":
                    if set(payload) - {"force"} or type(payload.get("force", False)) is not bool:
                        raise ApiError(400, "invalid_request", "Provide only optional boolean force")
                    result = LIBRARY_UPDATES.request(MpdClient(), payload.get("force", False))
                    self._json(200, {"service": SERVICE_NAME, "version": SERVICE_VERSION,
                                     "library": result})
                    return

                controller_commands = {
                    "/controllers/attach": CONTROLLERS.attach,
                    "/controllers/heartbeat": CONTROLLERS.heartbeat,
                    "/controllers/detach": CONTROLLERS.detach,
                }
                if path in controller_commands:
                    with MPD_WRITE_LOCK:
                        result = controller_commands[path](payload)
                    self._json(200, {"service": SERVICE_NAME, "version": SERVICE_VERSION, **result})
                    return

                if path == "/settings":
                    if set(payload) != {"passiveDefaultFolder"}:
                        raise ApiError(
                            400, "invalid_request",
                            "Provide only passiveDefaultFolder",
                        )
                    settings = PASSIVE_DEFAULT_SETTINGS.set_default_folder(
                        payload["passiveDefaultFolder"]
                    )
                    self._json(200, {
                        "service": SERVICE_NAME,
                        "version": SERVICE_VERSION,
                        "settings": settings,
                    })
                    return

                if path in {"/play", "/pause", "/stop", "/next", "/previous", "/seek",
                            "/shuffle", "/repeat", "/queue/clear", "/queue/replace", "/queue/reorder"}:
                    # Readiness is monotonic for this process. A pending reset may
                    # not acknowledge a new queue/command and later erase it.
                    MPD_STARTUP.require_ready()
                mpd = MpdClient()

                def explicit_transport():
                    PASSIVE_SESSION_POLICY.explicit_transport(mpd)

                if path == "/queue/reorder":
                    if set(payload) != {"songIds", "queueVersion"}:
                        raise ApiError(400, "invalid_request", "Provide only songIds and queueVersion")
                    state = mpd.reorder_queue(payload["songIds"], payload["queueVersion"])
                    self._json(200, {"service": SERVICE_NAME, "version": SERVICE_VERSION, "mpd": state})
                    return

                if path == "/play":
                    with MPD_WRITE_LOCK:
                        explicit_transport()
                        mpd.command("play")
                    self._mpd_state_response()
                    return

                if path == "/pause":
                    with MPD_WRITE_LOCK:
                        explicit_transport()
                        mpd.command("pause 1")
                    self._mpd_state_response()
                    return

                if path == "/stop":
                    with MPD_WRITE_LOCK:
                        explicit_transport()
                        mpd.command("stop")
                    self._mpd_state_response()
                    return

                if path == "/next":
                    with MPD_WRITE_LOCK:
                        mpd.command("next")
                    self._mpd_state_response()
                    return

                if path == "/previous":
                    with MPD_WRITE_LOCK:
                        mpd.command("previous")
                    self._mpd_state_response()
                    return

                if path == "/seek":
                    seconds = payload.get("seconds")
                    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
                        raise ApiError(400, "invalid_request", "seconds must be a number")
                    if seconds < 0:
                        raise ApiError(400, "invalid_request", "seconds cannot be negative")
                    with MPD_WRITE_LOCK:
                        mpd.command(f"seekcur {float(seconds):.3f}")
                    self._mpd_state_response()
                    return

                if path == "/shuffle":
                    enabled = bool_field(payload, "enabled")
                    with MPD_WRITE_LOCK:
                        mpd.command(f"random {1 if enabled else 0}")
                    self._mpd_state_response()
                    return

                if path == "/repeat":
                    enabled = bool_field(payload, "enabled")
                    with MPD_WRITE_LOCK:
                        mpd.command(f"repeat {1 if enabled else 0}")
                    self._mpd_state_response()
                    return

                if path == "/queue/clear":
                    with MPD_WRITE_LOCK:
                        explicit_transport()
                        mpd.command("clear")
                    self._mpd_state_response()
                    return

                if path == "/queue/replace":
                    tracks = payload.get("tracks")
                    if not isinstance(tracks, list) or not all(
                        isinstance(item, str) for item in tracks
                    ):
                        raise ApiError(
                            400, "invalid_request", "tracks must be an array of strings"
                        )

                    start_index = payload.get("startIndex", 0)
                    if not isinstance(start_index, int) or isinstance(start_index, bool):
                        raise ApiError(400, "invalid_request", "startIndex must be an integer")

                    play = payload.get("play", True)
                    if not isinstance(play, bool):
                        raise ApiError(400, "invalid_request", "play must be true or false")

                    position_seconds = payload.get("positionSeconds", 0.0)
                    if not isinstance(position_seconds, (int, float)) or isinstance(
                        position_seconds, bool
                    ):
                        raise ApiError(
                            400, "invalid_request", "positionSeconds must be a number"
                        )

                    state = mpd.replace_queue(
                        tracks=tracks,
                        start_index=start_index,
                        play=play,
                        position_seconds=float(position_seconds),
                        before_write=explicit_transport,
                    )
                    self._json(
                        200,
                        {
                            "service": SERVICE_NAME,
                            "version": SERVICE_VERSION,
                            "mpd": state,
                        },
                    )
                    return

                self._json(
                    404,
                    {
                        "service": SERVICE_NAME,
                        "version": SERVICE_VERSION,
                        "error": "not_found",
                    },
                )
        except ApiError as exc:
            self._json(
                exc.status,
                {
                    "service": SERVICE_NAME,
                    "version": SERVICE_VERSION,
                    "error": exc.code,
                    "detail": exc.detail,
                },
            )
        except (OSError, MpdError) as exc:
            self._json(
                503,
                {
                    "service": SERVICE_NAME,
                    "version": SERVICE_VERSION,
                    "error": "mpd_unavailable",
                    "detail": str(exc),
                },
            )


def main() -> None:
    SNAPCAST_MONITOR.start()
    PASSIVE_SESSION_POLICY.start()
    server = ThreadingHTTPServer((HTTP_BIND, HTTP_PORT), ApiHandler)
    print(
        f"{SERVICE_NAME} {SERVICE_VERSION} listening on "
        f"{HTTP_BIND}:{HTTP_PORT}; MPD={MPD_HOST}:{MPD_PORT}; "
        f"Snapserver={SNAPSERVER_HOST}:{SNAPSERVER_CONTROL_PORT}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
