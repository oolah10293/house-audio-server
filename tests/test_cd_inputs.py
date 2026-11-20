"""CD input contract and playlist-safety tests, without touching a real Pi.

The MPD model is the one used by the existing radio playlist-restore suite.
Physical MPD HTTP/WAV decoding and synchronized Snapcast sound require field tests.
"""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import house_audio_server as h
from input_sources import InputGateway, InputGatewayError
from test_controllers import NodeMonitor
from test_playlist_resume import MpdModel


SOURCE = {
    "sourceId": "cd-7452ed8dbd44", "name": "LiteOn CD Player", "kind": "cd",
    "ip": "192.168.1.175", "available": True, "status": "ready",
    "tracks": list(range(1, 12)),
}


class FakeGateway(InputGateway):
    def __init__(self):
        super().__init__()
        self.records = [dict(SOURCE)]

    def sources(self):
        return self.records


class CdMpdModel(MpdModel):
    def apply(self, command):
        if command == "next":
            index = self.values["songPosition"]
            if index + 1 < len(self.paths):
                self.values.update(songPosition=index + 1, elapsedSeconds=0, transport="play")
            else:
                self.values["transport"] = "stop"
            return
        if command == "previous":
            index = self.values["songPosition"]
            self.values.update(songPosition=max(0, index - 1),
                               elapsedSeconds=0, transport="play")
            return
        return super().apply(command)


