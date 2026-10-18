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
