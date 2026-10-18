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
import math
import os
import random
import re
import secrets
import signal
import socket
import tempfile
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from radio_history import RadioHistory
from radio_listening import RadioListening
from subwoofers import Subwoofers, SubwooferError
from radio_metadata import normalize_radio_metadata
from radio_stations import (RadioError, StationStore, probe_station,
                            validate_stream_url, validate_station_name)

SERVICE_NAME = "house-audio-server"
SERVICE_VERSION = "0.12.0"

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
RADIO_STATIONS_FILE = os.environ.get(
    "HOUSE_AUDIO_RADIO_STATIONS_FILE", "/var/lib/house-audio-server/radio-stations.json"
)

RADIO_HISTORY_FILE = os.environ.get(
    "HOUSE_AUDIO_RADIO_HISTORY_FILE", "/var/lib/house-audio-server/radio-history.json"
)

RADIO_LISTENING_FILE = os.environ.get(
    "HOUSE_AUDIO_RADIO_LISTENING_FILE", "/var/lib/house-audio-server/radio-listening.json"
)
SUBWOOFERS_FILE = os.environ.get(
    "HOUSE_AUDIO_SUBWOOFERS_FILE", "/var/lib/house-audio-server/subwoofers.json"
)

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
                 lease_seconds=CONTROLLER_LEASE_SECONDS, subwoofers=None):
        if not 0 < heartbeat_seconds < lease_seconds < float("inf"):
            raise ValueError("Require 0 < controller heartbeat < lease expiry")
        self.path = Path(path) if path is not None else None
        self.clock = clock
        self.subwoofers = subwoofers
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
        dependent = self.subwoofers.renderer_ids() if self.subwoofers is not None else set()
        with self._lock:
            now = self.clock()
            controllers = [self._view(key, lease, now) for key, lease in sorted(self._leases.items())]
            live = {item["controllerId"] for item in controllers
                    if item["present"] and item["rendererId"] not in dependent}
            passive, passive_audible, controlled_audible = set(), set(), set()
            renderers = []
            for client in snap.get("clients", []):
                renderer_id = str(client.get("id", ""))
                owner = self._bindings.get(renderer_id)
                present = bool(client.get("present", False))
                audible = present and bool(client.get("audible", False))
                if renderer_id in dependent:
                    renderers.append({"rendererId": renderer_id, "controllerId": owner,
                                      "passive": False, "present": present, "audible": False,
                                      "dependent": True, "eligibleForSubwoofer": False})
                    continue
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
                                  "passive": owner is None, "present": present, "audible": audible,
                                  "dependent": False,
                                  "eligibleForSubwoofer": present if owner is None else audible})
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

    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ApiError(400, "invalid_path", "Control characters are not allowed in paths")
    value = value.strip()
    if value in ("", "."):
        return ""

    if "://" in value:
        raise ApiError(400, "invalid_path", "Use /radio/play for station URLs")
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
            "error": status.get("error"),
            "bitrate": to_int(status.get("bitrate")),
            "audio": status.get("audio"),
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
        if not math.isfinite(position_seconds) or position_seconds < 0:
            raise ApiError(400, "invalid_position", "positionSeconds must be finite and nonnegative")

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
        self._snapshot_received_at = None
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
            result = copy.deepcopy(self._snapshot)
            if (result.get("reachable") and self._snapshot_received_at is not None
                    and time.monotonic() - self._snapshot_received_at > self.stale_after_seconds):
                result.update(reachable=False, error="Snapserver observation is stale")
            return result

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