class CdInputValidationTests(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()

    def test_constructs_only_local_gateway_urls_and_sorted_disc_queue(self):
        self.gateway.records[0]["tracks"] = [3, 1, 2]
        station = self.gateway.cd_selection(SOURCE["sourceId"], 2)
        self.assertEqual(station["trackNumbers"], [1, 2, 3])
        self.assertEqual(station["trackIndex"], 1)
        self.assertEqual(station["urls"], [
            "http://127.0.0.1:8768/audio/cd-7452ed8dbd44/1.wav",
            "http://127.0.0.1:8768/audio/cd-7452ed8dbd44/2.wav",
            "http://127.0.0.1:8768/audio/cd-7452ed8dbd44/3.wav",
        ])

    def test_rejects_invalid_id_track_and_offline_device(self):
        for sid, number in (("http://evil/", 1), (SOURCE["sourceId"], "1"),
                            (SOURCE["sourceId"], True), (SOURCE["sourceId"], 99)):
            with self.subTest(sid=sid, number=number), self.assertRaises((InputGatewayError, ValueError)):
                self.gateway.cd_selection(sid, number)
        self.gateway.records[0]["available"] = False
        with self.assertRaises(InputGatewayError):
            self.gateway.cd_selection(SOURCE["sourceId"], 1)

    def test_rejects_duplicate_or_malformed_track_lists(self):
        for tracks in ([1, 1], [0, 1], ["1"], [], list(range(1, 101))):
            self.gateway.records[0]["tracks"] = tracks
            with self.subTest(tracks=tracks), self.assertRaises(InputGatewayError):
                self.gateway.cd_selection(SOURCE["sourceId"], 1)


class CdPlaybackHttpTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        stations = h.StationStore(str(Path(tmp.name) / "stations.json"))
        self.radio = h.RadioPlayback(stations)
        self.mpd = CdMpdModel()
        self.original = list(self.mpd.paths)
        self.gateway = FakeGateway()
        self.monitor = NodeMonitor()
        self.monitor.set("speaker")
        self.registry = h.ControllerRegistry()
        self.policy = h.PassiveSessionPolicy(self.monitor, mpd_factory=lambda: self.mpd,
                                             controllers=self.registry, radio=self.radio)
        self.startup = h.MpdStartupBoundary()
        self.startup._ready = True
        for name, value in (("RADIO", self.radio), ("RADIO_STATIONS", stations),
                            ("INPUT_GATEWAY", self.gateway),
                            ("CONTROLLERS", self.registry), ("SNAPCAST_MONITOR", self.monitor),
                            ("PASSIVE_SESSION_POLICY", self.policy), ("MPD_STARTUP", self.startup),
                            ("MpdClient", lambda: self.mpd)):
            item = patch.object(h, name, value)
            item.start()
            self.addCleanup(item.stop)
        logging = patch.object(h.ApiHandler, "log_message")
        logging.start()
        self.addCleanup(logging.stop)
        self.server = h.ThreadingHTTPServer(("127.0.0.1", 0), h.ApiHandler)
        thread = threading.Thread(target=self.server.serve_forever,
                                  kwargs={"poll_interval": 0.005})
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, url, payload=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            connection.request("GET" if payload is None else "POST", url,
                               json.dumps(payload) if payload is not None else None,
                               {"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def play_cd(self, number=3):
        status, data = self.request("/inputs/play", {"sourceId": SOURCE["sourceId"], "trackNumber": number})
        self.assertEqual(status, 200, data)
        return data

    def test_disc_selection_preserves_library_snapshot_and_lists_all_tracks(self):
        status, listing = self.request("/inputs")
        self.assertEqual(status, 200)
        self.assertEqual(listing["sources"][0]["name"], "LiteOn CD Player")
        result = self.play_cd(3)
        self.assertEqual(result["source"]["type"], "cd")
        self.assertEqual(result["source"]["input"]["trackNumber"], 3)
        self.assertEqual(self.mpd.values["songPosition"], 2)
        self.assertEqual(len(self.mpd.paths), 11)
        self.assertTrue(all(url.startswith("http://127.0.0.1:8768/audio/") for url in self.mpd.paths))
        self.assertTrue(result["source"]["resumePlaylist"]["available"])
        self.assertEqual(self.radio.playlist_resume.saved["tracks"], self.original)
        self.assertEqual(self.radio.playlist_resume.saved["positionSeconds"], 47.125)

    def test_next_previous_and_same_disc_selection_keep_original_bookmark(self):
        first = self.play_cd(3)
        token = first["source"]["resumePlaylist"]["snapshotId"]
        before_next = len(self.mpd.commands)
        self.assertEqual(self.request("/next", {})[0], 200)
        self.assertEqual(self.mpd.commands[before_next:], ["stop", "play 3"])
        self.assertEqual(self.request("/state")[1]["source"]["input"]["trackNumber"], 4)
        before_previous = len(self.mpd.commands)
        self.assertEqual(self.request("/previous", {})[0], 200)
        self.assertEqual(self.mpd.commands[before_previous:], ["stop", "play 2"])
        self.assertEqual(self.request("/state")[1]["source"]["input"]["trackNumber"], 3)
        before = self.mpd.commands.count("clear")
        selected = self.play_cd(6)
        self.assertEqual(selected["source"]["input"]["trackNumber"], 6)
        self.assertEqual(selected["source"]["resumePlaylist"]["snapshotId"], token)
        self.assertEqual(self.mpd.commands.count("clear"), before)

    def test_cd_previous_on_first_track_restarts_without_native_seek(self):
        first = self.play_cd(1)
        token = first["source"]["resumePlaylist"]["snapshotId"]
        self.mpd.values["elapsedSeconds"] = 22.0
        before = len(self.mpd.commands)
        self.assertEqual(self.request("/previous", {})[0], 200)
        self.assertEqual(self.mpd.commands[before:], ["stop", "play 0"])
        state = self.request("/state")[1]
        self.assertEqual(state["source"]["input"]["trackNumber"], 1)
        self.assertEqual(self.mpd.values["songPosition"], 0)
        self.assertEqual(self.mpd.values["elapsedSeconds"], 0)
        self.assertEqual(self.mpd.values["transport"], "play")
        self.assertEqual(state["source"]["resumePlaylist"]["snapshotId"], token)

    def test_cd_next_on_last_track_still_stops_without_wrapping(self):
        first = self.play_cd(1)
        token = first["source"]["resumePlaylist"]["snapshotId"]
        self.play_cd(11)
        before = len(self.mpd.commands)
        self.assertEqual(self.request("/next", {})[0], 200)
        self.assertEqual(self.mpd.commands[before:], ["next"])
        self.assertEqual(self.mpd.values["transport"], "stop")
        self.assertEqual(self.radio.playlist_resume.view()["snapshotId"], token)

    def test_cd_skip_preserves_native_mpd_behavior_outside_normal_playback(self):
        self.play_cd(3)
        self.assertEqual(self.request("/pause", {})[0], 200)
        before = len(self.mpd.commands)
        self.assertEqual(self.request("/next", {})[0], 200)
        self.assertEqual(self.mpd.commands[before:], ["next"])

        self.mpd.values["random"] = True  # unexpected external MPD mode
        before = len(self.mpd.commands)
        self.assertEqual(self.request("/previous", {})[0], 200)
        self.assertEqual(self.mpd.commands[before:], ["previous"])

    def test_switching_an_active_cd_track_stops_old_wav_before_play(self):
        first = self.play_cd(3)
        token = first["source"]["resumePlaylist"]["snapshotId"]
        previous = len(self.mpd.commands)
        selected = self.play_cd(6)
        self.assertEqual(self.mpd.commands[previous:], ["stop", "play 5"])
        self.assertEqual(self.mpd.values["transport"], "play")
        self.assertEqual(self.mpd.values["songPosition"], 5)
        self.assertEqual(selected["source"]["input"]["trackNumber"], 6)
        self.assertEqual(selected["source"]["resumePlaylist"]["snapshotId"], token)

    def test_pause_resume_and_stop_do_not_restart_cd_from_beginning(self):
        self.play_cd(3)
        self.mpd.values["elapsedSeconds"] = 50
        self.assertEqual(self.request("/pause", {})[0], 200)
        self.assertEqual(self.mpd.values["elapsedSeconds"], 50)
        self.assertEqual(self.request("/play", {})[0], 200)
        self.assertEqual(self.mpd.values["elapsedSeconds"], 50)
        self.assertEqual(self.request("/stop", {})[0], 200)
        self.assertEqual(self.mpd.values["transport"], "stop")
        self.assertEqual(self.request("/seek", {"seconds": 1.0})[0], 409)

    def test_saved_library_restored_after_cd_via_existing_resume_contract(self):
        result = self.play_cd()
        token = result["source"]["resumePlaylist"]["snapshotId"]
        status, restored = self.request("/inputs/resume-playlist", {"snapshotId": token})
        self.assertEqual(status, 200, restored)
        self.assertEqual(self.mpd.paths, self.original)
        self.assertEqual(self.mpd.values["songPosition"], 2)
        self.assertEqual(self.mpd.values["elapsedSeconds"], 47.125)
        self.assertTrue(self.mpd.values["random"])
        self.assertEqual(restored["source"]["type"], "library")
        before = list(self.mpd.commands)
        self.assertEqual(self.request("/inputs/resume-playlist", {"snapshotId": token})[0], 200)
        self.assertEqual(self.mpd.commands, before)

    def test_switching_radio_and_cd_keeps_one_original_library_bookmark(self):
        radio_station, _ = self.radio.stations.add(
            "https://radio.example/modern-rock", "Modern Rock", generated_name="Modern Rock")
        cd = self.play_cd(1)
        token = cd["source"]["resumePlaylist"]["snapshotId"]
        status, result = self.request("/radio/play", {"stationId": radio_station["id"]})
        self.assertEqual(status, 200, result)
        self.assertEqual(result["source"]["type"], "radio")
        self.assertEqual(result["source"]["resumePlaylist"]["snapshotId"], token)
        again = self.play_cd(4)
        self.assertEqual(again["source"]["resumePlaylist"]["snapshotId"], token)
        self.assertEqual(self.request("/inputs/resume-playlist", {"snapshotId": token})[0], 200)
        self.assertEqual(self.mpd.paths, self.original)
        self.assertEqual(self.mpd.values["elapsedSeconds"], 47.125)

    def test_cd_playlist_resume_respects_muted_only_controller(self):
        token = self.play_cd()["source"]["resumePlaylist"]["snapshotId"]
        self.monitor.nodes.clear()
        self.registry.attach({"controllerId": "phone", "rendererId": "phone-out"})
        status, data = self.request("/inputs/resume-playlist", {"snapshotId": token})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["mpd"]["transport"], "pause")
        self.assertTrue(self.policy.snapshot()["autoPaused"])
        self.assertEqual(self.mpd.paths, self.original)

    def test_cd_track_click_recovers_from_orphaned_cd_url_queue(self):
        # MPD keeps its queue across a House Music process restart, while
        # RadioPlayback loses in-memory CD ownership. Clicking a track must
        # replace the old stream, not attempt to bookmark its URL as a file.
        stale = self.gateway.cd_selection(SOURCE["sourceId"], 1)["url"]
        self.mpd.paths = [stale]
        self.mpd.ids = [104]
        self.mpd.values.update(songPosition=0, elapsedSeconds=12.0,
                               queueVersion=2, transport="play")
        status, result = self.request("/inputs/play", {
            "sourceId": SOURCE["sourceId"], "trackNumber": 4})
        self.assertEqual(status, 200, result)
        self.assertEqual(result["source"]["type"], "cd")
        self.assertEqual(self.mpd.values["songPosition"], 3)
        self.assertEqual(self.mpd.paths, self.gateway.cd_selection(SOURCE["sourceId"], 4)["urls"])
        self.assertFalse(result["source"]["resumePlaylist"]["available"])

    def test_cd_track_click_skips_mixed_url_queue_bookmark(self):
        # A mixed library/stream queue cannot be restored safely as local
        # files. Keep CD selection usable without inventing a bookmark.
        self.mpd.paths = ["Rap/B.mp3", "https://radio.example/old"]
        self.mpd.ids = [104, 105]
        self.mpd.values.update(songPosition=1, elapsedSeconds=2.0,
                               queueVersion=3, transport="play")
        status, result = self.request("/inputs/play", {
            "sourceId": SOURCE["sourceId"], "trackNumber": 2})
        self.assertEqual(status, 200, result)
        self.assertEqual(self.mpd.values["songPosition"], 1)
        self.assertFalse(result["source"]["resumePlaylist"]["available"])

    def test_invalid_or_offline_sources_never_write_mpd(self):
        before = list(self.mpd.commands)
        for payload in ({"sourceId": "../../evil", "trackNumber": 1},
                        {"sourceId": SOURCE["sourceId"], "trackNumber": 44},
                        {"sourceId": SOURCE["sourceId"], "trackNumber": True},
                        {"sourceId": SOURCE["sourceId"], "trackNumber": 1, "url": "http://evil"}):
            with self.subTest(payload=payload):
                status, _ = self.request("/inputs/play", payload)
                self.assertIn(status, (400, 409))
        self.gateway.records[0]["available"] = False
        self.assertEqual(self.request("/inputs/play",
                         {"sourceId": SOURCE["sourceId"], "trackNumber": 1})[0], 409)
        self.assertEqual(self.mpd.commands, before)

    def test_external_queue_change_discards_input_and_bookmark(self):
        self.play_cd(3)
        self.mpd.run("stop", "clear", 'add "Rap/B.mp3"', "play 0")
        self.assertEqual(self.request("/state")[1]["source"]["type"], "library")
        self.assertFalse(self.radio.playlist_resume.view()["available"])


if __name__ == "__main__":
    unittest.main()
