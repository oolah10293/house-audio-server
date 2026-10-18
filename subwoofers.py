"""Persisted dependent-renderer assignments; demand and feedback are separate.

Never sends hardware commands. The sub owns its manual mode, actuator and timer.
"""
import copy
import json
import math
import re
import secrets
import threading
import time
from pathlib import Path

from state_file import save_json


class SubwooferError(RuntimeError):
    def __init__(self, status, code, detail):
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail


def identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value)


class Subwoofers:
    REPORT_TTL = 15.0
    STANDBY_SECONDS = 30
    POLL_SECONDS = 2

    def __init__(self, path=None, clock=time.monotonic):
        self.path = Path(path) if path else None
        self.clock = clock
        self._lock = threading.RLock()
        self._configs = []
        self._reports = {}
        if self.path is not None:
            try:
                raw = self.path.read_text(encoding='utf-8')
            except FileNotFoundError:
                return
            if len(raw) > 262144:
                raise ValueError('Subwoofer settings too large')
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get('version') != 1:
                raise ValueError('Invalid subwoofer settings version')
            configs = data.get('subwoofers')
            self._validate_all(configs)
            self._configs = configs

    @staticmethod
    def _invalid(detail, status=400):
        raise SubwooferError(status, 'invalid_subwoofer', detail)

    @classmethod
    def _validate_all(cls, configs):
        if not isinstance(configs, list) or len(configs) > 32:
            cls._invalid('At most 32 subwoofers are supported')
        ids, renderers = set(), set()
        for item in configs:
            if not isinstance(item, dict) or set(item) != {'id', 'name', 'rendererId', 'parentRendererIds'}:
                cls._invalid('Provide name, rendererId and parentRendererIds')
            if not identifier(item['id']) or not identifier(item['rendererId']):
                cls._invalid('Invalid subwoofer or renderer ID')
            name = item['name']
            if (not isinstance(name, str) or not name.strip() or len(name) > 120
                    or any(ord(c) < 32 or ord(c) == 127 for c in name)):
                cls._invalid('Name must contain 1–120 printable characters')
            parents = item['parentRendererIds']
            if (not isinstance(parents, list) or not 1 <= len(parents) <= 256
                    or not all(identifier(p) for p in parents) or len(set(parents)) != len(parents)):
                cls._invalid('Select one or more distinct normal speakers')
            if item['id'] in ids or item['rendererId'] in renderers:
                cls._invalid('Subwoofer IDs and renderer IDs must be unique', 409)
            ids.add(item['id'])
            renderers.add(item['rendererId'])
        if any(set(item['parentRendererIds']) & renderers for item in configs):
            cls._invalid('A subwoofer cannot activate itself or another subwoofer', 409)

    def renderer_ids(self):
        with self._lock:
            return {item['rendererId'] for item in self._configs}

    def _get(self, sub_id):
        for config in self._configs:
            if config['id'] == sub_id:
                return config
        raise SubwooferError(404, 'subwoofer_not_found', 'Unknown subwoofer ID')

    def save(self, payload, snap):
        with self._lock:
            if set(payload) - {'id', 'name', 'rendererId', 'parentRendererIds'}:
                self._invalid('Unknown configuration field')
            sub_id = payload.get('id')
            old = self._get(sub_id) if sub_id is not None else None
            candidate = dict(payload, id=old['id'] if old else 'sub-' + secrets.token_hex(8))
            updated = [candidate if s['id'] == sub_id else s for s in self._configs]
            if old is None:
                updated.append(candidate)
            self._validate_all(updated)
            if old and candidate['rendererId'] != old['rendererId']:
                self._invalid('Renderer identity is fixed after registration')
            known = {c.get('id') for c in snap.get('clients', [])}
            # Existing assignments remain editable when Snapserver is offline.
            if old:
                known.update([old['rendererId'], *old['parentRendererIds']])
            required = {candidate['rendererId'], *candidate['parentRendererIds']}
            if not required <= known:
                self._invalid('Use renderer IDs discovered by Snapserver; power on missing nodes first')
            candidate = dict(candidate, name=candidate['name'].strip())
            updated = [candidate if s['id'] == candidate['id'] else s for s in updated]
            try:
                save_json(self.path, {'version': 1, 'subwoofers': updated},
                          lambda: setattr(self, '_configs', updated))
            except OSError as exc:
                raise SubwooferError(503, 'subwoofer_write_failed',
                                     'Could not confirm settings save; reload before retrying') from exc
            return copy.deepcopy(candidate)

    def demand(self, sub_id, presence):
        with self._lock:
            config = self._get(sub_id)
            known = bool(presence.get('snapserverReachable'))
            eligible = {r['rendererId'] for r in presence.get('renderers', [])
                        if not r.get('dependent') and r.get('eligibleForSubwoofer')}
            matches = sorted(set(config['parentRendererIds']) & eligible) if known else []
            return {'id': sub_id, 'rendererId': config['rendererId'], 'known': known,
                    'needed': bool(matches) if known else None,
                    'matchingRendererIds': matches, 'standbyDelaySeconds': self.STANDBY_SECONDS,
                    'pollSeconds': self.POLL_SECONDS, 'validForSeconds': 10,
                    'reason': ('assigned_speaker_present' if matches else 'no_assigned_speakers')
                              if known else 'snapserver_unavailable'}

    def report(self, sub_id, payload):
        with self._lock:
            config = self._get(sub_id)
            allowed = {'rendererId', 'mode', 'state', 'standbyRemainingSeconds', 'acknowledged', 'detail'}
            if set(payload) - allowed or not {'rendererId', 'mode', 'state'} <= set(payload):
                self._invalid('Status requires rendererId, mode and state')
            if payload['rendererId'] != config['rendererId']:
                self._invalid('Status renderer does not match registration', 409)
            if payload['mode'] not in ('auto', 'force_standby', 'force_awake'):
                self._invalid('Invalid mode')
            if payload['state'] not in ('awake', 'standby', 'standby_pending', 'waking', 'unknown', 'error'):
                self._invalid('Invalid state')
            remaining = payload.get('standbyRemainingSeconds')
            if payload['state'] == 'standby_pending':
                if (payload['mode'] != 'auto' or type(remaining) not in (int, float)
                        or not 0 <= remaining <= self.STANDBY_SECONDS or not math.isfinite(remaining)):
                    self._invalid('Pending standby needs auto mode and a 0–30 second countdown')
            elif remaining is not None:
                self._invalid('Countdown is only valid for pending standby')
            ack = payload.get('acknowledged', False)
            if type(ack) is not bool or (ack and payload['state'] not in ('awake', 'standby')):
                self._invalid('Acknowledgment is only valid for awake or standby')
            detail = payload.get('detail', '')
            if not isinstance(detail, str) or len(detail) > 240 or any(ord(c) < 32 for c in detail):
                self._invalid('Detail must be at most 240 printable characters')
            self._reports[sub_id] = (self.clock(), dict(payload, acknowledged=ack, detail=detail))
            return self._status(sub_id)

    def _status(self, sub_id):
        report = self._reports.get(sub_id)
        age = max(0, self.clock() - report[0]) if report else None
        if report is None or age >= self.REPORT_TTL:
            return {'fresh': False, 'ageSeconds': round(age, 1) if age is not None else None,
                    'mode': None, 'state': 'unknown', 'acknowledged': False,
                    'standbyRemainingSeconds': None,
                    'detail': 'No status report yet' if report is None else 'Subwoofer is not responding'}
        result = dict(report[1], fresh=True, ageSeconds=round(age, 1))
        if result['state'] == 'standby_pending':
            result['standbyRemainingSeconds'] = round(max(0, result['standbyRemainingSeconds'] - age), 1)
        return result

    def snapshot(self, snap, presence):
        with self._lock:
            roles = {r['rendererId']: r for r in presence.get('renderers', [])}
            catalog = {c['id']: {'id': c['id'], 'name': c.get('name') or c['id'],
                       'present': bool(c.get('present')) if snap.get('reachable') else None,
                       'audible': roles.get(c['id'], {}).get('audible', False),
                       'dependent': c['id'] in self.renderer_ids()}
                       for c in snap.get('clients', []) if identifier(c.get('id'))}
            for config in self._configs:
                for rid in [config['rendererId'], *config['parentRendererIds']]:
                    catalog.setdefault(rid, {'id': rid, 'name': rid, 'present': None,
                                            'audible': False, 'dependent': rid in self.renderer_ids()})
            return {'subwoofers': [dict(copy.deepcopy(c), demand=self.demand(c['id'], presence),
                                        status=self._status(c['id'])) for c in self._configs],
                    'renderers': sorted(catalog.values(), key=lambda c: (c['name'].casefold(), c['id'])),
                    'snapserverReachable': bool(snap.get('reachable')),
                    'statusStaleAfterSeconds': self.REPORT_TTL}
