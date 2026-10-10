"""One previous radio song, plus a crash-safe qualifying-current checkpoint.

The daemon owns observation; no phone request is needed. All calls are serialized
by the server's MPD_WRITE_LOCK. This file never stores playback intent or queues.
"""
from __future__ import annotations

import copy
import json
import math
import os
import tempfile
import time
from pathlib import Path

from radio_metadata import normalize_radio_metadata


class RadioHistory:
    MINIMUM_SECONDS = 10.0
    MAX_SAMPLE_GAP = 3.0

    def __init__(self, path=None, clock=time.monotonic, wall_clock=time.time):
        self.path = Path(path) if path is not None else None
        self.clock, self.wall_clock = clock, wall_clock
        self.previous = None
        self.current = None
        self.seconds = 0.0
        self.anchor = None
        self.checkpointed = False
        self.error = None
        self.dirty = False
        self.retry_at = 0.0
        if self.path is not None:
            try:
                with self.path.open(encoding="utf-8") as source:
                    data = json.loads(source.read(65537))
                if not isinstance(data, dict) or data.get("version") != 1:
                    raise ValueError("Unsupported radio history format")
                previous = self._record(data.get("lastPlayed"))
                checkpoint = self._record(data.get("qualifiedCurrent"))
                # Startup intentionally ends the old playback session. Its
                # qualifying current song is now the most recent ended song.
                self.previous = checkpoint or previous
                self.dirty = checkpoint is not None
            except FileNotFoundError:
                pass
            except (OSError, ValueError, TypeError) as exc:
                self.error = f"Could not read radio history: {exc}"

    @staticmethod
    def _text(value):
        return " ".join(value.split())[:1024] if isinstance(value, str) else ""

    @classmethod
    def _record(cls, value):
        if value is None:
            return None
        if (not isinstance(value, dict) or not cls._text(value.get("title"))
                or not isinstance(value.get("station"), dict)):
            raise ValueError("Invalid radio history record")
        for key in ("playedSeconds", "startedAtEpoch"):
            number = value.get(key)
            if (not isinstance(number, (int, float)) or isinstance(number, bool)
                    or not math.isfinite(number) or number <= 0):
                raise ValueError("Invalid radio history timing")
        if value["playedSeconds"] <= cls.MINIMUM_SECONDS:
            raise ValueError("Unqualified radio history record")
        value = normalize_radio_metadata(value)
        if not cls._text(value.get("title")) or value.get("radioContentType") == "nonMusic":
            return None  # Old malformed/non-song envelope: keep the valid record.
        return {"title": cls._text(value["title"]),
                "artist": cls._text(value.get("artist")),
                "album": cls._text(value.get("album")),
                "station": {key: cls._text(value["station"].get(key))
                            for key in ("id", "name", "url")},
                "playedSeconds": value["playedSeconds"],
                "startedAtEpoch": value["startedAtEpoch"]}

    def _identity(self, station, song):
        title = self._text(song.get("title"))
        placeholders = {"unknown", "unknown title", "unknown - unknown", "stream", "live", "live radio"}
        if (song.get("radioContentType") == "nonMusic"
                or not title or not any(c.isalnum() for c in title)
                or title.casefold() in placeholders
                or title.casefold() in {self._text(station.get("name")).casefold(),
                                        self._text(song.get("stationName")).casefold()}):
            return None
        return (station.get("id"), station.get("url"), title,
                self._text(song.get("artist")) or self._text(song.get("albumArtist")))

    def suspend(self):
        """Break accounting across pause, a reconnect, or an unknown interval."""
        self.anchor = None

    def end(self):
        if self.current is not None and self.seconds > self.MINIMUM_SECONDS:
            self.previous = dict(self.current, playedSeconds=self.seconds)
            self.dirty = True
        self.current, self.anchor = None, None
        self.seconds, self.checkpointed = 0.0, False
        self._save()

    def observe(self, station, state, intent="play"):
        now = self.clock()
        song = normalize_radio_metadata(state.get("song") or {})
        identity = self._identity(station, song)
        old_identity = self._identity(self.current["station"], self.current) if self.current else None
        if identity != old_identity:
            self.end()
            if identity is not None:
                self.current = {"title": identity[2], "artist": identity[3],
                                "album": self._text(song.get("album")),
                                "station": {k: self._text(station.get(k)) for k in ("id", "name", "url")},
                                "startedAtEpoch": self.wall_clock()}
        elapsed = state.get("elapsedSeconds")
        valid = (identity is not None and intent == "play" and state.get("transport") == "play"
                 and not state.get("error") and isinstance(elapsed, (int, float))
                 and not isinstance(elapsed, bool) and math.isfinite(elapsed) and elapsed >= 0)
        if valid:
            if self.anchor is not None:
                wall_delta, playback_delta = now - self.anchor[0], elapsed - self.anchor[1]
                # Never count a blocked observer, elapsed reset, paused interval,
                # or a catch-up jump after buffering as time actually observed.
                if (0 < wall_delta <= self.MAX_SAMPLE_GAP
                        and 0 < playback_delta <= wall_delta + 1.0):
                    self.seconds += min(wall_delta, playback_delta)
            self.anchor = (now, elapsed)
        else:
            self.suspend()
        if self.current is not None:
            self.current["album"] = self._text(song.get("album"))
            self.current["station"]["name"] = self._text(station.get("name"))
        if self.seconds > self.MINIMUM_SECONDS and not self.checkpointed:
            self.checkpointed = True
            self.dirty = True
        self._save()

    def _save(self):
        if not self.dirty or self.clock() < self.retry_at:
            return
        if self.path is None:
            self.dirty = False
            return
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            checkpoint = (dict(self.current, playedSeconds=self.seconds)
                          if self.current and self.seconds > self.MINIMUM_SECONDS else None)
            data = {"version": 1, "lastPlayed": self.previous, "qualifiedCurrent": checkpoint}
            with tempfile.NamedTemporaryFile("w", dir=self.path.parent, prefix=".radio-history-",
                                             encoding="utf-8", delete=False) as target:
                temporary = target.name
                json.dump(data, target, ensure_ascii=False)
                target.write("\n")
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.path)
            temporary = None
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            self.dirty, self.error, self.retry_at = False, None, 0.0
        except OSError as exc:
            self.error = f"Could not save radio history: {exc}"
            self.retry_at = self.clock() + 5.0
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def snapshot(self):
        return {"lastPlayed": copy.deepcopy(self.previous), "persistenceError": self.error}
