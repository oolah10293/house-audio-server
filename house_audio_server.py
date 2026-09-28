#!/usr/bin/env python3
"""Minimal house-audio-server service.

Phase 1 skeleton:
- GET /health
- GET /state
- local MPD status/current-song access

No playback-changing endpoints are implemented yet.
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional, Tuple

SERVICE_NAME = "house-audio-server"
SERVICE_VERSION = "0.1.0"

HTTP_BIND = os.environ.get("HOUSE_AUDIO_BIND", "0.0.0.0")
HTTP_PORT = int(os.environ.get("HOUSE_AUDIO_PORT", "8787"))

MPD_HOST = os.environ.get("MPD_HOST", "127.0.0.1")
MPD_PORT = int(os.environ.get("MPD_PORT", "6600"))
MPD_TIMEOUT = float(os.environ.get("MPD_TIMEOUT", "2.0"))


class MpdError(RuntimeError):
    pass


@dataclass
class MpdResponse:
    lines: List[str]


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


def parse_mpd_fields(lines: List[str]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for line in lines:
        key, sep, value = line.partition(": ")
        if sep:
            result[key.lower()] = value
    return result


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
        "durationSeconds": to_float(fields.get("time")),
        "pos": to_int(fields.get("pos")),
        "id": to_int(fields.get("id")),
    }


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


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "HouseAudioServer/0.1"

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

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]

        if path == "/health":
            try:
                protocol_version = MpdClient().ping()
                self._json(
                    200,
                    {
                        "service": SERVICE_NAME,
                        "version": SERVICE_VERSION,
                        "status": "ok",
                        "mpd": {
                            "reachable": True,
                            "protocolVersion": protocol_version,
                        },
                    },
                )
            except (OSError, MpdError) as exc:
                self._json(
                    200,
                    {
                        "service": SERVICE_NAME,
                        "version": SERVICE_VERSION,
                        "status": "degraded",
                        "mpd": {
                            "reachable": False,
                            "error": str(exc),
                        },
                    },
                )
            return

        if path == "/state":
            try:
                state = MpdClient().state()
                self._json(
                    200,
                    {
                        "service": SERVICE_NAME,
                        "version": SERVICE_VERSION,
                        "mpd": state,
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
            return

        self._json(
            404,
            {
                "service": SERVICE_NAME,
                "version": SERVICE_VERSION,
                "error": "not_found",
            },
        )


def main() -> None:
    server = ThreadingHTTPServer((HTTP_BIND, HTTP_PORT), ApiHandler)
    print(
        f"{SERVICE_NAME} {SERVICE_VERSION} listening on "
        f"{HTTP_BIND}:{HTTP_PORT}; MPD={MPD_HOST}:{MPD_PORT}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
