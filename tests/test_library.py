import http.client
import json
import threading
import unittest
from unittest.mock import Mock, patch
import house_audio_server as h


class LibraryTests(unittest.TestCase):
    def test_incremental_update_only_and_status(self):
        mpd = h.MpdClient()
        with patch.object(mpd, 'run', return_value=('0.24.0', [h.MpdResponse(['updating_db: 7'])])) as run:
            self.assertEqual(mpd.update_library(), {'updating': True, 'jobId': 7})
            run.assert_called_once_with('update')
        with patch.object(mpd, 'run', return_value=('0.24.0', [h.MpdResponse(['state: play'])])) as run:
            self.assertEqual(mpd.library_status(), {'updating': False, 'jobId': None})
            run.assert_called_once_with('status')

    def test_multiple_phones_coalesce_active_scan_and_cooldown(self):
        clock = Mock(return_value=0)
        updates = h.LibraryUpdates(clock)
        mpd = Mock()
        mpd.library_status.return_value = {'updating': False, 'jobId': None}
        mpd.update_library.return_value = {'updating': True, 'jobId': 7}
        self.assertEqual(updates.request(mpd)['jobId'], 7)
        updates.request(mpd)
        mpd.update_library.assert_called_once()
        clock.return_value = 61
        updates.request(mpd)
        self.assertEqual(mpd.update_library.call_count, 2)
        updates.request(mpd, force=True)
        self.assertEqual(mpd.update_library.call_count, 3)
        mpd.library_status.return_value = {'updating': True, 'jobId': 8}
        self.assertEqual(updates.request(mpd, force=True)['jobId'], 8)
        self.assertEqual(mpd.update_library.call_count, 3)
        mpd.command.assert_not_called()
        mpd.replace_queue.assert_not_called()

    def test_failed_update_does_not_suppress_next_attempt(self):
        updates = h.LibraryUpdates()
        mpd = Mock()
        mpd.library_status.return_value = {'updating': False}
        mpd.update_library.side_effect = [OSError('offline'), {'updating': True, 'jobId': 2}]
        with self.assertRaises(OSError):
            updates.request(mpd)
        self.assertEqual(updates.request(mpd)['jobId'], 2)


class LibraryHttpTests(unittest.TestCase):
    def setUp(self):
        self.mpd = Mock()
        self.mpd.library_status.return_value = {'updating': False, 'jobId': None}
        self.mpd.update_library.return_value = {'updating': True, 'jobId': 7}
        self.mpd.browse.return_value = []
        for name, value in [('MpdClient', Mock(return_value=self.mpd)), ('LIBRARY_UPDATES', h.LibraryUpdates())]:
            p = patch.object(h, name, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(h.ApiHandler, 'log_message');p.start();self.addCleanup(p.stop)
        self.server = h.ThreadingHTTPServer(('127.0.0.1', 0), h.ApiHandler)
        t = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01}); t.start()
        self.addCleanup(t.join);self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)

    def request(self, method, path, payload=None):
        c = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        try:
            c.request(method, path, body=json.dumps(payload) if payload is not None else None,
                      headers={'Content-Type': 'application/json'})
            r = c.getresponse();return r.status, json.loads(r.read())
        finally:
            c.close()

    def test_refresh_then_browse_exposes_new_index_without_transport(self):
        self.assertEqual(self.request('POST', '/library/update', {})[1]['library']['jobId'], 7)
        self.mpd.browse.return_value = [{'type': 'file', 'path': 'MP3s/new.mp3', 'name': 'new.mp3'}]
        status, body = self.request('GET', '/browse?path=MP3s')
        self.assertEqual(status, 200)
        self.assertEqual(body['entries'][0]['name'], 'new.mp3')
        self.assertFalse(body['library']['updating'])
        self.mpd.command.assert_not_called()
        self.mpd.replace_queue.assert_not_called()

    def test_bad_payload_rejected_before_scan(self):
        for body in ({'force': 1}, {'force': 'true'}, {'play': True}, {'path': '../'}):
            self.assertEqual(self.request('POST', '/library/update', body)[0], 400)
        self.mpd.update_library.assert_not_called()

    def test_failed_scan_reports_503_and_recovers(self):
        self.mpd.update_library.side_effect = [h.MpdError('unavailable'), {'updating': True, 'jobId': 9}]
        self.assertEqual(self.request('POST', '/library/update', {})[0], 503)
        self.assertEqual(self.request('POST', '/library/update', {})[1]['library']['jobId'], 9)
