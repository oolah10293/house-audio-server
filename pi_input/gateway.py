#!/usr/bin/env python3
"""House Audio input gateway. Source registry and transparent WAV stream proxy.

No commands to MPD or Snapserver are issued: the established House Music
server retains sole control of the house playback session.
"""
from __future__ import annotations

import argparse
import http.client
import html
import ipaddress
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ID_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$')
TRACK_RE = re.compile(r'^/audio/([A-Za-z0-9_-]{1,64})/([0-9]{1,3})\.wav$')
ALIVE_SECONDS = 18
LOG = logging.getLogger('house_audio.cd_gateway')


@dataclass
class Source:
    sourceId: str
    name: str
    kind: str
    ip: str
    port: int
    tracks: list
    last_seen: float
    status: str = 'ready'
    bootSession: str = ''
    discRevision: int = 0

    def view(self, now: float) -> dict:
        return dict(sourceId=self.sourceId, name=self.name, kind=self.kind,
                    ip=self.ip, tracks=self.tracks, status=self.status,
                    available=(now-self.last_seen) < ALIVE_SECONDS,
                    bootSession=self.bootSession, discRevision=self.discRevision)


class Registry:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.sources = {}

    def register(self, data: dict, ip: str):
        if not isinstance(data, dict):
            raise ValueError('Expected JSON object')
        sid = data.get('sourceId')
        if not isinstance(sid, str) or not ID_RE.fullmatch(sid):
            raise ValueError('Invalid sourceId')
        name = data.get('name')
        kind = data.get('kind')
        status = data.get('status', 'ready')
        port = data.get('port', 8080)
        tracks = data.get('tracks', [])
        boot_session = data.get('bootSession', '')
        disc_revision = data.get('discRevision', 0)
        if (not isinstance(boot_session, str) or
                (boot_session and not re.fullmatch(r'[0-9a-fA-F]{16}', boot_session))):
            raise ValueError('Invalid bootSession')
        if type(disc_revision) is not int or not 0 <= disc_revision <= 0xffffffff:
            raise ValueError('Invalid discRevision')
        if (boot_session and disc_revision == 0 and tracks):
            raise ValueError('Ready tracks need a positive discRevision')
        if (not boot_session and disc_revision):
            raise ValueError('Disc revision requires a boot session')
        if not isinstance(name, str) or not 1 <= len(name) <= 80:
            raise ValueError('Invalid name')
        if not isinstance(kind, str) or kind not in ('cd', 'line-in', 'other'):
            raise ValueError('Invalid kind')
        if status not in ('ready', 'no_disc', 'playing', 'error'):
            raise ValueError('Invalid status')
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('Invalid port')
        if not isinstance(tracks, list) or len(tracks) > 99 or any(type(t) is not int or not 1 <= t <= 99 for t in tracks) or len(set(tracks)) != len(tracks):
            raise ValueError('Invalid tracks')
        address = ipaddress.ip_address(ip)
        if not address.is_private or address.is_loopback or address.is_link_local:
            raise ValueError('Source must be on trusted private LAN')
        with self.lock:
            if len(self.sources) >= 32 and sid not in self.sources:
                raise ValueError('Source registry full')
            self.sources[sid] = Source(sid, name, kind, str(address), port,
                                       tracks, self.clock(), status,
                                       boot_session.lower(), disc_revision)

    def list(self):
        with self.lock:
            now = self.clock()
            return [self.sources[k].view(now) for k in sorted(self.sources)]

    def get_track(self, sid: str, number: int) -> Source:
        with self.lock:
            src = self.sources.get(sid)
            if not src or self.clock()-src.last_seen >= ALIVE_SECONDS or number not in src.tracks:
                raise KeyError('Source not present or track unavailable')
            return Source(**asdict(src))


