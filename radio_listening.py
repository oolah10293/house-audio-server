"""Bounded observation of station listening time; no playback intent or audio."""
import json
import math
import time
from pathlib import Path

from state_file import save_json


class RadioListening:
    SAVE_SECONDS = 30.0
    MAX_SAMPLE_GAP = 3.0

    def __init__(self, path=None, clock=time.monotonic, wall_clock=time.time):
        self.path = Path(path) if path else None
        self.clock = clock
        self.started = wall_clock()
        self.totals = {}
        self.anchor = None
        self.dirty = False
        self.error = None
        self.read_only = False
        self.next_save = clock() + self.SAVE_SECONDS
        if self.path is not None:
            try:
                raw = self.path.read_text(encoding='utf-8')
                if len(raw) > 1048576:
                    raise ValueError('Listening totals too large')
                data = json.loads(raw)
                totals = data.get('totals') if isinstance(data, dict) else None
                if (not isinstance(data, dict) or data.get('version') != 1
                        or not isinstance(totals, dict) or len(totals) > 256
                        or not self._number(data.get('trackingStartedAtEpoch'))
                        or any(not isinstance(k, str) or not self._number(v) for k, v in totals.items())):
                    raise ValueError('Invalid listening totals')
                self.totals, self.started = totals, data['trackingStartedAtEpoch']
            except FileNotFoundError:
                pass
            except (OSError, ValueError, TypeError) as exc:
                # Playback must survive counter errors; do not overwrite evidence.
                self.error, self.read_only = str(exc), True

    @staticmethod
    def _number(value):
        try:
            return type(value) in (int, float) and math.isfinite(value) and value >= 0
        except OverflowError:
            return False

    def suspend(self, flush=False):
        self.anchor = None
        self.save(force=flush)

    def remove(self, station_id):
        if station_id in self.totals:
            del self.totals[station_id]
            self.dirty = True
        self.suspend(flush=True)

    def observe(self, station, state, audible, intent='play'):
        now = self.clock()
        elapsed = state.get('elapsedSeconds')
        if (station is None or not audible or intent != 'play' or state.get('transport') != 'play'
                or state.get('error') or state.get('volume') == 0 or not self._number(elapsed)):
            self.suspend()
            return
        identity = (station['id'], state.get('songId'), state.get('queueVersion'))
        if self.anchor is not None:
            old_identity, old_time, old_elapsed = self.anchor
            dt, played = now - old_time, elapsed - old_elapsed
            if old_identity == identity and 0 < dt <= self.MAX_SAMPLE_GAP and 0 < played <= dt + 1:
                self.totals[station['id']] = self.totals.get(station['id'], 0) + min(dt, played)
                self.dirty = True
        self.anchor = (identity, now, elapsed)
        self.save()

    def save(self, force=False):
        if self.read_only or not self.dirty or (not force and self.clock() < self.next_save):
            return
        try:
            save_json(self.path, {'version': 1, 'trackingStartedAtEpoch': self.started,
                                 'totals': self.totals})
            self.dirty, self.error = False, None
        except OSError as exc:
            self.error = str(exc)
        self.next_save = self.clock() + self.SAVE_SECONDS

    def rank(self, stations):
        rows = [dict(s, listeningSeconds=round(self.totals.get(s['id'], 0), 3)) for s in stations]
        return sorted(rows, key=lambda s: (-s['listeningSeconds'], s['name'].casefold(), s['id']))

    def snapshot(self):
        return {'trackingStartedAtEpoch': self.started, 'saveIntervalSeconds': self.SAVE_SECONDS,
                'persistenceError': self.error, 'readOnly': self.read_only}
