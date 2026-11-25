import copy
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import house_audio_server as h
from test_house_audio_server import FakeClock, FakeMpd


class NodeMonitor:
    def __init__(self):
        self.reachable = True
        self.nodes = {}

    def set(self, renderer_id, present=True, audible=True):
        self.nodes[renderer_id] = {"id": renderer_id, "present": present, "audible": audible}

    def snapshot(self):
        return {"reachable": self.reachable, "clients": list(self.nodes.values())}


class ControllerFixture(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.registry = h.ControllerRegistry(clock=self.clock)
        self.monitor = NodeMonitor()
        self.mpd = FakeMpd()
        self.shuffle = Mock(side_effect=lambda tracks: tracks.reverse())
        self.policy = h.PassiveSessionPolicy(
            self.monitor, mpd_factory=lambda: self.mpd, controllers=self.registry,
            enabled=True, shuffle=self.shuffle,
        )

    def attach(self, name="phone", renderer="phone-audio", muted=True):
        return self.registry.attach({"controllerId": name, "rendererId": renderer,
                                     "outputMuted": muted, "outputReady": renderer is not None})

    def heartbeat(self, lease, muted=False, sequence=1, ready=True):
        return self.registry.heartbeat({"controllerId": lease["controller"]["controllerId"],
            "leaseId": lease["leaseId"], "outputMuted": muted, "outputReady": ready, "sequence": sequence})

    def detach(self, lease):
        return self.registry.detach({"controllerId": lease["controller"]["controllerId"], "leaseId": lease["leaseId"]})

    def pause_for_unavailable_phone(self):
        lease = self.attach(muted=False)
        self.monitor.set("radio")
        self.policy._tick()
        self.monitor.set("radio", present=False)
        self.policy._tick()
        self.assertTrue(self.policy.snapshot()["autoPaused"])
        self.assertEqual(self.mpd.state_data["transport"], "pause")
        return lease


class RegistryTests(ControllerFixture):
    def test_controller_and_renderer_are_one_node_and_never_passive(self):
        lease = self.attach(muted=False)
        self.monitor.set("phone-audio")
        presence = self.registry.snapshot(self.monitor.snapshot())
        self.assertEqual((presence["presentCount"], presence["controllerCount"],
                          presence["passiveCount"], presence["audibleCount"]), (1, 1, 0, 1))
        self.assertNotIn("leaseId", presence["controllers"][0])
        self.heartbeat(lease, muted=True)
        presence = self.registry.snapshot(self.monitor.snapshot())
        self.assertEqual((presence["presentCount"], presence["audibleCount"]), (0, 0))
        self.assertEqual(presence["controllerCount"], 1)
        self.assertEqual(presence["unmutedControllerCount"], 0)

    def test_unmuted_report_requires_real_present_audible_renderer(self):
        self.attach(muted=False)
        for present, audible in ((False, True), (True, False), (False, False)):
            self.monitor.set("phone-audio", present=present, audible=audible)
            self.assertEqual(self.registry.snapshot(self.monitor.snapshot())["audibleCount"], 0)

    def test_heartbeat_keeps_background_controller_until_exact_expiry(self):
        lease = self.attach()
        self.clock.advance(14)
        self.heartbeat(lease, muted=True)
        self.clock.advance(14.9)
        self.assertEqual(self.registry.snapshot(self.monitor.snapshot())["controllerCount"], 1)
        self.clock.advance(0.1)
        self.assertEqual(self.registry.snapshot(self.monitor.snapshot())["controllerCount"], 0)
        with self.assertRaises(h.ApiError) as caught:
            self.heartbeat(lease, sequence=2)
        self.assertEqual(caught.exception.code, "expired_controller_lease")

    def test_stale_messages_cannot_unmute_or_detach_new_attachment(self):
        old = self.attach()
        new = self.attach()
        for operation in (lambda: self.heartbeat(old), lambda: self.detach(old)):
            with self.assertRaises(h.ApiError) as caught:
                operation()
            self.assertEqual(caught.exception.code, "stale_controller_lease")
        self.heartbeat(new, muted=True, sequence=2)
        for sequence in (1, 2):
            with self.assertRaises(h.ApiError) as caught:
                self.heartbeat(new, muted=False, sequence=sequence)
            self.assertEqual(caught.exception.code, "stale_controller_sequence")
        self.assertTrue(self.registry.snapshot(self.monitor.snapshot())["controllers"][0]["outputMuted"])

    def test_detach_is_immediate_and_idempotent_despite_lingering_socket(self):
        lease = self.attach(muted=False)
        self.monitor.set("phone-audio")
        self.detach(lease)
        self.detach(lease)
        presence = self.registry.snapshot(self.monitor.snapshot())
        self.assertEqual((presence["presentCount"], presence["passiveCount"]), (0, 0))
        with self.assertRaises(h.ApiError):
            self.heartbeat(lease, muted=False)

    def test_audio_can_survive_expired_control_lease_without_becoming_passive(self):
        self.attach(muted=False)
        self.monitor.set("phone-audio")
        self.clock.advance(15)
        presence = self.registry.snapshot(self.monitor.snapshot())
        self.assertEqual((presence["controllerCount"], presence["presentCount"], presence["passiveCount"]), (0, 1, 0))

    def test_durable_binding_prevents_phone_autostart_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "controllers.json")
            registry = h.ControllerRegistry(path, clock=self.clock)
            registry.attach({"controllerId": "phone", "rendererId": "phone-audio"})
            restarted = h.ControllerRegistry(path, clock=self.clock)
            self.monitor.set("phone-audio")
            self.mpd.state_data["transport"] = "stop"
            policy = h.PassiveSessionPolicy(self.monitor, controllers=restarted,
                mpd_factory=lambda: self.mpd, enabled=True, shuffle=self.shuffle)
            policy._tick()
            self.assertEqual(self.mpd.commands, [])
            self.assertEqual(restarted.snapshot(self.monitor.snapshot())["controllerCount"], 0)
            with self.assertRaises(h.ApiError) as caught:
                restarted.attach({"controllerId": "other", "rendererId": "phone-audio"})
            self.assertEqual(caught.exception.code, "renderer_already_owned")

    def test_failed_binding_save_does_not_admit_controller(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "controllers.json"
            registry = h.ControllerRegistry(str(path), clock=self.clock)
            with patch.object(h.os, "replace", side_effect=OSError("disk full")):
                with self.assertRaises(h.ApiError) as caught:
                    registry.attach({"controllerId": "phone", "rendererId": "phone-audio"})
            self.assertEqual(caught.exception.code, "controller_storage_failed")
            self.assertEqual(registry.snapshot(self.monitor.snapshot())["controllerCount"], 0)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_corrupt_bindings_fail_instead_of_reclassifying_phone_as_radio(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "controllers.json"
            for content in ("broken", "[]", '{"phone-audio":false}'):
                path.write_text(content)
                with self.assertRaises((ValueError, h.ApiError)):
                    h.ControllerRegistry(str(path))


class ControllerPolicyTests(ControllerFixture):
    def test_muting_last_paused_phone_ends_session_and_passive_return_starts_fresh(self):
        for automatic in (False, True):
            with self.subTest(automatic=automatic):
                self.setUp()
                if automatic:
                    lease = self.pause_for_unavailable_phone()
                else:
                    lease = self.attach(muted=False)
                    self.mpd.state_data["transport"] = "pause"
                    self.policy._tick()
                self.heartbeat(lease, muted=True)
                self.policy._tick()
                self.assertEqual(self.mpd.state_data["transport"], "stop")
                self.assertEqual(self.mpd.queue_files, [])
                self.assertFalse(self.policy.snapshot()["autoPaused"])
                self.assertFalse(self.policy.snapshot()["pendingFinalStop"])
                self.assertEqual(self.policy.snapshot()["controllerCount"], 1)
                self.assertEqual(self.policy.snapshot()["presentCount"], 0)
                self.monitor.set("radio")
                self.policy._tick()
                self.assertEqual(self.mpd.queue_files, list(reversed(self.mpd.library["MP3s"])))

    def test_muted_only_first_sample_cannot_hold_existing_pause(self):
        self.attach()
        self.mpd.state_data["transport"] = "pause"
        self.policy._tick()
        self.assertEqual(self.mpd.commands, ["clear"])
        self.assertEqual(self.mpd.queue_files, [])

    def test_unmuted_paused_phone_holds_session_with_background_heartbeats(self):
        for ready in (False, True):
            with self.subTest(ready=ready):
                self.setUp()
                lease = self.attach(muted=False)
                self.mpd.state_data["transport"] = "pause"
                before = copy.deepcopy((self.mpd.state_data, self.mpd.queue_files))
                for sequence in range(1, 5):
                    self.clock.advance(5)
                    self.heartbeat(lease, muted=False, ready=ready, sequence=sequence)
                    self.policy._tick()
                self.assertEqual((self.mpd.state_data, self.mpd.queue_files), before)
                self.assertEqual(self.mpd.commands, [])
                self.assertEqual(self.policy.snapshot()["unmutedControllerCount"], 1)
                self.assertEqual(self.policy.snapshot()["presentCount"], 1)

    def test_one_unmuted_phone_holds_pause_among_muted_controllers_until_expiry(self):
        self.attach(muted=False)
        muted = self.attach(name="second", renderer="second-out")
        self.attach(name="browser", renderer=None)
        self.mpd.state_data["transport"] = "pause"
        self.policy._tick()
        self.clock.advance(14)
        self.heartbeat(muted, muted=True)
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "pause")
        self.clock.advance(1)
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "stop")
        self.assertEqual(self.policy.snapshot()["controllerCount"], 1)

    def test_muted_heartbeats_do_not_prevent_drain_completion_or_fresh_shuffle(self):
        lease = self.attach()
        self.monitor.set("radio")
        self.policy._tick()
        self.monitor.set("radio", present=False)
        self.policy._tick()
        self.assertTrue(self.policy.snapshot()["pendingFinalStop"])
        for sequence in range(1, 4):
            self.clock.advance(5)
            self.heartbeat(lease, muted=True, sequence=sequence)
            self.policy._tick()
            self.assertTrue(self.policy.snapshot()["pendingFinalStop"])
            self.assertEqual(self.mpd.state_data["transport"], "play")
        self.mpd.state_data.update(transport="pause", songId=99, elapsedSeconds=0)
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "stop")
        self.assertFalse(self.policy.snapshot()["pendingFinalStop"])
        self.heartbeat(lease, muted=False, sequence=4)
        self.monitor.set("phone-audio")
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "stop")
        self.monitor.set("radio")
        self.policy._tick()
        self.assertEqual(self.mpd.queue_files, list(reversed(self.mpd.library["MP3s"])))

    def test_muting_phone_preserves_other_node_playback_and_manual_pause(self):
        lease = self.attach(muted=False)
        self.monitor.set("radio")
        self.monitor.set("phone-audio")
        self.policy._tick()
        for sequence, transport in enumerate(("play", "pause"), 1):
            self.mpd.state_data["transport"] = transport
            before = copy.deepcopy((self.mpd.state_data, self.mpd.queue_files))
            self.heartbeat(lease, muted=True, sequence=sequence)
            self.policy._tick()
            self.assertEqual((self.mpd.state_data, self.mpd.queue_files), before)
        self.assertEqual(self.mpd.commands, [])

    def test_pause_during_muted_only_drain_ends_without_advancing(self):
        self.attach()
        self.policy._tick()
        self.assertTrue(self.policy.snapshot()["pendingFinalStop"])
        self.mpd.state_data["transport"] = "pause"
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "stop")
        self.assertEqual(self.mpd.queue_files, [])
        self.assertFalse(self.policy.snapshot()["pendingFinalStop"])
        self.assertTrue(self.mpd.state_data["repeat"])
        self.assertEqual(self.mpd.state_data["elapsedSeconds"], 90.5)

    def test_failed_manual_pause_session_end_retries_with_muted_controller(self):
        self.attach()
        self.mpd.state_data["transport"] = "pause"
        with patch.object(self.mpd, "command", side_effect=OSError("offline")):
            self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "pause")
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "stop")

    def test_unmuted_controller_last_pause_end_cannot_restart_old_queue(self):
        lease = self.attach(muted=False)
        self.mpd.state_data["transport"] = "pause"
        self.policy._tick()
        old_queue = list(self.mpd.queue_files)
        self.assertTrue(old_queue)
        self.detach(lease)
        self.policy._tick()
        self.assertEqual(self.mpd.commands, ["clear"])
        self.assertEqual(self.mpd.state_data["transport"], "stop")
        self.assertEqual(self.mpd.queue_files, [])
        self.mpd.command("play")
        self.assertEqual(self.mpd.state_data["transport"], "stop")
        self.monitor.set("radio")
        self.policy._tick()
        self.assertEqual(self.mpd.queue_files,
                         list(reversed(self.mpd.library["MP3s"])))

    def test_failed_paused_end_clear_retries_when_only_muted_phone_remains(self):
        lease = self.attach(muted=False)
        self.mpd.state_data["transport"] = "pause"
        self.policy._tick()
        self.heartbeat(lease, muted=True)
        original_command = self.mpd.command
        def fail_clear(command):
            if command == "clear":
                raise OSError("MPD queue clear failed")
            return original_command(command)
        with patch.object(self.mpd, "command", side_effect=fail_clear):
            self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "pause")
        self.assertTrue(self.mpd.queue_files)
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "stop")
        self.assertEqual(self.mpd.queue_files, [])
        self.assertEqual(self.policy.snapshot()["lastAction"],
                         "last_session_participant_left_session_idle")

    def test_controller_only_fresh_idle_never_starts_even_unmuted(self):
        for muted in (True, False):
            self.attach(muted=muted)
            self.monitor.set("phone-audio")
            self.mpd.state_data["transport"] = "stop"
            self.policy._tick()
            self.assertEqual(self.mpd.commands, [])
        self.shuffle.assert_not_called()

    def test_muted_controller_join_and_quit_preserve_other_listeners(self):
        self.monitor.set("radio")
        self.policy._tick()
        before = copy.deepcopy((self.mpd.state_data, self.mpd.queue_files))
        lease = self.attach()
        self.policy._tick()
        self.detach(lease)
        self.policy._tick()
        self.assertEqual((self.mpd.state_data, self.mpd.queue_files), before)
        self.assertEqual(self.mpd.commands, [])

    def test_unmuted_phone_without_receiver_holds_pause_when_last_radio_leaves(self):
        original = list(self.mpd.queue_files)
        self.pause_for_unavailable_phone()
        self.assertEqual(self.mpd.commands, ["pause 1"])
        self.assertFalse(self.policy.snapshot()["pendingFinalStop"])
        self.assertEqual(self.mpd.queue_files, original)
        self.assertEqual(self.mpd.state_data["elapsedSeconds"], 90.5)
        self.shuffle.assert_not_called()

    def test_unmuted_receiver_recovery_resumes_only_when_audible(self):
        lease = self.pause_for_unavailable_phone()
        self.heartbeat(lease, muted=False)
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "pause")
        self.monitor.set("phone-audio")
        self.policy._tick()
        self.assertEqual(self.mpd.commands, ["pause 1", "play"])
        self.assertFalse(self.policy.snapshot()["autoPaused"])
        self.assertEqual(self.mpd.state_data["elapsedSeconds"], 90.5)

    def test_passive_radio_resumes_retained_pause(self):
        self.pause_for_unavailable_phone()
        self.monitor.set("radio")
        self.policy._tick()
        self.assertEqual(self.mpd.commands, ["pause 1", "play"])
        self.shuffle.assert_not_called()

    def test_manual_pause_overrides_automatic_pause_on_controller_unmute(self):
        lease = self.pause_for_unavailable_phone()
        self.policy.explicit_transport(self.mpd)
        self.mpd.command("pause 1")
        self.heartbeat(lease, muted=False)
        self.monitor.set("phone-audio")
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "pause")
        self.assertNotIn("play", self.mpd.commands)
        self.monitor.set("radio")
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "play")

    def test_controller_arrival_does_not_override_ordinary_pause(self):
        self.mpd.state_data["transport"] = "pause"
        self.attach(muted=False)
        self.monitor.set("phone-audio")
        self.policy._tick()
        self.assertEqual(self.mpd.commands, [])

    def test_last_unmuted_detach_ends_automatic_pause_without_advancing(self):
        lease = self.pause_for_unavailable_phone()
        self.detach(lease)
        self.policy._tick()
        self.assertEqual(self.mpd.commands, ["pause 1", "clear"])
        self.assertEqual(self.mpd.queue_files, [])
        self.assertFalse(self.policy.snapshot()["autoPaused"])
        self.assertFalse(self.policy.snapshot()["pendingFinalStop"])
        self.assertEqual(self.mpd.state_data["elapsedSeconds"], 90.5)
        self.monitor.set("radio")
        self.policy._tick()
        self.assertEqual(self.mpd.queue_files, list(reversed(self.mpd.library["MP3s"])))

    def test_control_only_browser_does_not_hold_session_after_unmuted_phone_leaves(self):
        first = self.pause_for_unavailable_phone()
        self.attach(name="browser", renderer=None)
        self.detach(first)
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "stop")
        self.assertEqual(self.policy.snapshot()["controllerCount"], 1)
        self.assertEqual(self.policy.snapshot()["presentCount"], 0)

    def test_lone_playing_phone_mute_arms_final_track_drain(self):
        lease = self.attach(muted=False)
        self.monitor.set("phone-audio")
        self.policy._tick()
        self.heartbeat(lease, muted=True)
        self.policy._tick()
        self.assertEqual(self.mpd.commands, ["repeat 0", "single oneshot"])
        self.assertTrue(self.policy.snapshot()["pendingFinalStop"])
        self.assertFalse(self.policy.snapshot()["autoPaused"])

    def test_output_failure_and_recovery_preserve_unmuted_choice(self):
        lease = self.attach(muted=False)
        self.monitor.set("phone-audio")
        self.policy._tick()
        self.heartbeat(lease, muted=False, ready=False)
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "pause")
        self.assertFalse(self.registry.snapshot(self.monitor.snapshot())["controllers"][0]["outputMuted"])
        self.heartbeat(lease, muted=False, ready=True, sequence=2)
        self.policy._tick()
        self.assertEqual(self.mpd.commands, ["pause 1", "play"])

    def test_failed_final_node_departure_write_is_retried(self):
        lease = self.attach(muted=False)
        self.monitor.set("phone-audio")
        self.policy._tick()
        self.detach(lease)
        with patch.object(self.mpd, "run", side_effect=OSError("offline")):
            self.policy._tick()
        self.assertFalse(self.policy.snapshot()["pendingFinalStop"])
        self.policy._tick()
        self.assertTrue(self.policy.snapshot()["pendingFinalStop"])

    def test_manual_stop_cannot_be_undone_by_controller_unmute(self):
        lease = self.pause_for_unavailable_phone()
        self.policy.explicit_transport(self.mpd)
        self.mpd.command("stop")
        self.heartbeat(lease, muted=False)
        self.monitor.set("phone-audio")
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "stop")
        self.assertNotIn("play", self.mpd.commands)

    def test_muted_phone_return_does_not_cancel_final_track_drain(self):
        lease = self.attach(muted=False)
        self.monitor.set("phone-audio")
        self.policy._tick()
        self.detach(lease)
        self.policy._tick()
        self.assertTrue(self.policy.snapshot()["pendingFinalStop"])
        self.attach()
        self.policy._tick()
        self.assertTrue(self.policy.snapshot()["pendingFinalStop"])
        self.assertFalse(self.policy.snapshot()["autoPaused"])
        self.assertEqual(self.mpd.state_data["songId"], 42)
        self.assertEqual(self.mpd.state_data["elapsedSeconds"], 90.5)
        self.assertNotIn("clear", self.mpd.commands)

    def test_audible_controller_returns_before_boundary_preserves_playing_queue(self):
        self.monitor.set("radio")
        self.policy._tick()
        self.monitor.set("radio", present=False)
        self.policy._tick()
        original = list(self.mpd.queue_files)
        self.attach(muted=False)
        self.monitor.set("phone-audio")
        self.policy._tick()
        self.assertFalse(self.policy.snapshot()["pendingFinalStop"])
        self.assertEqual(self.mpd.state_data["transport"], "play")
        self.assertEqual(self.mpd.queue_files, original)
        self.assertEqual(self.mpd.state_data["elapsedSeconds"], 90.5)

    def test_controller_after_boundary_does_not_resurrect_queue_or_autostart(self):
        for boundary in ("pause", "stop"):
            with self.subTest(boundary=boundary):
                self.setUp()
                self.monitor.set("radio")
                self.policy._tick()
                self.monitor.set("radio", present=False)
                self.policy._tick()
                self.mpd.state_data.update(transport=boundary, songId=99, elapsedSeconds=0)
                self.attach(muted=False)
                self.monitor.set("phone-audio")
                self.policy._tick()
                self.assertEqual(self.mpd.state_data["transport"], "stop")
                self.shuffle.assert_not_called()
                self.assertNotIn("play", self.mpd.commands)

    def test_renderer_audio_continues_after_control_loss_then_drains_when_output_leaves(self):
        self.attach(muted=False)
        self.monitor.set("phone-audio")
        self.policy._tick()
        self.clock.advance(16)
        self.policy._tick()
        self.assertEqual(self.mpd.commands, [])
        self.monitor.set("phone-audio", present=False)
        self.policy._tick()
        self.assertTrue(self.policy.snapshot()["pendingFinalStop"])

    def test_snapserver_outage_does_not_pause_from_missing_renderer_evidence(self):
        self.attach()
        self.monitor.set("radio")
        self.policy._tick()
        self.monitor.reachable = False
        self.monitor.nodes.clear()
        self.policy._tick()
        self.assertEqual(self.mpd.commands, [])

    def test_failed_pause_and_last_unmuted_stop_retry_on_next_poll(self):
        lease = self.attach(muted=False)
        self.monitor.set("radio")
        self.policy._tick()
        self.monitor.set("radio", present=False)
        with patch.object(self.mpd, "command", side_effect=OSError("offline")):
            self.policy._tick()
        self.policy._tick()
        self.assertTrue(self.policy.snapshot()["autoPaused"])
        self.detach(lease)
        with patch.object(self.mpd, "command", side_effect=OSError("offline")):
            self.policy._tick()
        self.assertTrue(self.policy.snapshot()["autoPaused"])
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "stop")


