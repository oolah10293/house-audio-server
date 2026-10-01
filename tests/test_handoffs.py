import threading
import unittest
from unittest.mock import patch

import house_audio_server as h
import test_controllers as controller_tests
from test_controllers import NodeMonitor
from test_house_audio_server import FakeClock, FakeMpd


class HandoffMpd(FakeMpd):
    def __init__(self):
        super().__init__()
        self.state_data.update(transport="stop", queueVersion=3, songPosition=0)

    def library_files(self, folder):
        if folder == "":
            return [track for tracks in self.library.values() for track in tracks]
        return super().library_files(folder)

    def run(self, *commands):
        for command in commands:
            super().run(command)
            if command == "clear" or command.startswith("add "):
                self.state_data["queueVersion"] += 1
            if command.startswith("play "):
                self.state_data["songPosition"] = int(command.split()[1])
                self.state_data["songId"] = 100 + self.state_data["songPosition"]
                self.state_data["elapsedSeconds"] = 0
            if command.startswith("seekcur "):
                self.state_data["elapsedSeconds"] = float(command.split()[1])
        return "0.24.4", []


class HandoffFixture(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.monitor = NodeMonitor()
        self.controllers = h.ControllerRegistry(clock=self.clock)
        self.mpd = HandoffMpd()
        self.policy = h.PassiveSessionPolicy(self.monitor, controllers=self.controllers,
                                            mpd_factory=lambda: self.mpd)
        self.handoffs = h.ReturnHomeHandoffs(self.policy, clock=self.clock)
        self.key = {"controllerId": "phone", "handoffId": "arrival-1"}
        self.payload = {**self.key, "tracks": ["Rap/C.mp3", "MP3s/A.mp3", "Rap/A.mp3"],
                        "startIndex": 1, "positionSeconds": 47.125,
                        "shuffle": False, "repeat": True}
        self.lease = self.controllers.attach({"controllerId": "phone", "rendererId": "phone-audio"})

    def prepare(self):
        return self.handoffs.prepare(self.key, self.mpd)["handoff"]["status"]

    def status(self):
        return self.handoffs.status(self.key, self.mpd)["handoff"]["status"]

    def commit(self):
        return self.handoffs.commit(self.payload, self.mpd)["handoff"]["status"]


class HandoffTests(HandoffFixture):
    def test_transfer_keeps_queue_order_current_track_position_shuffle_repeat(self):
        self.monitor.set("radio")
        self.assertEqual(self.prepare(), "reserved")
        self.assertEqual(self.mpd.commands, [])
        self.assertEqual(self.commit(), "committed")
        self.assertEqual(self.mpd.queue_files, self.payload["tracks"])
        self.assertEqual(self.mpd.state_data["songPosition"], 1)
        self.assertEqual(self.mpd.state_data["elapsedSeconds"], 47.125)
        self.assertFalse(self.mpd.state_data["random"])
        self.assertTrue(self.mpd.state_data["repeat"])
        self.assertEqual(self.mpd.state_data["transport"], "play")
        self.assertFalse(self.mpd.state_data["consume"])
        self.assertEqual(self.mpd.state_data["singleMode"], "0")

    def test_arriving_passive_cannot_start_default_during_or_after_transfer(self):
        self.policy._tick()
        self.prepare()
        self.monitor.set("radio")
        for _ in range(3):
            self.policy._tick()
        self.assertEqual(self.mpd.commands, [])
        self.commit()
        self.policy._tick()
        self.assertEqual(self.mpd.commands.count("clear"), 1)
        self.assertEqual(self.mpd.queue_files, self.payload["tracks"])
        self.assertEqual(self.mpd.state_data["elapsedSeconds"], 47.125)

    def test_existing_play_or_retained_pause_is_never_replaced(self):
        for state in ("play", "pause"):
            with self.subTest(state=state):
                self.setUp()
                self.mpd.state_data["transport"] = state
                before = self.mpd.state()
                self.assertEqual(self.prepare(), "adopt_existing")
                self.assertEqual(self.commit(), "adopt_existing")
                self.assertEqual(self.mpd.state(), before)
                self.assertEqual(self.mpd.commands, [])

    def test_muted_only_transfer_retains_pause_without_silent_play_exception(self):
        self.prepare()
        self.assertEqual(self.commit(), "committed")
        self.assertEqual(self.mpd.state_data["transport"], "pause")
        self.assertTrue(self.policy.snapshot()["autoPaused"])
        self.assertEqual(self.mpd.state_data["elapsedSeconds"], 47.125)
        self.monitor.set("radio")
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "play")
        self.assertEqual(self.mpd.commands.count("clear"), 1)

    def test_last_muted_controller_departure_ends_transferred_session(self):
        self.prepare()
        self.commit()
        self.controllers.detach({"controllerId": "phone", "leaseId": self.lease["leaseId"]})
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "stop")
        self.assertEqual(self.mpd.state_data["elapsedSeconds"], 47.125)
        self.monitor.set("radio")
        self.policy._tick()
        self.assertNotEqual(self.mpd.queue_files, self.payload["tracks"])

    def test_lost_ack_status_and_repeat_commit_never_replay_position(self):
        self.prepare()
        self.commit()
        count = len(self.mpd.commands)
        self.mpd.state_data["elapsedSeconds"] = 79.1
        self.assertEqual(self.status(), "committed")
        self.assertEqual(self.commit(), "committed")
        self.assertEqual(self.prepare(), "committed")
        self.assertEqual(len(self.mpd.commands), count)
        self.assertEqual(self.mpd.state_data["elapsedSeconds"], 79.1)

    def test_committed_receipt_survives_unreadable_mpd_state(self):
        self.monitor.set("radio")
        self.prepare()
        original = self.mpd.state
        reads = 0
        def state():
            nonlocal reads
            reads += 1
            if reads > 1:
                raise OSError("reply read lost")
            return original()
        with patch.object(self.mpd, "state", side_effect=state):
            result = self.handoffs.commit(self.payload, self.mpd)
        self.assertEqual(result["handoff"]["status"], "committed")
        self.assertIsNone(result["mpd"])
        self.assertIn("reply read lost", result["mpdError"])
        self.assertEqual(self.mpd.commands.count("clear"), 1)

    def test_prepare_renews_and_abandoned_reservation_expires(self):
        self.prepare()
        self.clock.advance(14)
        self.assertEqual(self.prepare(), "reserved")
        self.clock.advance(14)
        self.assertEqual(self.status(), "reserved")
        self.monitor.set("radio")
        self.policy._tick()
        self.assertEqual(self.mpd.commands, [])
        self.clock.advance(1)
        self.policy._tick()
        self.assertEqual(self.status(), "expired")
        self.assertEqual(self.commit(), "expired")
        self.assertEqual(self.mpd.state_data["transport"], "play")
        self.assertEqual(self.mpd.commands.count("clear"), 1)

    def test_second_controller_cannot_steal_reservation(self):
        self.prepare()
        with self.assertRaises(h.ApiError) as caught:
            self.handoffs.prepare({"controllerId": "other", "handoffId": "arrival-2"}, self.mpd)
        self.assertEqual(caught.exception.code, "handoff_in_progress")

    def test_manual_server_command_cancels_transfer_without_replay(self):
        self.prepare()
        self.handoffs.explicit_command()
        self.mpd.command("pause 1")
        self.assertEqual(self.commit(), "cancelled")
        self.assertEqual(self.mpd.commands, ["pause 1"])

    def test_cancel_is_idempotent_and_committed_cancel_preserves_shared_playback(self):
        self.prepare()
        self.handoffs.cancel(self.key, self.mpd)
        self.handoffs.cancel(self.key, self.mpd)
        self.assertEqual(self.commit(), "cancelled")
        self.assertEqual(self.mpd.commands, [])
        self.setUp()
        self.prepare()
        self.commit()
        commands = list(self.mpd.commands)
        self.handoffs.cancel(self.key, self.mpd)
        self.assertEqual(self.status(), "committed")
        self.assertEqual(self.mpd.commands, commands)

    def test_commit_requires_live_controller_but_never_claims_audibility(self):
        self.prepare()
        self.controllers.detach({"controllerId": "phone", "leaseId": self.lease["leaseId"]})
        with self.assertRaises(h.ApiError) as caught:
            self.commit()
        self.assertEqual(caught.exception.code, "handoff_controller_required")
        self.assertEqual(self.mpd.commands, [])

    def test_long_commit_keeps_existing_controller_present_until_next_heartbeat(self):
        self.prepare()
        original = self.mpd.run
        def slow(*commands):
            if commands[0] == "clear":
                self.clock.advance(20)
            return original(*commands)
        with patch.object(self.mpd, "run", side_effect=slow):
            self.assertEqual(self.commit(), "committed")
        # No passive radio and no client reattach/heartbeat has occurred. A
        # server-blocked lease must retain the queue, not turn it into stopped
        # idle just before Android can join the transferred session.
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "pause")
        self.assertEqual(self.mpd.state_data["elapsedSeconds"], 47.125)
        self.assertTrue(self.policy.snapshot()["autoPaused"])
        presence = self.controllers.snapshot(self.monitor.snapshot())
        self.assertEqual(presence["controllerCount"], 1)
        self.assertEqual(presence["audibleCount"], 0)
        self.assertTrue(presence["controllers"][0]["outputMuted"])
        self.assertFalse(presence["controllers"][0]["outputReady"])
        self.clock.advance(15)
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "stop")

    def test_handoff_lease_extension_never_revives_detach_or_replaces_new_lease(self):
        token = self.controllers.handoff_lease("phone")
        self.controllers.detach({"controllerId": "phone", "leaseId": token})
        self.controllers.renew_handoff_lease("phone", token)
        self.assertIsNone(self.controllers.handoff_lease("phone"))
        new = self.controllers.attach({"controllerId": "phone"})
        self.clock.advance(10)
        self.controllers.renew_handoff_lease("phone", token)
        self.clock.advance(5)
        self.assertIsNone(self.controllers.handoff_lease("phone"))

    def test_invalid_queue_or_settings_fail_before_mpd_writes(self):
        self.prepare()
        cases = [{"tracks": []}, {"tracks": ["Rap"]}, {"tracks": ["../outside.mp3"]},
                 {"tracks": ["Rap/A.mp3\nstop"]}, {"tracks": ["Missing.mp3"]},
                 {"startIndex": -1}, {"startIndex": True}, {"positionSeconds": float("nan")},
                 {"positionSeconds": float("inf")}, {"positionSeconds": -1},
                 {"positionSeconds": 10 ** 400},
                 {"shuffle": 1}, {"repeat": "yes"}]
        for changed in cases:
            with self.subTest(changed=changed):
                with self.assertRaises(h.ApiError):
                    self.handoffs.commit({**self.payload, **changed}, self.mpd)
                self.assertEqual(self.mpd.commands, [])

    def test_partial_write_failure_is_not_retried_and_blocks_default_until_cancel(self):
        self.prepare()
        original = self.mpd.run
        def broken(*commands):
            original(commands[0])
            raise OSError("connection broke after clear")
        with patch.object(self.mpd, "run", side_effect=broken):
            self.assertEqual(self.commit(), "failed")
        self.clock.advance(100)
        self.monitor.set("radio")
        self.policy._tick()
        self.assertEqual(self.status(), "failed")
        self.assertEqual(self.commit(), "failed")
        self.assertEqual(self.mpd.commands, ["clear"])
        self.handoffs.cancel(self.key, self.mpd)
        self.assertEqual(self.mpd.commands, ["clear", "stop"])
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "play")

    def test_failed_cancellation_retains_quarantine(self):
        self.prepare()
        with patch.object(self.mpd, "run", side_effect=OSError("offline")):
            self.assertEqual(self.commit(), "failed")
            with self.assertRaises(OSError):
                self.handoffs.cancel(self.key, self.mpd)
        self.assertTrue(self.handoffs.blocks_policy())

    def test_out_of_band_house_write_between_prepare_commit_is_adopted(self):
        self.prepare()
        self.mpd.state_data["queueVersion"] += 1
        self.assertEqual(self.commit(), "adopt_existing")
        self.assertEqual(self.mpd.commands, [])

    def test_unknown_after_restart_never_commits_without_reservation(self):
        self.prepare()
        self.commit()
        restarted = h.ReturnHomeHandoffs(self.policy, clock=self.clock)
        count = len(self.mpd.commands)
        self.assertEqual(restarted.status(self.key, self.mpd)["handoff"]["status"], "unknown")
        self.assertEqual(restarted.commit(self.payload, self.mpd)["handoff"]["status"], "unknown")
        self.assertEqual(len(self.mpd.commands), count)

    def test_completed_drain_boundary_is_idle_but_unfinished_drain_is_preserved(self):
        self.policy._pending_final_stop = True
        self.policy._pending_song_id = 42
        self.policy._saved_single_mode = "0"
        self.mpd.state_data.update(transport="play", songId=42)
        self.assertEqual(self.prepare(), "adopt_existing")
        self.key["handoffId"] = "arrival-2"
        self.mpd.state_data.update(transport="pause", songId=99)
        self.assertEqual(self.prepare(), "reserved")
        self.assertEqual(self.mpd.state_data["transport"], "stop")
        self.assertFalse(self.policy.snapshot()["pendingFinalStop"])


