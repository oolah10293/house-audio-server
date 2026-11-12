import http.client
import json
import struct
import pathlib
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'pi_input'))
from gateway import Registry, make_server


class StubNode(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != '/track/1.wav':
            self.send_error(404)
            return
        data = b'\x55' * 4096
        body = struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', 36 + len(data), b'WAVE', b'fmt ', 16, 1, 2, 44100, 176400, 4, 16, b'data', len(data)) + data
        self.send_response(200)
        self.send_header('Content-Type', 'audio/wav')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = ThreadingHTTPServer(('127.0.0.1', 0), StubNode)
        cls.server = make_server('127.0.0.1', 0)
        for srv in (cls.node, cls.server):
            threading.Thread(target=srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        for srv in (cls.node, cls.server):
            srv.shutdown()
            srv.server_close()

    def request(self, method, path, data=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
        conn.request(method, path, body=json.dumps(data).encode() if data is not None else None,
                     headers={'Content-Type':'application/json'} if data is not None else {})
        response = conn.getresponse()
        out = response.status, response.read()
        conn.close()
        return out

    def test_registry_bad_data(self):
        r = Registry()
        with self.assertRaises(ValueError):
            r.register({'sourceId':'../bad', 'name':'test', 'kind':'cd'}, '192.168.1.20')
        with self.assertRaises(ValueError):
            r.register({'sourceId':'x', 'name':'ok', 'kind':'cd', 'tracks':[1]}, '127.0.0.1')
        with self.assertRaises(ValueError):
            r.register({'sourceId':'x', 'name':'ok', 'kind':'cd', 'tracks':[True]}, '192.168.1.20')

    def test_registry_heartbeat_expiry(self):
        clock = [100.0]
        r = Registry(clock=lambda: clock[0])
        r.register({'sourceId':'cd1','name':'CD','kind':'cd','tracks':[1]}, '192.168.1.20')
        self.assertTrue(r.get_track('cd1', 1))
        clock[0] += 18
        with self.assertRaises(KeyError):
            r.get_track('cd1', 1)
        self.assertFalse(r.list()[0]['available'])

    def test_api_list_and_refuse_unregistered(self):
        code, body = self.request('GET', '/sources')
        self.assertEqual(code, 200)
        self.assertIn('sources', json.loads(body))
        code, body = self.request('GET','/audio/unknown/1.wav')
        self.assertEqual(code,404)

    def test_proxy_wav(self):
        # Register as a LAN address, then override the runtime stub for local-only test.
        self.server.RequestHandlerClass.registry.register({
            'sourceId':'cdtest','name':'CD test','kind':'cd','port':self.node.server_port,
            'tracks':[1,2]}, '192.168.1.20')
        with self.server.RequestHandlerClass.registry.lock:
            self.server.RequestHandlerClass.registry.sources['cdtest'].ip = '127.0.0.1'
        code, body = self.request('GET','/audio/cdtest/1.wav')
        self.assertEqual(code, 200)
        self.assertEqual(len(body), 4140)
        self.assertTrue(body.startswith(b'RIFF'))
        code, body = self.request('HEAD','/audio/cdtest/1.wav')
        self.assertEqual(code, 200)
        self.assertEqual(body, b'')
        code, _ = self.request('GET','/audio/cdtest/3.wav')
        self.assertEqual(code, 404)

    def test_register_http(self):
        code, body = self.request('POST','/register', {'sourceId':'x','name':'CD', 'kind':'cd','tracks':[1]})
        self.assertEqual(code,400)  # loopback refused in production


if __name__ == '__main__':
    unittest.main()
