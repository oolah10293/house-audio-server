import copy
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import house_audio_server as h
from test_controllers import NodeMonitor
from test_house_audio_server import FakeMpd

REAL_MPD_CLIENT = h.MpdClient


class StartupFixture(unittest.TestCase):
    def setUp(self):
        self.monitor = NodeMonitor()
        self.mpd = FakeMpd()
        self.startup = h.MpdStartupBoundary()
        self.controllers = h.ControllerRegistry()
        self.shuffle = Mock(side_effect=lambda tracks: tracks.reverse())
        self.policy = self.new_policy()

    def new_policy(self, **kwargs):
        return h.PassiveSessionPolicy(
            self.monitor, mpd_factory=lambda: self.mpd,
            controllers=self.controllers, startup=self.startup,
            shuffle=self.shuffle, **kwargs,
        )

    def assert_idle(self):
        self.assertTrue(self.startup.snapshot()["ready"])
        self.assertEqual(self.mpd.state_data["transport"], "stop")
        self.assertEqual(self.mpd.queue_files, [])
        self.assertEqual(self.mpd.state_data["singleMode"], "0")
        for mode in ("repeat", "random", "consume"):
            self.assertFalse(self.mpd.state_data[mode])


class StartupPolicyTests(StartupFixture):
    def test_every_old_transport_and_drain_artifact_becomes_empty_idle(self):
        for transport, elapsed in (("play", 90.5), ("pause", 90.5), ("pause", 0), ("stop", 0)):
            with self.subTest(transport=transport, elapsed=elapsed):
                self.setUp()
                self.mpd.state_data.update(transport=transport, elapsedSeconds=elapsed,
                                           singleMode="oneshot", consume=True)
                self.policy._tick()
                self.assert_idle()
                self.assertFalse(self.policy.snapshot()["pendingFinalStop"])
                self.assertFalse(self.policy.snapshot()["autoPaused"])
                self.shuffle.assert_not_called()

    def test_passive_already_present_starts_new_configured_default_after_reset(self):
        for folder in ("MP3s", "Rap"):
            with self.subTest(folder=folder):
                self.setUp()
                self.monitor.set("radio")
                self.policy = self.new_policy(default_folder=folder)
                self.policy._tick()
                self.assertTrue(self.startup.snapshot()["ready"])
                self.assertEqual(self.mpd.queue_files, list(reversed(self.mpd.library[folder])))
                self.assertEqual(self.mpd.state_data["transport"], "play")
                self.assertTrue(self.mpd.state_data["repeat"])
                self.assertTrue(self.mpd.state_data["random"])
                self.shuffle.assert_called_once()

    def test_durable_settings_and_identity_survive_but_old_leases_do_not(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_path = str(Path(directory) / "settings.json")
            bindings_path = str(Path(directory) / "controllers.json")
            settings = h.PassiveDefaultSettings(settings_path)
            settings.set_default_folder("Rap")
            old = h.ControllerRegistry(bindings_path)
            lease = old.attach({"controllerId": "phone", "rendererId": "phone-audio",
                                "outputMuted": False, "outputReady": True})
            self.controllers = h.ControllerRegistry(bindings_path)
            self.policy = self.new_policy(settings=h.PassiveDefaultSettings(settings_path))
            self.monitor.set("phone-audio")
            self.policy._tick()
            self.assert_idle()
            presence = self.controllers.snapshot(self.monitor.snapshot())
            self.assertEqual(presence["controllerCount"], 0)
            self.assertEqual(presence["passiveCount"], 0)
            with self.assertRaises(h.ApiError):
                self.controllers.heartbeat({"controllerId": "phone", "leaseId": lease["leaseId"],
                    "sequence": 1, "outputMuted": False, "outputReady": True})
            # A newly attached audible phone still cannot start an idle house.
            self.controllers.attach({"controllerId": "phone", "rendererId": "phone-audio",
                                     "outputMuted": False, "outputReady": True})
            self.policy._tick()
            self.assert_idle()
            self.monitor.set("radio")
            self.policy._tick()
            self.assertEqual(self.mpd.queue_files, list(reversed(self.mpd.library["Rap"])))

    def test_reset_does_not_wait_for_snapserver(self):
        self.monitor.reachable = False
        self.policy._tick()
        self.assert_idle()
        self.assertEqual(self.policy.snapshot()["lastAction"], "waiting_for_snapserver")
        self.monitor.set("radio")
        self.monitor.reachable = True
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "play")
        self.assertEqual(self.mpd.commands.count("stop"), 1)

    def test_partial_failure_and_failed_verification_retry_before_passive_start(self):
        self.monitor.set("radio")
        original_run = self.mpd.run

        def fail_after_stop(*commands):
            original_run(commands[0])
            raise OSError("MPD connection lost mid-reset")

        with patch.object(self.mpd, "run", side_effect=fail_after_stop):
            self.policy._tick()
        self.assertFalse(self.startup.snapshot()["ready"])
        self.assertIn("mid-reset", self.startup.snapshot()["lastError"])
        self.assertEqual(self.mpd.queue_files[0], "CDs/Old/A.mp3")
        with patch.object(self.mpd, "state", side_effect=h.MpdError("verification unavailable")):
            self.policy._tick()
        self.assertFalse(self.startup.snapshot()["ready"])
        self.shuffle.assert_not_called()
        self.policy._tick()
        self.assertTrue(self.startup.snapshot()["ready"])
        self.assertIsNone(self.startup.snapshot()["lastError"])
        self.assertEqual(self.mpd.state_data["transport"], "play")
        self.shuffle.assert_called_once()

    def test_successful_commands_without_idle_confirmation_do_not_open_write_gate(self):
        with patch.object(self.mpd, "run"):
            self.policy._tick()
        self.assertFalse(self.startup.snapshot()["ready"])
        with self.assertRaises(h.ApiError) as caught:
            self.startup.require_ready()
        self.assertEqual(caught.exception.code, "startup_pending")
        self.policy._tick()
        self.assert_idle()

    def test_disabled_presence_policy_still_drives_startup_and_retries(self):
        self.monitor.set("radio")
        self.policy = self.new_policy(enabled=False, poll_seconds=0.001)
        completed = threading.Event()
        original_run = self.mpd.run
        calls = []

        def unavailable_once(*commands):
            calls.append(commands)
            if len(calls) == 1:
                raise OSError("MPD not started yet")
            result = original_run(*commands)
            completed.set()
            return result

        with patch.object(self.mpd, "run", side_effect=unavailable_once):
            self.policy.start()
            try:
                self.assertTrue(completed.wait(2))
            finally:
                self.policy._stop.set()
                self.policy._thread.join(2)
        self.assert_idle()
        self.shuffle.assert_not_called()

    def test_dependency_reconnect_does_not_reset_new_queue_or_resume_manual_pause(self):
        self.policy._tick()
        self.controllers.attach({"controllerId": "browser"})
        self.mpd.run('add "CDs/New/A.mp3"')
        self.mpd.state_data.update(transport="pause", elapsedSeconds=37)
        before = copy.deepcopy((self.mpd.queue_files, self.mpd.state_data))
        with patch.object(self.mpd, "state", side_effect=OSError("MPD offline")):
            self.policy._tick()
        self.monitor.reachable = False
        self.policy._tick()
        self.monitor.reachable = True
        self.policy._tick()
        self.assertEqual((self.mpd.queue_files, self.mpd.state_data), before)
        self.assertEqual(self.mpd.commands.count("clear"), 1)
        self.assertTrue(self.startup.snapshot()["ready"])

    def test_new_session_early_return_still_cancels_drain_unchanged(self):
        self.monitor.set("radio")
        self.policy._tick()
        self.mpd.state_data["elapsedSeconds"] = 47
        before = copy.deepcopy((self.mpd.queue_files, self.mpd.state_data))
        self.monitor.set("radio", present=False)
        self.policy._tick()
        self.assertTrue(self.policy.snapshot()["pendingFinalStop"])
        self.monitor.set("radio")
        self.policy._tick()
        self.assertFalse(self.policy.snapshot()["pendingFinalStop"])
        self.assertEqual((self.mpd.queue_files, self.mpd.state_data), before)
        self.shuffle.assert_called_once()