class HandoffHttpTests(HandoffFixture):
    request = controller_tests.ControllerHttpTests.request

    def setUp(self):
        super().setUp()
        startup = h.MpdStartupBoundary()
        startup.ensure_ready(FakeMpd())
        for name, value in (("CONTROLLERS", self.controllers), ("SNAPCAST_MONITOR", self.monitor),
                            ("PASSIVE_SESSION_POLICY", self.policy), ("MPD_STARTUP", startup),
                            ("RETURN_HOME_HANDOFFS", self.handoffs)):
            patcher = patch.object(h, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        logger = patch.object(h.ApiHandler, "log_message")
        logger.start()
        self.addCleanup(logger.stop)
        self.server = h.ThreadingHTTPServer(("127.0.0.1", 0), h.ApiHandler)
        worker = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        worker.start()
        self.addCleanup(worker.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def test_full_http_contract_and_explicit_pause_cancellation(self):
        key = {"controllerId": "phone", "handoffId": "arrival-1"}
        with patch.object(h, "MpdClient", return_value=self.mpd):
            self.assertEqual(self.request("/session/handoff/status", key)[1]["handoff"]["status"], "unknown")
            status, result = self.request("/session/handoff/prepare", key)
            self.assertEqual(status, 200)
            self.assertEqual(result["handoff"]["status"], "reserved")
            self.assertEqual(result["version"], "0.9.0")
            self.assertEqual(self.request("/pause", {})[0], 200)
            self.assertEqual(self.request("/session/handoff/status", key)[1]["handoff"]["status"], "cancelled")

    def test_handoff_requests_obey_startup_guard(self):
        with patch.object(h, "MPD_STARTUP", h.MpdStartupBoundary()):
            for operation in ("prepare", "commit", "status", "cancel"):
                status, body = self.request("/session/handoff/" + operation,
                                            {"controllerId": "phone", "handoffId": "arrival-1"})
                self.assertEqual(status, 503)
                self.assertEqual(body["error"], "startup_pending")

    def test_rejected_commands_preserve_reservation_and_failure_quarantine(self):
        for handoff_status in ("reserved", "failed"):
            with self.subTest(handoff_status=handoff_status):
                self.handoffs.records.clear()
                self.handoffs.active = None
                with patch.object(h, "MpdClient", return_value=self.mpd):
                    self.prepare()
                    if handoff_status == "failed":
                        with patch.object(self.mpd, "run", side_effect=OSError("partial write")):
                            self.assertEqual(self.commit(), "failed")
                    for path, payload in (("/shuffle", {"enabled": "bad"}),
                            ("/repeat", {"enabled": 1}), ("/seek", {"seconds": -1}),
                            ("/queue/replace", {"tracks": "bad"}),
                            ("/queue/reorder", {"songIds": []})):
                        self.assertEqual(self.request(path, payload)[0], 400)
                        self.assertEqual(self.status(), handoff_status)
                        self.assertTrue(self.handoffs.blocks_policy())
                real = h.MpdClient()
                with patch.object(real, "state", return_value={"queueVersion": 7}):
                    with self.assertRaises(h.ApiError):
                        real.reorder_queue([1], 6, before_write=self.handoffs.explicit_command)
                self.assertEqual(self.status(), handoff_status)
                self.assertEqual(self.mpd.commands, [])

    def test_long_commit_and_policy_arrival_serialize(self):
        key = {"controllerId": "phone", "handoffId": "arrival-1"}
        payload = {**key, "tracks": ["Rap/A.mp3"], "startIndex": 0,
                   "positionSeconds": 13.75, "shuffle": True, "repeat": False}
        reached_write, continue_write = threading.Event(), threading.Event()
        original = self.mpd.run
        def delayed(*commands):
            reached_write.set()
            self.assertTrue(continue_write.wait(2))
            return original(*commands)
        results = []
        with patch.object(h, "MpdClient", return_value=self.mpd):
            self.assertEqual(self.request("/session/handoff/prepare", key)[0], 200)
            self.monitor.set("radio")
            with patch.object(self.mpd, "run", side_effect=delayed):
                worker = threading.Thread(target=lambda: results.append(self.request("/session/handoff/commit", payload)))
                worker.start()
                self.assertTrue(reached_write.wait(2))
                self.clock.advance(20)
                policy_worker = threading.Thread(target=self.policy._tick)
                policy_worker.start()
                continue_write.set()
                worker.join(2)
                policy_worker.join(2)
                self.assertFalse(worker.is_alive())
                self.assertFalse(policy_worker.is_alive())
        self.assertEqual(results[0][1]["handoff"]["status"], "committed")
        self.assertEqual(self.mpd.commands.count("clear"), 1)
        self.assertEqual(self.mpd.queue_files, ["Rap/A.mp3"])
        self.assertEqual(self.mpd.state_data["elapsedSeconds"], 13.75)
