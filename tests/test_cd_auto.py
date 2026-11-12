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

    def test_eject_stops_old_stream_and_new_disc_starts_without_losing_bookmark(self):
        self.tick()
        token = self.radio.playlist_resume.token
        self.source.update(status="no_disc", tracks=[])
        self.tick()
        self.assertEqual(self.mpd.paths, [])
        self.assertEqual(self.mpd.values["transport"], "stop")
        self.assertEqual(self.radio.playlist_resume.token, token)
        for _ in range(3):
            self.tick()
        self.assertEqual(self.mpd.paths, [])
        self.source.update(status="ready", tracks=list(range(1, 9)), discRevision=2)
        self.tick()
        self.assertEqual(len(self.mpd.paths), 8)
        self.assertEqual(self.mpd.values["songPosition"], 0)
        self.assertEqual(self.mpd.values["transport"], "play")
        self.assertEqual(self.radio.playlist_resume.token, token)

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

    def test_ejected_then_unplugged_restores_original_library(self):
        self.tick()
        self.source.update(status="no_disc", tracks=[])
        self.tick()
        self.source["available"] = False
        self.tick()
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

    def test_eject_reports_no_disc_and_refuses_play(self):
        self.tick()
        self.source.update(status="no_disc", tracks=[])
        self.tick()
        state = self.radio.snapshot(self.mpd.state())
        self.assertEqual(state["status"], "no_disc")
        self.assertIsNone(state["input"]["trackNumber"])
        self.assertFalse(state["canSkip"])
        with self.assertRaises(h.ApiError) as error:
            self.radio.resume(self.mpd)
        self.assertEqual(error.exception.code, "cd_no_disc")

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
