"""Durable station bookmarks and bounded stream-header inspection.

This module never controls playback. MPD remains responsible for decoding and
playing a station; a successful probe only confirms a plausible HTTP source.
"""

from __future__ import annotations

import copy
import ipaddress
import json
import math
import os
import re
import secrets
import socket
import tempfile
import threading
import time
import unicodedata
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_STATIONS = 256
MAX_URL_LENGTH = 2048
MAX_NAME_LENGTH = 120
MAX_STORAGE_BYTES = 1024 * 1024
PROBE_TIMEOUT_SECONDS = 10.0
MAX_PROBE_REDIRECTS = 5


class RadioError(RuntimeError):
    def __init__(self, status: int, code: str, detail: str):
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail


def _invalid_url(detail: str) -> RadioError:
    return RadioError(400, "invalid_stream_url", detail)


def validate_stream_url(value: object) -> str:
    """Validate without decoding or changing case-sensitive paths or queries.

    HTTP hosts and schemes are canonicalized, but escaped URL bytes remain
    intact. LAN addresses are allowed: this is a trusted home control API.
    """
    if not isinstance(value, str):
        raise _invalid_url("url must be an HTTP or HTTPS stream URL")
    value = value.strip(" ")
    if not value or len(value) > MAX_URL_LENGTH:
        raise _invalid_url(f"url must contain 1–{MAX_URL_LENGTH} characters")
    if any(char.isspace() or unicodedata.category(char) in {"Cc", "Cf"}
           for char in value) or "\\" in value:
        raise _invalid_url("Stream URLs cannot contain whitespace, controls, or backslashes")
    if "#" in value:
        raise _invalid_url("Stream URLs cannot contain fragments")
    if re.search(r"%(?![0-9A-Fa-f]{2})", value):
        raise _invalid_url("Stream URL contains a malformed percent escape")
    if any(char in value for char in '<>"`{}|^'):
        raise _invalid_url("Stream URL contains characters that must be URL-encoded")
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
            raise _invalid_url("Use a direct HTTP or HTTPS stream URL")
        if parts.username is not None or parts.password is not None:
            raise _invalid_url("Stream URLs cannot contain usernames or passwords")
        host = parts.hostname
        port = parts.port
        if not host or (port is not None and not 1 <= port <= 65535):
            raise _invalid_url("Stream URL has an invalid host or port")
        if parts.netloc.endswith(":"):
            raise _invalid_url("Stream URL has an empty port")
        if ":" in host:
            # Reject zone identifiers and ambiguous/unbalanced bracket forms.
            ipaddress.IPv6Address(host)
            if "%" in host or not parts.netloc.startswith("["):
                raise ValueError("Invalid IPv6 host")
            closing = parts.netloc.index("]")
            if parts.netloc[closing + 1:] not in ("", f":{port}"):
                raise ValueError("Invalid IPv6 authority")
            authority = f"[{host.lower()}]"
        else:
            host = host.encode("idna").decode("ascii").lower()
            labels = (host[:-1] if host.endswith(".") else host).split(".")
            if len(host) > 253 or any(not re.fullmatch(
                    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                    for label in labels):
                raise ValueError("Invalid hostname")
            if re.fullmatch(r"[0-9.]+", host) and "." in host:
                ipaddress.IPv4Address(host)
            authority = host
        if port is not None:
            authority += f":{port}"
        result = urlunsplit((parts.scheme.lower(), authority, parts.path or "/",
                            parts.query, ""))
        # urllib/MPD accept escaped paths; ask callers to escape raw Unicode
        # there instead of silently modifying a signed or case-sensitive URL.
        result.encode("ascii")
        if len(result) > MAX_URL_LENGTH:
            raise ValueError("URL too long")
        return result
    except (ValueError, UnicodeError) as exc:
        raise _invalid_url("Stream URL has an invalid host, port, or unescaped path") from exc


def validate_station_name(value: object) -> str:
    if not isinstance(value, str):
        raise RadioError(400, "invalid_station_name", "name must be a nonempty string")
    name = value.strip()
    if (not name or len(name) > MAX_NAME_LENGTH or
            any(unicodedata.category(char) in {"Cc", "Cf"} for char in value)):
        raise RadioError(400, "invalid_station_name",
                         f"name must contain 1–{MAX_NAME_LENGTH} printable characters")
    return name


def _clean_generated_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    value = "".join(" " if unicodedata.category(char) in {"Cc", "Cf"} else char
                    for char in value)
    return " ".join(value.split())[:MAX_NAME_LENGTH].strip()


def fallback_station_name(url: str) -> str:
    parts = urlsplit(validate_stream_url(url))
    host = parts.hostname or "Radio station"
    if host.startswith("www."):
        host = host[4:]
    leaf = unquote(parts.path.rstrip("/").rsplit("/", 1)[-1])
    leaf = re.sub(r"\.(mp3|aac|aacp|ogg|opus|flac|m3u8?)$", "", leaf, flags=re.I)
    leaf = re.sub(r"[_-]+", " ", leaf)
    leaf = _clean_generated_name(leaf)
    return _clean_generated_name(f"{host} / {leaf}" if leaf else host)


class _SafeRedirects(HTTPRedirectHandler):
    """Validate every destination; never drain a potentially infinite body."""

    def __init__(self, deadline: float):
        super().__init__()
        self.deadline = deadline
        self.count = 0

    def http_error_302(self, req, fp, code, msg, headers):
        try:
            location = headers.get("Location") or headers.get("URI")
            if not location:
                raise RadioError(422, "station_probe_failed",
                                 "Stream redirect did not supply a destination")
            # Validate raw controls before urljoin/urlsplit can strip them.
            if any(char.isspace() or unicodedata.category(char) in {"Cc", "Cf"}
                   for char in location) or "\\" in location:
                raise _invalid_url("Stream redirect contains invalid URL characters")
            target = validate_stream_url(urljoin(req.full_url, location))
            self.count += 1
            remaining = self.deadline - time.monotonic()
            if self.count > MAX_PROBE_REDIRECTS:
                raise RadioError(422, "station_probe_failed", "Stream redirected too many times")
            if remaining <= 0:
                raise RadioError(504, "station_probe_failed", "Stream inspection timed out")
        finally:
            fp.close()
        # urllib adds Host to unredirected headers; copying header_items() here
        # would send the old hostname to a different CDN after a redirect.
        redirected = Request(target, headers=dict(req.headers), method="GET")
        return self.parent.open(redirected, timeout=remaining)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def probe_station(url: object, *, timeout: float = PROBE_TIMEOUT_SECONDS) -> str:
    """Inspect response headers only, close immediately, and return a name.

    MIME validation is a preflight check, not a promise that MPD can decode the
    stream. PLS and ordinary M3U station directories must be resolved by users.
    """
    url = validate_stream_url(url)
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive finite number")
    opener = build_opener(_SafeRedirects(time.monotonic() + timeout))
    request = Request(url, headers={
        "User-Agent": "house-audio-server station probe",
        "Accept": "audio/*, application/vnd.apple.mpegurl, application/x-mpegurl, application/ogg",
    }, method="GET")
    try:
        with opener.open(request, timeout=timeout) as response:
            mime = response.headers.get_content_type().lower()
            if mime in {"audio/x-scpls", "application/pls+xml", "application/x-scpls"}:
                raise RadioError(422, "unsupported_stream",
                                 "PLS station playlists are not supported; paste the audio stream URL")
            if mime in {"audio/mpegurl", "audio/x-mpegurl"}:
                raise RadioError(422, "unsupported_stream",
                                 "M3U station playlists are not supported; paste the audio stream or HLS URL")
            if not (mime.startswith("audio/") or mime in {
                    "application/vnd.apple.mpegurl", "application/x-mpegurl", "application/ogg"}):
                raise RadioError(422, "unsupported_stream",
                                 "URL did not return an audio stream or HLS manifest; paste a direct stream URL")
            return (_clean_generated_name(response.headers.get("icy-name"))
                    or fallback_station_name(url))
    except HTTPError as exc:
        exc.close()
        raise RadioError(422, "station_probe_failed",
                         f"Stream returned HTTP {exc.code}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise RadioError(504, "station_probe_failed", "Stream inspection timed out") from exc
    except URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise RadioError(504, "station_probe_failed", "Stream inspection timed out") from exc
        raise RadioError(422, "station_probe_failed", "Could not connect to the stream") from exc
    except (OSError, HTTPException) as exc:
        raise RadioError(422, "station_probe_failed", "Could not read the stream response") from exc


class StationStore:
    """One shared bookmark list with atomic, durable updates and copy-on-read."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._stations = []
        try:
            with self.path.open(encoding="utf-8") as source:
                content = source.read(MAX_STORAGE_BYTES + 1)
        except FileNotFoundError:
            return
        try:
            if len(content) > MAX_STORAGE_BYTES:
                raise ValueError("Station storage exceeds size limit")
            saved = json.loads(content)
            if (not isinstance(saved, dict) or type(saved.get("version")) is not int
                    or saved["version"] != 1 or not isinstance(saved.get("stations"), list)
                    or len(saved["stations"]) > MAX_STATIONS):
                raise ValueError("Invalid station file structure")
            ids, urls = set(), set()
            for entry in saved["stations"]:
                if (not isinstance(entry, dict)
                        or set(entry) != {"id", "url", "name", "nameSource"}
                        or not isinstance(entry["id"], str)
                        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", entry["id"])
                        or entry["nameSource"] not in {"stream", "fallback", "user"}):
                    raise ValueError("Invalid saved station")
                normalized = dict(entry, url=validate_stream_url(entry["url"]),
                                  name=validate_station_name(entry["name"]))
                if normalized["id"] in ids or normalized["url"] in urls:
                    raise ValueError("Duplicate saved station")
                ids.add(normalized["id"])
                urls.add(normalized["url"])
                self._stations.append(normalized)
        except (ValueError, TypeError, RadioError) as exc:
            raise RadioError(503, "station_storage_invalid",
                             "Saved stations are invalid; repair the file before starting the server") from exc

    def list(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(self._stations)

    def _index(self, station_id: object) -> int:
        if (not isinstance(station_id, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", station_id)):
            raise RadioError(400, "invalid_station_id", "stationId must be a valid saved station ID")
        for index, station in enumerate(self._stations):
            if station["id"] == station_id:
                return index
        raise RadioError(404, "station_not_found", "Saved station was not found")

    def get(self, station_id: object) -> dict:
        with self._lock:
            return dict(self._stations[self._index(station_id)])

    def _persist(self, updated: list[dict]) -> None:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                    dir=self.path.parent, prefix=f".{self.path.name}.", delete=False) as target:
                temporary = Path(target.name)
                json.dump({"version": 1, "stations": updated}, target, ensure_ascii=False)
                target.write("\n")
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.path)
            # Replacement committed: retain consistency even if directory fsync
            # reports uncertainty and the caller receives an error.
            self._stations = updated
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as exc:
            raise RadioError(503, "station_write_failed",
                             "Could not confirm station persistence; reload the station list before retrying") from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def add(self, url: object, name: object = None, *, generated_name: object = None) -> tuple[dict, bool]:
        url = validate_stream_url(url)
        fallback = fallback_station_name(url)
        if name is not None:
            resolved_name, source = validate_station_name(name), "user"
        else:
            resolved_name = _clean_generated_name(generated_name) or fallback
            source = "fallback" if resolved_name == fallback else "stream"
        with self._lock:
            for station in self._stations:
                if station["url"] == url:
                    return dict(station), False
            if len(self._stations) >= MAX_STATIONS:
                raise RadioError(409, "station_limit", f"Save at most {MAX_STATIONS} stations")
            station = {"id": "radio-" + secrets.token_hex(12), "url": url,
                       "name": resolved_name, "nameSource": source}
            self._persist(self._stations + [station])
            return dict(station), True

    def rename(self, station_id: object, name: object) -> dict:
        name = validate_station_name(name)
        with self._lock:
            index = self._index(station_id)
            updated = copy.deepcopy(self._stations)
            updated[index].update(name=name, nameSource="user")
            self._persist(updated)
            return dict(updated[index])

    def delete(self, station_id: object) -> dict:
        with self._lock:
            index = self._index(station_id)
            updated = copy.deepcopy(self._stations)
            removed = updated.pop(index)
            self._persist(updated)
            return removed

    def update_generated_name(self, station_id: object, name: object) -> dict:
        with self._lock:
            index = self._index(station_id)
            station = self._stations[index]
            if station["nameSource"] == "user":
                return dict(station)
            name = _clean_generated_name(name)
            if not name or name == station["name"]:
                return dict(station)
            updated = copy.deepcopy(self._stations)
            updated[index].update(name=name, nameSource=(
                "fallback" if name == fallback_station_name(station["url"]) else "stream"))
            self._persist(updated)
            return dict(updated[index])