class GatewayHandler(BaseHTTPRequestHandler):
    registry: Registry
    protocol_version = 'HTTP/1.1'

    def send_bytes(self, data: bytes, mime='application/json', code=200):
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def do_POST(self):
        if self.path != '/register':
            return self.send_bytes(b'Not found', 'text/plain', 404)
        try:
            length = int(self.headers.get('Content-Length', '-1'))
            if length < 2 or length > 8192:
                raise ValueError('Invalid request size')
            data = json.loads(self.rfile.read(length))
            self.registry.register(data, self.client_address[0])
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return self.send_bytes(json.dumps({'error': str(exc)}).encode(), code=400)
        self.send_bytes(b'{"ok":true}')

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        target = urlsplit(self.path).path
        if target == '/sources':
            return self.send_bytes(json.dumps({'sources': self.registry.list()}).encode())
        if target == '/':
            rows = []
            for source in self.registry.list():
                name = html.escape(source['name'])
                kind = html.escape(source['kind'])
                status = 'connected' if source['available'] else 'offline'
                audio = ''.join(f'<a href="/audio/{source["sourceId"]}/{t}.wav">Track {t}</a> ' for t in source['tracks'])
                rows.append(f'<li><b>{name}</b> ({kind}, {status}): {audio}</li>')
            page = ('<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1">'
                    '<meta http-equiv="refresh" content="5"><title>House Music inputs</title>'
                    '<h2>House Music input sources</h2><ul>' + ''.join(rows) + '</ul>'
                    '<p>This page only lists sources. It does not change the house session.</p>')
            return self.send_bytes(page.encode(), 'text/html; charset=utf-8')
        m = TRACK_RE.fullmatch(target)
        if m:
            try:
                source = self.registry.get_track(m.group(1), int(m.group(2)))
            except KeyError:
                return self.send_bytes(b'Source offline or track unavailable', 'text/plain', 404)
            return self.proxy_track(source, int(m.group(2)))
        self.send_bytes(b'Not found', 'text/plain', 404)

    def proxy_track(self, source: Source, number: int):
        connection = http.client.HTTPConnection(source.ip, source.port, timeout=8)
        headers_sent = False
        remaining = None
        # An upstream READ CD failure can truncate a fixed-length WAV. Never
        # keep the MPD connection alive after advertising that Content-Length:
        # it could otherwise wait indefinitely for bytes that will never come.
        self.close_connection = True
        try:
            connection.request('GET', f'/track/{number}.wav', headers={'Connection': 'close'})
            upstream = connection.getresponse()
            if upstream.status != 200:
                return self.send_bytes(f'Input node rejected track: HTTP {upstream.status}'.encode(), 'text/plain', 502)
            total = upstream.getheader('Content-Length')
            if total is None or not total.isdecimal() or not 44 <= int(total) <= 3_000_000_000:
                return self.send_bytes(b'Invalid WAV size from input node', 'text/plain', 502)
            self.send_response(200)
            self.send_header('Content-Type', 'audio/wav')
            self.send_header('Content-Length', total)
            self.send_header('Connection', 'close')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            headers_sent = True
            if self.command == 'GET':
                remaining = int(total)
                while remaining > 0:
                    # read1 returns available data without waiting to fill a
                    # 16 KiB buffer; preserves partial audio before abrupt EOF.
                    chunk = upstream.read1(min(16384, remaining))
                    if not chunk:
                        LOG.warning('CD stream ended early: source=%s track=%d missing=%d bytes',
                                    source.sourceId, number, remaining)
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (OSError, http.client.HTTPException, ValueError) as exc:
            # Once 200 + Content-Length are sent, we cannot send a new 502.
            # Closing the socket is the only valid way to signal truncation.
            LOG.warning('CD stream failed: source=%s track=%d missing=%s error=%s',
                        source.sourceId, number, remaining, exc)
            if not headers_sent:
                try:
                    self.send_bytes(f'Source unavailable: {exc}'.encode(), 'text/plain', 502)
                except OSError:
                    pass
        finally:
            connection.close()
            # BaseHTTPRequestHandler will close the response socket after this
            # handler returns. No next HTTP request may reuse a truncated WAV.
            self.close_connection = True


def make_server(host='127.0.0.1', port=8768, registry=None):
    class BoundHandler(GatewayHandler):
        pass
    BoundHandler.registry = registry if registry is not None else Registry()
    return ThreadingHTTPServer((host, port), BoundHandler)


def main():
    p = argparse.ArgumentParser(description='House Music source registry and audio proxy')
    p.add_argument('--host', default='0.0.0.0')
    p.add_argument('--port', default=8768, type=int)
    args = p.parse_args()
    with make_server(args.host, args.port) as server:
        print(f'House audio inputs: http://{args.host}:{server.server_port}', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