class ControllerHttpTests(ControllerFixture):
    def setUp(self):
        super().setUp()
        # These HTTP cases exercise an established session after startup.
        startup = h.MpdStartupBoundary()
        startup.ensure_ready(FakeMpd())
        for name, value in (("CONTROLLERS", self.registry), ("SNAPCAST_MONITOR", self.monitor),
                            ("PASSIVE_SESSION_POLICY", self.policy), ("MPD_STARTUP", startup)):
            patcher = patch.object(h, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        log = patch.object(h.ApiHandler, "log_message")
        log.start()
        self.addCleanup(log.stop)
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

    def test_registration_heartbeat_and_detach_do_not_call_mpd(self):
        with patch.object(h, "MpdClient", side_effect=AssertionError("Presence must not directly command MPD")):
            status, lease = self.request("/controllers/attach", {"controllerId": "phone", "rendererId": "phone-audio"})
            self.assertEqual(status, 200)
            self.assertTrue(lease["controller"]["outputMuted"])
            payload = {"controllerId": "phone", "leaseId": lease["leaseId"], "sequence": 1, "outputMuted": False, "outputReady": True}
            self.assertEqual(self.request("/controllers/heartbeat", payload)[0], 200)
            self.assertEqual(self.request("/controllers/heartbeat", payload)[0], 409)
            status, body = self.request("/controllers")
            self.assertEqual(status, 200)
            self.assertEqual(body["presence"]["controllerCount"], 1)
            self.assertNotIn(lease["leaseId"], json.dumps(body))
            self.assertEqual(self.request("/controllers/detach", {"controllerId": "phone", "leaseId": lease["leaseId"]})[0], 200)
        self.assertEqual(self.mpd.commands, [])

    def test_bad_controller_requests_are_400(self):
        for payload in ({}, {"controllerId": "../phone"}, {"controllerId": "phone", "rendererId": []},
                        {"controllerId": "phone", "outputMuted": False}, {"controllerId": "phone", "outputMuted": 1},
                        {"controllerId": "phone", "extra": True}, []):
            self.assertEqual(self.request("/controllers/attach", payload)[0], 400)

    def test_removed_handoff_routes_cannot_modify_house_playback(self):
        # The independent-app baseline no longer ships ReturnHomeHandoffs.
        payload = {"controllerId": "phone", "handoffId": "arrival-1",
                   "tracks": ["Rap/A.mp3"], "startIndex": 0,
                   "positionSeconds": 47.125, "shuffle": False, "repeat": True}
        mpd = Mock(spec=h.MpdClient)
        with patch.object(h, "MpdClient", return_value=mpd):
            for action in ("prepare", "commit", "status", "cancel"):
                with self.subTest(action=action):
                    status, body = self.request("/session/handoff/" + action, payload)
                    self.assertEqual(status, 404)
                    self.assertEqual(body["error"], "not_found")
        self.assertEqual(mpd.mock_calls, [])

    def test_removed_diagnostics_route_is_not_advertised_as_available(self):
        with patch.object(h, "MpdClient", side_effect=AssertionError("Unexpected MPD access")):
            for path in ("/diagnostics", "/diagnostics?limit=50"):
                with self.subTest(path=path):
                    status, body = self.request(path)
                    self.assertEqual(status, 404)
                    self.assertEqual(body["error"], "not_found")

    def test_explicit_pause_via_http_prevents_auto_resume_but_passive_arrival_can_resume(self):
        lease = self.pause_for_unavailable_phone()
        with patch.object(h, "MpdClient", return_value=self.mpd):
            self.assertEqual(self.request("/pause", {})[0], 200)
        self.heartbeat(lease, muted=False)
        self.monitor.set("phone-audio")
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "pause")
        self.monitor.set("radio")
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "play")


if __name__ == "__main__":
    unittest.main()
