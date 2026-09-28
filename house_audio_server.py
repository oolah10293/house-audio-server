#!/usr/bin/env python3
"""house-audio-server.

Current scope:
- read MPD health/state/queue/library
- basic MPD transport and queue control over HTTP
- live Snapserver renderer-presence tracking
- no autonomous house-session policy yet

The service intentionally uses only the Python standard library.
"""

from __future__ import annotations

import copy
import json
import os
import socket
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import PurePosixPath
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

SERVICE_NAME = "house-audio-server"
SERVICE_VERSION = "0.3.1"

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

    def browse(self, path: str) -> List[Dict[str, object]]:
        command = "lsinfo" if not path else f"lsinfo {mpd_quote(path)}"
        _, responses = self.run(command)
        records = parse_mpd_records(
            responses[0].lines, {"file", "directory", "playlist"}
        )
        return [normalize_library_entry(record) for record in records]

    def replace_queue(
        self,
        tracks: List[str],
        start_index: int = 0,
        play: bool = True,
        position_seconds: float = 0.0,
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
    server_version = "HouseAudioServer/0.3.1"

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
            },
        )

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path

        try:
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

                healthy = bool(mpd_status["reachable"]) and bool(
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
                entries = MpdClient().browse(browse_path)
                self._json(
                    200,
                    {
                        "service": SERVICE_NAME,
                        "version": SERVICE_VERSION,
                        "path": browse_path,
                        "count": len(entries),
                        "entries": entries,
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
            mpd = MpdClient()

            if path == "/play":
                with MPD_WRITE_LOCK:
                    mpd.command("play")
                self._mpd_state_response()
                return

            if path == "/pause":
                with MPD_WRITE_LOCK:
                    mpd.command("pause 1")
                self._mpd_state_response()
                return

            if path == "/stop":
                with MPD_WRITE_LOCK:
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
