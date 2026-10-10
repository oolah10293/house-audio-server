import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import radio_stations as r


class StreamUrlTests(unittest.TestCase):
    def test_canonical_host_preserves_signed_path_query_and_escapes(self):
        self.assertEqual(r.validate_stream_url("  HTTPS://Stream.Example.COM/Rock%2fLive?Token=AbC%2F9  "),
                         "https://stream.example.com/Rock%2fLive?Token=AbC%2F9")
        self.assertEqual(r.validate_stream_url("http://pihole:8000"), "http://pihole:8000/")
        self.assertEqual(r.validate_stream_url("http://[::1]:8000/live"), "http://[::1]:8000/live")
        self.assertEqual(r.validate_stream_url("https://bücher.example/stream"),
                         "https://xn--bcher-kva.example/stream")

    def test_invalid_urls_cannot_become_network_or_mpd_commands(self):
        invalid = [None, True, [], "", "file:///tmp/music", "ftp://host/a", "https:///a",
                   "https://user:password@host/live", "https://user@host/live", "https://@host/live",
                   "https://host/live#anything", "https://host/live#", "https://host:0/live",
                   "https://host:/live", "https://host:65536/live", "https://host:abc/live",
                   "https://host\\path/live", "https://bad_host/live", "https://bad..host/live",
                   "https://host../live",
                   "https://-bad.example/live", "https://999.1.1.1/live", "http://[::1]suffix/live",
                   "http://[fe80::1%25eth0]/live", "https://host/%Q0", "https://host/%",
                   "https://host/a\nb", "https://host/a\rb", "https://host/a\tb", "\nhttps://host/a",
                   "https://host/live\x00", "https://host/a b", "https://host/a\u200bb",
                   'https://host/a"b', "https://host/" + "a" * r.MAX_URL_LENGTH]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(r.RadioError) as caught:
                r.validate_stream_url(value)
            self.assertEqual(caught.exception.code, "invalid_stream_url")
            self.assertEqual(caught.exception.status, 400)

    def test_fallback_never_uses_private_query_or_song_metadata(self):
        self.assertEqual(r.fallback_station_name("https://www.example.com/rock-mix.mp3?token=secret"),
                         "example.com / rock mix")
        self.assertEqual(r.fallback_station_name("http://pihole:8000/"), "pihole")
        self.assertNotIn("\n", r.fallback_station_name("https://example.com/rock%0amix"))


class StationStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "stations.json"
        self.store = r.StationStore(self.path)

    def test_roundtrip_add_rename_delete_and_copy_isolation(self):
        self.assertFalse(self.path.exists())
        station, created = self.store.add("https://example.com/rock", generated_name="Rock Station")
        self.assertTrue(created)
        self.assertEqual(station["nameSource"], "stream")
        self.assertEqual(r.StationStore(self.path).get(station["id"]), station)
        station["name"] = "caller edit"
        self.store.list()[0]["name"] = "another caller edit"
        saved = self.store.get(station["id"])
        self.assertEqual(saved["name"], "Rock Station")
        renamed = self.store.rename(station["id"], "  My Favorite  ")
        self.assertEqual(renamed["name"], "My Favorite")
        self.assertEqual(renamed["nameSource"], "user")
        self.assertEqual(self.store.delete(station["id"]), renamed)
        self.assertEqual(r.StationStore(self.path).list(), [])
        for operation in (self.store.get, self.store.delete):
            with self.assertRaises(r.RadioError) as caught:
                operation(station["id"])
            self.assertEqual(caught.exception.status, 404)

    def test_duplicate_url_returns_existing_and_does_not_rename(self):
        station, _ = self.store.add("HTTPS://EXAMPLE.COM/Rock?token=AbC", name="Mine")
        before = self.path.read_bytes()
        duplicate, created = self.store.add("https://example.com/Rock?token=AbC",
                                            generated_name="Changed station")
        self.assertFalse(created)
        self.assertEqual(duplicate, station)
        self.assertEqual(self.path.read_bytes(), before)
        distinct, created = self.store.add("https://example.com/rock?token=AbC")
        self.assertTrue(created)
        self.assertNotEqual(distinct["id"], station["id"])

    def test_generated_name_can_improve_fallback_but_never_replace_user_choice(self):
        station, _ = self.store.add("https://example.com/rock")
        self.assertEqual(station["nameSource"], "fallback")
        updated = self.store.update_generated_name(station["id"], "Live Rock")
        self.assertEqual(updated["name"], "Live Rock")
        self.assertEqual(updated["nameSource"], "stream")
        self.store.rename(station["id"], "My rock")
        before = self.path.read_bytes()
        updated = self.store.update_generated_name(station["id"], "New Stream Name")
        self.assertEqual(updated["name"], "My rock")
        self.assertEqual(self.path.read_bytes(), before)

    def test_blank_stream_names_fall_back_and_hostile_metadata_is_cleaned(self):
        for number, generated in enumerate((None, "", " \t\n ")):
            station, _ = self.store.add(f"https://example.com/{number}", generated_name=generated)
            self.assertEqual(station["nameSource"], "fallback")
        station, _ = self.store.add("https://example.com/live", generated_name="Cool\r\nStation")
        self.assertEqual(station["name"], "Cool Station")

    def test_invalid_user_names_and_urls_leave_disk_unchanged(self):
        station, _ = self.store.add("https://example.com/rock")
        before = self.path.read_bytes()
        for name in (None, True, 1, [], {}, "", "  ", "Line\nbreak", "\tName", "x" * 121):
            with self.subTest(name=name), self.assertRaises(r.RadioError):
                self.store.rename(station["id"], name)
            self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaises(r.RadioError):
            self.store.add("file:///etc/passwd")
        self.assertEqual(self.path.read_bytes(), before)

    def test_malformed_ids_are_bad_requests_and_unknown_valid_ids_are_not_found(self):
        self.store.add("https://example.com/rock")
        before = self.path.read_bytes()
        for station_id in (None, [], {}, True, 23, "", "a" * 129, "a\nb", "a b"):
            for operation in (self.store.get, self.store.delete,
                              lambda value: self.store.rename(value, "New Name"),
                              lambda value: self.store.update_generated_name(value, "Metadata")):
                with self.subTest(station_id=station_id), self.assertRaises(r.RadioError) as caught:
                    operation(station_id)
                self.assertEqual(caught.exception.status, 400)
                self.assertEqual(caught.exception.code, "invalid_station_id")
        with self.assertRaises(r.RadioError) as caught:
            self.store.get("unknown-but-valid-id")
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(self.path.read_bytes(), before)

    def test_malformed_saved_file_is_preserved_and_never_reset(self):
        self.store.add("https://example.com/rock")
        valid = json.loads(self.path.read_text())
        invalid_files = ["broken", "[]", "{}", '{"version":2,"stations":[]}',
                         json.dumps({"version": 1, "stations": [valid["stations"][0]] * 2}),
                         json.dumps({"version": 1, "stations": [{"id": "broken"}]}),
                         json.dumps({"version": 1, "stations": [dict(valid["stations"][0], name="")]}),
                         json.dumps({"version": 1, "stations": [dict(valid["stations"][0], url="file:///bad")]})]
        for content in invalid_files:
            self.path.write_text(content)
            with self.subTest(content=content), self.assertRaises(r.RadioError) as caught:
                r.StationStore(self.path)
            self.assertEqual(caught.exception.code, "station_storage_invalid")
            self.assertEqual(self.path.read_text(), content)
        with patch.object(Path, "open", side_effect=PermissionError("unreadable")):
            with self.assertRaises(PermissionError):
                r.StationStore(self.path)

    def test_replace_failure_preserves_memory_disk_and_cleans_temporary(self):
        original, _ = self.store.add("https://example.com/rock")
        with patch.object(r.os, "replace", side_effect=OSError("disk failure")):
            with self.assertRaises(r.RadioError) as caught:
                self.store.rename(original["id"], "Changed")
        self.assertEqual(caught.exception.code, "station_write_failed")
        self.assertEqual(self.store.list(), [original])
        self.assertEqual(r.StationStore(self.path).list(), [original])
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_directory_sync_failure_keeps_committed_memory_and_disk_consistent(self):
        original, _ = self.store.add("https://example.com/rock")
        with patch.object(r.os, "fsync", side_effect=[None, OSError("directory sync failed")]):
            with self.assertRaises(r.RadioError) as caught:
                self.store.rename(original["id"], "Committed")
        self.assertEqual(caught.exception.status, 503)
        self.assertEqual(self.store.get(original["id"])["name"], "Committed")
        self.assertEqual(r.StationStore(self.path).list(), self.store.list())

    def test_concurrent_duplicate_add_creates_one_durable_station(self):
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(lambda _: self.store.add("https://example.com/rock"), range(12)))
        self.assertEqual(sum(created for _, created in results), 1)
        self.assertEqual(len({station["id"] for station, _ in results}), 1)
        self.assertEqual(r.StationStore(self.path).list(), self.store.list())

    def test_station_limit_preserves_existing_and_still_allows_duplicate(self):
        with patch.object(r, "MAX_STATIONS", 2):
            first, _ = self.store.add("https://example.com/one")
            self.store.add("https://example.com/two")
            with self.assertRaises(r.RadioError) as caught:
                self.store.add("https://example.com/three")
            self.assertEqual(caught.exception.code, "station_limit")
            self.assertEqual(self.store.add(first["url"]), (first, False))


class ProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                if self.path == "/slow":
                    time.sleep(0.2)
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/audio")
                    # A response with an undrained/incomplete body must not
                    # prevent proceeding to the audio endpoint.
                    self.send_header("Content-Length", "1000000")
                    self.end_headers()
                    return
                if self.path == "/redirect-host":
                    self.send_response(302)
                    self.send_header("Location", f"http://localhost:{self.server.server_port}/host")
                    self.end_headers()
                    return
                if self.path.startswith("/redirect-"):
                    self.send_response(302)
                    self.send_header("Location", {
                        "/redirect-file": "file:///etc/passwd",
                        "/redirect-credentials": "http://secret:password@127.0.0.1/audio",
                        "/redirect-loop": "/redirect-loop",
                        "/redirect-fragment": "/audio#fragment",
                    }[self.path])
                    self.end_headers()
                    return
                if self.path == "/missing":
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", {
                    "/html": "text/html", "/pls": "audio/x-scpls", "/m3u": "audio/x-mpegurl",
                    "/hls": "application/vnd.apple.mpegurl", "/unknown": "application/octet-stream",
                }.get(self.path, "audio/mpeg; charset=binary"))
                if self.path in {"/audio", "/slow"}:
                    self.send_header("icy-name", "The Test Station")
                    self.send_header("icy-title", "Not the station name")
                if self.path == "/host":
                    self.send_header("icy-name", self.headers["Host"])
                self.send_header("Content-Length", "1000000000")
                self.end_headers()
                # Probes must return without trying to download live audio.

        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.httpd.daemon_threads = True
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)

    def test_direct_stream_and_redirect_read_station_header_without_body(self):
        self.assertEqual(r.probe_station(self.base + "/audio"), "The Test Station")
        self.assertEqual(r.probe_station(self.base + "/redirect"), "The Test Station")
        self.assertEqual(r.probe_station(self.base + "/redirect-host"),
                         f"localhost:{self.httpd.server_port}")

    def test_hls_and_missing_icy_name_use_fallback(self):
        for path in ("/hls", "/nameless"):
            url = self.base + path
            self.assertEqual(r.probe_station(url), r.fallback_station_name(url))

    def test_html_playlists_and_untyped_responses_are_rejected(self):
        for path in ("/html", "/pls", "/m3u", "/unknown"):
            with self.subTest(path=path), self.assertRaises(r.RadioError) as caught:
                r.probe_station(self.base + path)
            self.assertEqual(caught.exception.code, "unsupported_stream")

    def test_every_redirect_destination_is_validated_and_count_is_bounded(self):
        for path in ("/redirect-file", "/redirect-credentials", "/redirect-fragment"):
            with self.subTest(path=path), self.assertRaises(r.RadioError) as caught:
                r.probe_station(self.base + path)
            self.assertEqual(caught.exception.code, "invalid_stream_url")
        with self.assertRaises(r.RadioError) as caught:
            r.probe_station(self.base + "/redirect-loop")
        self.assertIn("too many", caught.exception.detail)

    def test_timeout_and_http_failure_are_actionable_api_errors(self):
        with self.assertRaises(r.RadioError) as caught:
            r.probe_station(self.base + "/slow", timeout=0.04)
        self.assertEqual(caught.exception.status, 504)
        with self.assertRaises(r.RadioError) as caught:
            r.probe_station(self.base + "/missing")
        self.assertEqual(caught.exception.status, 422)
        self.assertIn("404", caught.exception.detail)


if __name__ == "__main__":
    unittest.main()
