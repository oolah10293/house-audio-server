"""Regression coverage for the shared HOUSE browser controller."""
import unittest
from unittest.mock import Mock, patch

import house_audio_server as h


class HouseWebRouteTests(unittest.TestCase):
    def test_cd_browse_exposes_disc_and_track_tags(self):
        result = h.normalize_library_entry({
            "file": "CDs/Album/201-song.mp3",
            "track": "1/12",
            "disc": "2/3",
            "last-modified": "2026-10-01T12:00:00Z",
        })
        self.assertEqual(result["track"], "1/12")
        self.assertEqual(result["disc"], "2/3")
        self.assertEqual(result["lastModified"], "2026-10-01T12:00:00Z")

    def test_browser_assets_are_served_by_existing_http_handler(self):
        cases = {
            "/": ("house.html", "text/html; charset=utf-8"),
            "/?utm_source=chatgpt.com": ("house.html", "text/html; charset=utf-8"),
            "/house": ("house.html", "text/html; charset=utf-8"),
            "/house/": ("house.html", "text/html; charset=utf-8"),
            "/house.css": ("house.css", "text/css; charset=utf-8"),
            "/house.js": ("house.js", "text/javascript; charset=utf-8"),
            "/subwoofers": ("subwoofers.html", "text/html; charset=utf-8"),
        }
        for path, asset in cases.items():
            with self.subTest(path=path):
                handler = object.__new__(h.ApiHandler)
                handler.path = path
                handler._page_asset = Mock()
                handler._json = Mock()
                handler.do_GET()
                handler._page_asset.assert_called_once_with(*asset)
                handler._json.assert_not_called()


class ExistingQueueSelectionTests(unittest.TestCase):
    def handler(self, payload):
        handler = object.__new__(h.ApiHandler)
        handler.path = "/queue/play"
        handler._read_json = Mock(return_value=payload)
        handler._json = Mock()
        handler._mpd_state_response = Mock()
        return handler

    def run_selection(self, handler, queue):
        mpd = Mock()
        mpd.queue.return_value = queue
        mpd.state.return_value = {"transport": "pause", "queueLength": len(queue)}
        with (
            patch.object(h, "MpdClient", return_value=mpd),
            patch.object(h.MPD_STARTUP, "require_ready"),
            patch.object(h.RADIO, "reconcile"),
            patch.object(h.RADIO, "station", None),
            patch.object(h.RADIO.playlist_resume, "status", "ready"),
            patch.object(h.PASSIVE_SESSION_POLICY, "explicit_transport") as explicit,
        ):
            handler.do_POST()
        return mpd, explicit

    def test_play_existing_id_without_rebuilding_queue(self):
        handler = self.handler({"songId": 42})
        mpd, explicit = self.run_selection(
            handler, [{"id": 17}, {"id": 42}, {"id": 51}])
        mpd.command.assert_called_once_with("playid 42")
        explicit.assert_called_once()
        handler._mpd_state_response.assert_called_once()
        handler._json.assert_not_called()

    def test_stale_id_rejected_without_any_playback_change(self):
        handler = self.handler({"songId": 987})
        mpd, explicit = self.run_selection(handler, [{"id": 17}])
        mpd.command.assert_not_called()
        explicit.assert_not_called()
        self.assertEqual(handler._json.call_args.args[0], 409)
        self.assertEqual(handler._json.call_args.args[1]["error"], "stale_queue")

    def test_invalid_id_rejected_without_any_playback_change(self):
        for payload in ({}, {"songId": "42"}, {"songId": -1}, {"songId": True},
                        {"songId": 42, "unexpected": True}):
            with self.subTest(payload=payload):
                handler = self.handler(payload)
                mpd, explicit = self.run_selection(handler, [{"id": 42}])
                mpd.command.assert_not_called()
                explicit.assert_not_called()
                self.assertEqual(handler._json.call_args.args[0], 400)

    def test_radio_queue_selection_is_blocked(self):
        handler = self.handler({"songId": 42})
        mpd = Mock()
        mpd.state.return_value = {"transport": "play", "queueLength": 1}
        with (
            patch.object(h, "MpdClient", return_value=mpd),
            patch.object(h.MPD_STARTUP, "require_ready"),
            patch.object(h.RADIO, "reconcile"),
            patch.object(h.RADIO, "station", {"id": "radio-a"}),
            patch.object(h.RADIO.playlist_resume, "status", "ready"),
        ):
            handler.do_POST()
        mpd.command.assert_not_called()
        self.assertEqual(handler._json.call_args.args[0], 409)
        self.assertEqual(handler._json.call_args.args[1]["error"], "live_stream_operation")


if __name__ == "__main__":
    unittest.main()
