"""New physical CD events, manual override, and unplug restoration.

Only modeled MPD/gateway behavior is verified here. No hardware audio claims.
"""
import copy
import tempfile
import unittest
from pathlib import Path

import house_audio_server as h
from auto_cd import AutoCdSource
from test_cd_inputs import CdMpdModel, FakeGateway, SOURCE
from test_controllers import NodeMonitor


class AutoCdSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stations = h.StationStore(str(Path(self.tmp.name) / "stations.json"))
        self.radio = h.RadioPlayback(self.stations)
        self.mpd = CdMpdModel()
        self.original = copy.deepcopy(self.mpd.paths)
        self.gateway = FakeGateway()
        self.source = dict(SOURCE, bootSession="abcdef0123456789", discRevision=1)
        self.gateway.records = [self.source]
        self.monitor = NodeMonitor()
        self.monitor.set("speaker")
        self.controllers = h.ControllerRegistry()
        self.now = [100.0]
        self.auto = AutoCdSource(self.gateway, clock=lambda: self.now[0], poll_seconds=0)
        self.policy = h.PassiveSessionPolicy(self.monitor, mpd_factory=lambda: self.mpd,
                                             controllers=self.controllers, radio=self.radio,
                                             auto_cd=self.auto)

    def tick(self):
        self.now[0] += 1
        self.policy._tick()

    def test_new_boot_autoplays_track_one_once_while_heartbeats_do_nothing(self):
        self.tick()
        self.assertEqual(self.auto.snapshot()["lastAction"], "cd_connected_autoplay")
        self.assertEqual(self.mpd.values["songPosition"], 0)
        self.assertEqual(len(self.mpd.paths), 11)
        self.assertTrue(self.radio.playlist_resume.view()["available"])
        commands = list(self.mpd.commands)
        for _ in range(7):
            self.tick()
        self.assertEqual(self.mpd.commands, commands)
        self.assertEqual(self.radio.station["sourceId"], SOURCE["sourceId"])

    def test_eject_restores_original_library_and_new_disc_retakes_cleanly(self):
        self.tick()
        old_token = self.radio.playlist_resume.token
        self.source.update(status="no_disc", tracks=[])
        self.tick()
        self.assertEqual(self.mpd.paths, self.original)
        self.assertEqual(self.mpd.values["transport"], "pause")
        self.assertEqual(self.mpd.values["songPosition"], 2)
        self.assertEqual(self.mpd.values["elapsedSeconds"], 47.125)
        self.assertIsNone(self.auto.active)
        self.assertIsNone(self.radio.station)
        self.assertEqual(self.auto.snapshot()["lastAction"], "cd_ejected_restored_library")
        self.assertFalse(self.radio.playlist_resume.view()["available"])
        old_commands = list(self.mpd.commands)
        for _ in range(3):
            self.tick()
        self.assertEqual(self.mpd.commands, old_commands)  # no heartbeat retake
        self.source.update(status="ready", tracks=list(range(1, 9)), discRevision=2)
        self.tick()
        self.assertEqual(len(self.mpd.paths), 8)
        self.assertEqual(self.mpd.values["songPosition"], 0)
        self.assertEqual(self.mpd.values["transport"], "play")
        self.assertEqual(self.radio.playlist_resume.saved["tracks"], self.original)
        self.assertNotEqual(self.radio.playlist_resume.token, old_token)

    def test_same_track_count_with_new_revision_is_still_new_disc(self):
        self.tick()
        self.mpd.apply("play 5")
        before = self.mpd.commands.count("play 0")
        self.source["discRevision"] = 2
        self.tick()
        self.assertGreater(self.mpd.commands.count("play 0"), before)
        self.assertEqual(self.mpd.values["songPosition"], 0)

    def test_removed_source_restores_original_library_occurrence_and_position(self):
        self.tick()
        self.source["available"] = False
        self.tick()
        self.assertEqual(self.mpd.paths, self.original)
        self.assertEqual(self.mpd.values["songPosition"], 2)
        self.assertEqual(self.mpd.values["elapsedSeconds"], 47.125)
        self.assertEqual(self.mpd.values["transport"], "play")
        self.assertIsNone(self.auto.active)
        self.tick()
        self.assertEqual(self.mpd.paths, self.original)

    def test_ejected_then_unplugged_does_not_restore_twice(self):
        self.tick()
        self.source.update(status="no_disc", tracks=[])
        self.tick()
        self.assertEqual(self.mpd.paths, self.original)
        self.assertIsNone(self.auto.active)
        commands = list(self.mpd.commands)
        self.source["available"] = False
        self.tick()
        self.assertEqual(self.mpd.commands, commands)
        self.assertEqual(self.mpd.paths, self.original)
        self.assertIsNone(self.auto.active)

    def test_manual_selection_wins_pending_cd_discovery(self):
        self.auto.refresh()  # event sampled but not applied
        station, _ = self.stations.add("https://radio.example/a", "Station A")
        self.auto.manual_source_choice()  # same action as the API source switch
        self.radio.select(self.mpd, station)
        self.tick()
        self.assertIsNone(self.auto.active)
        self.assertEqual(self.radio.station["id"], station["id"])
        self.source["discRevision"] = 2
        self.tick()
        self.assertEqual(self.radio.station["kind"], "cd")

    def test_manual_radio_selection_blocks_autoplay_until_new_disc(self):
        self.tick()
        station, _ = self.stations.add("https://radio.example/a", "Station A")
        self.radio.select(self.mpd, station)
        self.tick()
        self.assertIsNone(self.auto.active)
        self.assertEqual(self.radio.station["id"], station["id"])
        for _ in range(4):
            self.tick()
        self.assertEqual(self.radio.station["id"], station["id"])
        self.source["discRevision"] = 2
        self.tick()
        self.assertIsNotNone(self.auto.active)
        self.assertEqual(self.radio.station["kind"], "cd")
        self.source["available"] = False
        self.tick()
        self.assertEqual(self.radio.station["id"], station["id"])

    def test_radio_prior_to_first_cd_is_restored_on_unplug(self):
        station, _ = self.stations.add("https://radio.example/a", "Station A")
        self.radio.select(self.mpd, station)
        self.tick()
        self.assertEqual(self.radio.station["kind"], "cd")
        self.source["available"] = False
        self.tick()
        self.assertEqual(self.radio.station["id"], station["id"])
        self.assertEqual(self.mpd.paths, [station["url"]])

    def test_manual_resume_playlist_blocks_heartbeat_retake(self):
        self.tick()
        token = self.radio.playlist_resume.token
        self.radio.resume_playlist(self.mpd, token)
        self.tick()
        self.assertIsNone(self.auto.active)
        self.assertEqual(self.mpd.paths, self.original)
        for _ in range(3):
            self.tick()
        self.assertEqual(self.mpd.paths, self.original)
        self.source.update(discRevision=2)
        self.tick()
        self.assertEqual(self.radio.station["kind"], "cd")

    def test_manual_pause_and_stop_not_overridden_by_heartbeats(self):
        self.tick()
        self.radio.pause(self.mpd)
        for _ in range(3):
            self.tick()
        self.assertEqual(self.mpd.values["transport"], "pause")
        self.radio.stop(self.mpd)
        for _ in range(3):
            self.tick()
        self.assertEqual(self.mpd.values["transport"], "stop")

    def test_no_speakers_pauses_cd_until_audible_node_returns(self):
        self.tick()
        self.monitor.nodes.clear()
        self.tick()
        self.assertEqual(self.radio.station["kind"], "cd")
        self.assertEqual(self.mpd.values["transport"], "pause")
        self.assertTrue(self.radio.auto_paused)
        self.monitor.set("speaker")
        self.tick()
        self.assertEqual(self.mpd.values["transport"], "play")

    def test_unplug_with_only_muted_controller_restores_library_then_ends_session(self):
        self.monitor.nodes.clear()
        self.monitor.set("phone-out")
        self.controllers.attach({"controllerId": "phone",
                                 "rendererId": "phone-out",
                                 "outputMuted": True,
                                 "outputReady": True})
        self.tick()
        self.assertEqual(self.radio.station["kind"], "cd")
        self.assertEqual(self.mpd.values["transport"], "pause")
        self.source["available"] = False
        self.tick()
        self.assertEqual(self.mpd.paths, self.original)
        self.assertEqual(self.mpd.values["transport"], "stop")
        self.assertFalse(self.policy.snapshot()["autoPaused"])
        self.assertEqual(self.policy.snapshot()["controllerCount"], 1)
        self.assertEqual(self.policy.snapshot()["presentCount"], 0)

    def test_manual_pause_does_not_resume_on_new_speaker(self):
        self.tick()
        self.radio.pause(self.mpd)
        self.monitor.set("new-speaker")
        self.tick()
        self.assertEqual(self.mpd.values["transport"], "pause")

    def test_gateway_outage_does_not_count_as_power_off(self):
        self.tick()
        def offline():
            raise h.InputGatewayError("gateway transient timeout")
        saved = self.gateway.sources
        self.gateway.sources = offline
        self.tick()
        self.assertIsNotNone(self.auto.active)
        self.assertEqual(self.radio.station["kind"], "cd")
        self.gateway.sources = saved
        self.tick()
        self.assertIsNotNone(self.auto.active)

    def test_temporary_empty_gateway_registry_does_not_steal_cd(self):
        self.tick()
        self.gateway.records = []
        self.tick()
        self.assertIsNotNone(self.auto.active)
        self.assertEqual(self.radio.station["kind"], "cd")
        self.gateway.records = [self.source]
        self.tick()
        self.assertIsNotNone(self.auto.active)
        self.gateway.records = []
        self.tick()
        self.now[0] += 19
        self.tick()
        self.assertIsNone(self.auto.active)
        self.assertEqual(self.mpd.paths, self.original)

    def test_legacy_firmware_cannot_trigger_unidentified_autoplay(self):
        self.source.pop("bootSession")
        self.source.pop("discRevision")
        for _ in range(5):
            self.tick()
        self.assertIsNone(self.radio.station)
        self.assertEqual(self.mpd.paths, self.original)

    def test_new_boot_nonce_can_reactivate_after_manual_override(self):
        self.tick()
        self.radio.release(self.mpd, clear=True)
        self.tick()
        self.assertIsNone(self.auto.active)
        self.source["bootSession"] = "0123456789abcdef"
        self.source["discRevision"] = 1
        self.tick()
        self.assertEqual(self.radio.station["kind"], "cd")

    def test_eject_releases_cd_controls_and_restores_library(self):
        self.tick()
        self.source.update(status="no_disc", tracks=[])
        self.tick()
        state = self.radio.snapshot(self.mpd.state())
        self.assertEqual(state["type"], "library")
        self.assertEqual(self.mpd.paths, self.original)
        self.assertIsNone(self.auto.active)
        self.assertIsNone(self.radio.station)

    def test_eject_restores_previous_radio_without_retake_on_heartbeat(self):
        station, _ = self.stations.add("https://radio.example/a", "Station A")
        self.radio.select(self.mpd, station)
        self.tick()
        self.assertEqual(self.radio.station["kind"], "cd")
        self.source.update(status="no_disc", tracks=[])
        self.tick()
        self.assertEqual(self.radio.station["id"], station["id"])
        self.assertEqual(self.mpd.paths, [station["url"]])
        self.assertEqual(self.mpd.values["transport"], "stop")
        self.assertEqual(self.radio.intent, "pause")
        self.assertEqual(self.auto.snapshot()["lastAction"], "cd_ejected_returned_to_radio")
        for _ in range(3):
            self.tick()
        self.assertEqual(self.radio.station["id"], station["id"])
        self.source.update(status="ready", tracks=[1, 2], discRevision=2)
        self.tick()
        self.assertEqual(self.radio.station["kind"], "cd")

    def test_eject_restores_previous_library_paused_without_resuming(self):
        self.mpd.apply("pause 1")
        self.tick()
        self.source.update(status="no_disc", tracks=[])
        self.tick()
        self.assertEqual(self.mpd.paths, self.original)
        self.assertEqual(self.mpd.values["transport"], "pause")
        self.assertEqual(self.mpd.values["elapsedSeconds"], 47.125)

    def test_two_ejects_keep_original_library_position_paused(self):
        self.tick()
        self.source.update(status="no_disc", tracks=[])
        self.tick()
        self.assertEqual(self.mpd.values["transport"], "pause")
        self.source.update(status="ready", tracks=[1, 2], discRevision=2)
        self.tick()
        self.assertEqual(self.mpd.values["transport"], "play")
        self.assertEqual(self.mpd.values["songPosition"], 0)
        self.source.update(status="no_disc", tracks=[])
        self.tick()
        self.assertEqual(self.mpd.paths, self.original)
        self.assertEqual(self.mpd.values["transport"], "pause")
        self.assertEqual(self.mpd.values["songPosition"], 2)
        self.assertEqual(self.mpd.values["elapsedSeconds"], 47.125)
        self.assertIsNone(self.auto.active)

    def test_passive_arrival_does_not_resume_eject_restored_library_until_play(self):
        self.tick()
        self.source.update(status="no_disc", tracks=[])
        self.tick()
        self.assertEqual(self.mpd.values["transport"], "pause")
        before = list(self.mpd.commands)
        self.monitor.set("second-speaker")
        self.tick()
        self.assertEqual(self.mpd.values["transport"], "pause")
        self.assertEqual(self.mpd.commands, before)
        self.policy.explicit_transport(self.mpd)  # same explicit-Play hook as HTTP
        self.mpd.command("play")
        self.tick()
        self.assertEqual(self.mpd.values["transport"], "play")
        self.assertEqual(self.mpd.values["elapsedSeconds"], 47.125)

    def test_eject_restored_radio_does_not_start_even_briefly(self):
        station, _ = self.stations.add("https://radio.example/b", "Station B")
        self.radio.select(self.mpd, station)
        self.tick()
        before = len(self.mpd.commands)
        self.source.update(status="no_disc", tracks=[])
        self.tick()
        self.assertEqual(self.radio.station["id"], station["id"])
        self.assertEqual(self.radio.intent, "pause")
        self.assertEqual(self.mpd.values["transport"], "stop")
        self.assertNotIn("play 0", self.mpd.commands[before:])
        self.monitor.set("second-speaker")
        self.tick()
        self.assertEqual(self.mpd.values["transport"], "stop")
        self.policy.explicit_transport(self.mpd)
        self.radio.resume(self.mpd)
        self.tick()
        self.assertEqual(self.mpd.values["transport"], "play")

    def test_eject_with_no_nodes_clears_and_next_passive_starts_fresh(self):
        self.mpd.indexed.add("MP3s/Second.mp3")
        self.policy._shuffle = lambda paths: paths.reverse()
        self.tick()
        self.monitor.nodes.clear()
        self.tick()
        self.assertEqual(self.radio.station["kind"], "cd")
        self.assertEqual(self.mpd.values["transport"], "pause")
        self.source.update(status="no_disc", tracks=[])
        self.tick()
        self.assertIsNone(self.auto.active)
        self.assertEqual(self.auto.snapshot()["lastAction"], "cd_ejected_session_idle")
        self.assertEqual(self.mpd.paths, [])
        self.assertEqual(self.mpd.values["transport"], "stop")
        self.assertFalse(self.radio.playlist_resume.view()["available"])
        self.monitor.set("speaker")
        self.tick()
        self.assertEqual(self.mpd.paths, ["MP3s/Second.mp3", "MP3s/Default.mp3"])
        self.assertEqual(self.mpd.values["transport"], "play")
        self.assertTrue(self.mpd.values["random"])
        self.assertTrue(self.mpd.values["repeat"])
        self.assertIsNone(self.radio.station)

    def test_eject_with_only_muted_controller_clears_instead_of_resuming(self):
        self.monitor.nodes.clear()
        self.monitor.set("phone-out")
        self.controllers.attach({"controllerId": "phone",
                                 "rendererId": "phone-out",
                                 "outputMuted": True,
                                 "outputReady": True})
        self.tick()
        self.source.update(status="no_disc", tracks=[])
        self.tick()
        self.assertEqual(self.policy.snapshot()["presentCount"], 0)
        self.assertEqual(self.auto.snapshot()["lastAction"], "cd_ejected_session_idle")
        self.assertEqual(self.mpd.paths, [])
        self.assertFalse(self.radio.playlist_resume.view()["available"])

    def test_unreachable_presence_is_not_assumed_to_be_zero_on_eject(self):
        self.tick()
        self.monitor.nodes.clear()
        self.monitor.reachable = False
        self.source.update(status="no_disc", tracks=[])
        self.tick()
        self.assertEqual(self.mpd.paths, self.original)
        self.assertEqual(self.mpd.values["transport"], "pause")
        self.assertEqual(self.mpd.values["elapsedSeconds"], 47.125)

    def test_manual_source_override_before_eject_is_not_replaced(self):
        self.tick()
        station, _ = self.stations.add("https://radio.example/override", "Override")
        self.radio.select(self.mpd, station)
        self.tick()
        self.assertIsNone(self.auto.active)
        self.source.update(status="no_disc", tracks=[])
        self.tick()
        self.assertEqual(self.radio.station["id"], station["id"])
        self.assertEqual(self.mpd.values["transport"], "play")

    def test_eject_with_no_prior_queue_releases_cd_and_stops(self):
        self.mpd.apply("stop")
        self.mpd.apply("clear")
        self.tick()
        self.source.update(status="no_disc", tracks=[])
        self.tick()
        self.assertIsNone(self.auto.active)
        self.assertIsNone(self.radio.station)
        self.assertEqual(self.mpd.paths, [])
        self.assertEqual(self.mpd.values["transport"], "stop")
        self.assertEqual(self.auto.snapshot()["lastAction"], "cd_ejected_stopped")

    def test_other_transient_not_ready_keeps_existing_handling(self):
        self.tick()
        self.source.update(status="scanning", tracks=[])
        self.tick()
        self.assertIsNotNone(self.auto.active)
        self.assertEqual(self.auto.snapshot()["lastAction"], "cd_ejected_waiting_for_disc")
        self.assertEqual(self.mpd.paths, [])

    def test_previously_stopped_library_stays_stopped_when_unplugged(self):
        self.mpd.apply("stop")
        self.tick()
        self.source["available"] = False
        self.tick()
        self.assertEqual(self.mpd.paths, self.original)
        self.assertEqual(self.mpd.values["transport"], "stop")

    def test_previous_library_paused_remains_paused_after_disconnect(self):
        self.mpd.apply("pause 1")
        self.tick()
        self.source["available"] = False
        self.tick()
        self.assertEqual(self.mpd.paths, self.original)
        self.assertEqual(self.mpd.values["transport"], "pause")

    def test_unplug_with_no_speakers_restores_library_paused(self):
        self.tick()
        self.monitor.nodes.clear()
        self.tick()
        self.source["available"] = False
        self.tick()
        self.assertEqual(self.mpd.paths, self.original)
        self.assertEqual(self.mpd.values["transport"], "pause")

    def test_empty_previous_queue_stops_cleanly_when_unplugged(self):
        self.mpd.apply("stop")
        self.mpd.apply("clear")
        self.tick()
        self.source["available"] = False
        self.tick()
        self.assertIsNone(self.radio.station)
        self.assertEqual(self.mpd.paths, [])
        self.assertEqual(self.mpd.values["transport"], "stop")


if __name__ == "__main__":
    unittest.main()
