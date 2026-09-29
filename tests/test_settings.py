import copy
import http.client
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

import house_audio_server as h
from test_house_audio_server import FakeMonitor, FakeMpd


class SettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "settings.json"
        self.settings = h.PassiveDefaultSettings(str(self.path))

    def test_missing_file_uses_fallback_without_writing(self):
        settings = h.PassiveDefaultSettings(str(self.path), fallback="Rap")
        self.assertEqual(settings.snapshot()["passiveDefaultFolder"], "Rap")
        self.assertFalse(self.path.exists())

    def test_saved_choice_survives_restart_and_overrides_environment(self):
        # Explicitly choosing the current fallback must persist it too.
        for folder, fallback in (("MP3s", "Rap"), ("Rap", "MP3s")):
            self.settings.set_default_folder(folder)
            restarted = h.PassiveDefaultSettings(str(self.path), fallback=fallback)
            self.assertEqual(restarted.snapshot()["passiveDefaultFolder"], folder)
            self.assertEqual(json.loads(self.path.read_text()),
                             {"passiveDefaultFolder": folder})

    def test_invalid_values_leave_saved_choice_unchanged(self):
        self.settings.set_default_folder("Rap")
        original = self.path.read_bytes()
        for invalid in (None, True, 0, [], {}, "", "rap", "MP3s/", "../Rap", "CDs", "Rap\nstop"):
            with self.subTest(value=invalid), self.assertRaises(h.ApiError) as caught:
                self.settings.set_default_folder(invalid)
            self.assertEqual(caught.exception.status, 400)
            self.assertEqual(self.settings.snapshot()["passiveDefaultFolder"], "Rap")
            self.assertEqual(self.path.read_bytes(), original)

    def test_bad_saved_data_does_not_silently_revert_or_overwrite(self):
        for content in ("broken json", "[]", "{}", '{"passiveDefaultFolder":"CDs"}'):
            self.path.write_text(content)
            with self.subTest(content=content), self.assertRaises((ValueError, h.ApiError)):
                h.PassiveDefaultSettings(str(self.path))
            self.assertEqual(self.path.read_text(), content)
        with patch.object(Path, "open", side_effect=PermissionError("unreadable")):
            with self.assertRaises(PermissionError):
                h.PassiveDefaultSettings(str(self.path))

    def test_failed_replace_retains_old_value_and_cleans_temporary_file(self):
        self.settings.set_default_folder("MP3s")
        with patch.object(h.os, "replace", side_effect=OSError("disk failure")):
            with self.assertRaises(h.ApiError) as caught:
                self.settings.set_default_folder("Rap")
        self.assertEqual(caught.exception.code, "settings_write_failed")
        self.assertEqual(self.settings.snapshot()["passiveDefaultFolder"], "MP3s")
        self.assertEqual(h.PassiveDefaultSettings(str(self.path)).snapshot(), self.settings.snapshot())
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_directory_sync_failure_reports_uncertainty_but_keeps_memory_consistent(self):
        with patch.object(h.os, "fsync", side_effect=[None, OSError("sync failure")]):
            with self.assertRaises(h.ApiError) as caught:
                self.settings.set_default_folder("Rap")
        self.assertEqual(caught.exception.status, 503)
        self.assertEqual(self.settings.snapshot()["passiveDefaultFolder"], "Rap")
        self.assertEqual(h.PassiveDefaultSettings(str(self.path)).snapshot(), self.settings.snapshot())

    def test_concurrent_explicit_sets_remain_valid_and_consistent(self):
        folders = ["Rap", "MP3s"] * 4
        with ThreadPoolExecutor(max_workers=4) as executor:
            responses = list(executor.map(self.settings.set_default_folder, folders))
        self.assertEqual([item["passiveDefaultFolder"] for item in responses], folders)
        self.assertEqual(h.PassiveDefaultSettings(str(self.path)).snapshot(), self.settings.snapshot())
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])


class RuntimeDefaultPolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "settings.json"
        self.settings = h.PassiveDefaultSettings(str(self.path))
        self.monitor = FakeMonitor(present=1)
        self.mpd = FakeMpd()
        self.shuffle = Mock(side_effect=lambda tracks: tracks.reverse())
        self.policy = h.PassiveSessionPolicy(
            self.monitor, mpd_factory=lambda: self.mpd, enabled=True,
            shuffle=self.shuffle, settings=self.settings,
        )

    def test_setting_does_not_touch_playing_paused_or_stopped_session(self):
        for transport in ("play", "pause", "stop"):
            self.mpd.state_data["transport"] = transport
            before = copy.deepcopy((self.mpd.state_data, self.mpd.queue_files))
            self.settings.set_default_folder("Rap")
            self.assertEqual((self.mpd.state_data, self.mpd.queue_files), before)
            self.assertEqual(self.mpd.commands, [])
            self.assertEqual(self.policy.snapshot()["defaultFolder"], "Rap")
        self.shuffle.assert_not_called()

    def test_changed_default_preserves_early_return_then_applies_after_completed_drain(self):
        original_queue = list(self.mpd.queue_files)
        self.policy._tick()
        self.monitor.present = 0
        self.policy._tick()
        self.settings.set_default_folder("Rap")
        self.assertTrue(self.policy.snapshot()["pendingFinalStop"])
        self.monitor.present = 1
        self.policy._tick()
        self.assertFalse(self.policy.snapshot()["pendingFinalStop"])
        self.assertEqual(self.policy.snapshot()["lastAction"], "pending_stop_cancelled_renderer_returned")
        self.assertEqual(self.mpd.queue_files, original_queue)
        self.assertEqual(self.mpd.state_data["songId"], 42)
        self.assertEqual(self.mpd.state_data["elapsedSeconds"], 90.5)
        self.shuffle.assert_not_called()

        self.monitor.present = 0
        self.policy._tick()
        self.mpd.state_data.update(transport="pause", songId=99, elapsedSeconds=0.0)
        self.policy._tick()
        self.monitor.present = 1
        self.policy._tick()
        self.assertEqual(self.mpd.queue_files, list(reversed(self.mpd.library["Rap"])))
        self.assertEqual(self.mpd.state_data["transport"], "play")
        self.shuffle.assert_called_once()

    def test_ordinary_paused_queue_resumes_despite_new_default(self):
        self.monitor.present = 0
        self.mpd.state_data["transport"] = "pause"
        self.policy._tick()
        self.settings.set_default_folder("Rap")
        original_queue = list(self.mpd.queue_files)
        self.monitor.present = 1
        self.policy._tick()
        self.assertEqual(self.mpd.queue_files, original_queue)
        self.assertEqual(self.mpd.commands, ["play"])
        self.assertEqual(self.mpd.state_data["elapsedSeconds"], 90.5)
        self.shuffle.assert_not_called()

    def test_restart_reads_saved_selection_without_starting_until_radio_arrives(self):
        self.settings.set_default_folder("Rap")
        restarted = h.PassiveDefaultSettings(str(self.path), fallback="MP3s")
        self.monitor.present = 0
        self.mpd.state_data["transport"] = "stop"
        policy = h.PassiveSessionPolicy(
            self.monitor, mpd_factory=lambda: self.mpd, enabled=True,
            settings=restarted, shuffle=self.shuffle,
        )
        policy._tick()
        self.assertEqual(self.mpd.commands, [])
        self.monitor.present = 1
        policy._tick()
        self.assertEqual(self.mpd.queue_files, list(reversed(self.mpd.library["Rap"])))
        self.assertTrue(policy.snapshot()["runtimeDefaultFolderImplemented"])

    def test_folder_is_chosen_once_per_fresh_start(self):
        self.mpd.state_data["transport"] = "stop"
        original_files = self.mpd.library_files

        def change_while_loading(folder):
            self.settings.set_default_folder("Rap")
            return original_files(folder)

        self.mpd.library_files = change_while_loading
        self.policy._tick()
        self.assertEqual(self.mpd.queue_files, list(reversed(self.mpd.library["MP3s"])))
        self.assertEqual(self.policy.snapshot()["defaultFolder"], "Rap")


class SettingsHttpTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "settings.json"
        self.settings = h.PassiveDefaultSettings(str(self.path))
        for name, value in (
            ("PASSIVE_DEFAULT_SETTINGS", self.settings),
            ("PASSIVE_SESSION_POLICY", h.PassiveSessionPolicy(FakeMonitor(), settings=self.settings)),
        ):
            patcher = patch.object(h, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.mpd_patcher = patch.object(h, "MpdClient", side_effect=AssertionError("Settings must not call MPD"))
        self.mpd_patcher.start()
        self.addCleanup(self.mpd_patcher.stop)
        log = patch.object(h.ApiHandler, "log_message")
        log.start()
        self.addCleanup(log.stop)
        self.server = h.ThreadingHTTPServer(("127.0.0.1", 0), h.ApiHandler)
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method="GET", body=None, path="/settings"):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        try:
            connection.request(method, path, body=json.dumps(body) if body is not None else None,
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_get_set_and_session_read_work_without_mpd(self):
        status, body = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(body["settings"], {"passiveDefaultFolder": "MP3s", "allowedPassiveDefaultFolders": ["MP3s", "Rap"]})
        for _ in range(2):  # Retrying the same explicit value is idempotent.
            status, body = self.request("POST", {"passiveDefaultFolder": "Rap"})
            self.assertEqual(status, 200)
            self.assertEqual(body["settings"]["passiveDefaultFolder"], "Rap")
        self.assertEqual(self.request()[1]["settings"], body["settings"])
        status, body = self.request(path="/session")
        self.assertEqual(status, 200)
        self.assertEqual(body["sessionPolicy"]["defaultFolder"], "Rap")
        self.assertTrue(body["sessionPolicy"]["runtimeDefaultFolderImplemented"])
        self.assertEqual(h.PassiveDefaultSettings(str(self.path)).snapshot()["passiveDefaultFolder"], "Rap")

    def test_bad_requests_return_400_without_creating_settings(self):
        for payload in ({}, {"passiveDefaultFolder": "CDs"}, {"passiveDefaultFolder": False},
                        {"passiveDefaultFolder": "Rap", "play": True}, [], "Rap"):
            with self.subTest(payload=payload):
                status, _ = self.request("POST", payload)
                self.assertEqual(status, 400)
                self.assertFalse(self.path.exists())

    def test_storage_failure_is_reported_as_settings_error(self):
        with patch.object(h.os, "replace", side_effect=PermissionError("read only")):
            status, body = self.request("POST", {"passiveDefaultFolder": "Rap"})
        self.assertEqual(status, 503)
        self.assertEqual(body["error"], "settings_write_failed")
        self.assertEqual(self.request()[1]["settings"]["passiveDefaultFolder"], "MP3s")


if __name__ == "__main__":
    unittest.main()