class StartupHttpTests(StartupFixture):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        settings = h.PassiveDefaultSettings(str(Path(temporary.name) / "settings.json"))
        for name, value in (("MPD_STARTUP", self.startup), ("PASSIVE_SESSION_POLICY", self.policy),
                            ("SNAPCAST_MONITOR", self.monitor), ("CONTROLLERS", self.controllers),
                            ("PASSIVE_DEFAULT_SETTINGS", settings)):
            patcher = patch.object(h, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for patcher in (patch.object(h, "MpdClient", return_value=self.mpd),
                        patch.object(h.ApiHandler, "log_message")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.mpd.ping = lambda: "0.24.4"
        self.server = h.ThreadingHTTPServer(("127.0.0.1", 0), h.ApiHandler)
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, path, payload=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        try:
            connection.request("POST" if payload is not None else "GET", path,
                body=json.dumps(payload) if payload is not None else None,
                headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_all_mpd_writes_rejected_until_reset_verified_then_never_erased(self):
        for path, payload in (("/play", {}), ("/pause", {}), ("/stop", {}), ("/next", {}),
                              ("/previous", {}), ("/seek", {"seconds": 10}),
                              ("/shuffle", {"enabled": True}), ("/repeat", {"enabled": True}),
                              ("/queue/clear", {}), ("/queue/replace", {"tracks": ["CDs/New/A.mp3"]}),
                              ("/queue/reorder", {"songIds": [42], "queueVersion": 1})):
            with self.subTest(path=path):
                status, body = self.request(path, payload)
                self.assertEqual(status, 503)
                self.assertEqual(body["error"], "startup_pending")
        self.assertEqual(self.mpd.commands, [])
        self.policy._tick()
        # Exercise real queue replacement with the fake command transport.
        self.mpd.replace_queue = lambda **kw: REAL_MPD_CLIENT.replace_queue(self.mpd, **kw)
        status, _ = self.request("/queue/replace", {"tracks": ["CDs/New/A.mp3"], "play": False})
        self.assertEqual(status, 200)
        self.assertEqual(self.request("/play", {})[0], 200)
        self.policy._tick()
        self.assertEqual(self.mpd.queue_files, ["CDs/New/A.mp3"])
        self.assertEqual(self.mpd.state_data["transport"], "play")

    def test_health_exposes_pending_even_when_dependencies_are_reachable(self):
        status, body = self.request("/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "degraded")
        self.assertFalse(body["startup"]["ready"])
        self.assertFalse(self.request("/session")[1]["sessionPolicy"]["startup"]["ready"])
        self.policy._tick()
        body = self.request("/health")[1]
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["startup"]["ready"])

    def test_settings_and_new_lease_work_while_reset_pending_and_survive_it(self):
        self.assertEqual(self.request("/settings", {"passiveDefaultFolder": "Rap"})[0], 200)
        status, lease = self.request("/controllers/attach", {"controllerId": "browser"})
        self.assertEqual(status, 200)
        self.policy._tick()
        self.assert_idle()
        self.assertEqual(self.request("/settings")[1]["settings"]["passiveDefaultFolder"], "Rap")
        self.assertEqual(self.request("/controllers/heartbeat", {"controllerId": "browser",
            "leaseId": lease["leaseId"], "sequence": 1, "outputMuted": True, "outputReady": False})[0], 200)
        self.assertEqual(self.controllers.snapshot(self.monitor.snapshot())["controllerCount"], 1)

    def test_removed_endpoints_cannot_write_or_reserve_house_before_or_after_startup(self):
        for ready in (False, True):
            if ready:
                self.policy._tick()
            before = copy.deepcopy((self.mpd.commands, self.mpd.queue_files, self.mpd.state_data))
            for action in ("prepare", "commit", "status", "cancel"):
                status, body = self.request("/session/handoff/" + action, {
                    "controllerId": "old-phone", "handoffId": "old-transfer",
                    "tracks": ["Rap/A.mp3"], "startIndex": 0,
                    "positionSeconds": 12, "shuffle": True, "repeat": True,
                })
                self.assertEqual((status, body["error"]), (404, "not_found"))
            for path in ("/diagnostics", "/diagnostics?limit=50"):
                status, body = self.request(path)
                self.assertEqual((status, body["error"]), (404, "not_found"))
            self.assertEqual((self.mpd.commands, self.mpd.queue_files, self.mpd.state_data), before)
        self.monitor.set("radio")
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "play")
        self.assertEqual(self.mpd.queue_files, list(reversed(self.mpd.library["MP3s"])))

    def test_transport_commands_and_reorder_still_work_without_transfer_hooks(self):
        self.monitor.set("radio")
        self.policy._tick()
        for path, payload, command in (
            ("/pause", {}, "pause 1"), ("/play", {}, "play"),
            ("/next", {}, "next"), ("/previous", {}, "previous"),
            ("/seek", {"seconds": 12.5}, "seekcur 12.500"),
            ("/shuffle", {"enabled": False}, "random 0"),
            ("/repeat", {"enabled": False}, "repeat 0"),
            ("/stop", {}, "stop"),
        ):
            with self.subTest(path=path):
                before = len(self.mpd.commands)
                self.assertEqual(self.request(path, payload)[0], 200)
                self.assertEqual(self.mpd.commands[before:], [command])
        self.mpd.state_data["queueVersion"] = 7
        self.mpd.queue = lambda: [{"id": i} for i in (1, 2, 3)]
        self.mpd.reorder_queue = lambda *args: REAL_MPD_CLIENT.reorder_queue(self.mpd, *args)
        before = len(self.mpd.commands)
        self.assertEqual(self.request("/queue/reorder", {"songIds": [3, 1, 2], "queueVersion": 7})[0], 200)
        self.assertEqual(self.mpd.commands[before:], ["moveid 3 0"])

    def test_explicit_pause_still_cancels_final_drain_via_http(self):
        self.monitor.set("radio")
        self.policy._tick()
        self.monitor.set("radio", present=False)
        self.policy._tick()
        self.assertTrue(self.policy.snapshot()["pendingFinalStop"])
        self.assertEqual(self.request("/pause", {})[0], 200)
        self.assertFalse(self.policy.snapshot()["pendingFinalStop"])
        self.assertEqual(self.mpd.state_data["transport"], "pause")
        self.assertTrue(self.mpd.state_data["repeat"])
        self.assertEqual(self.mpd.state_data["singleMode"], "0")


if __name__ == "__main__":
    unittest.main()
