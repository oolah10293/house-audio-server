"""Exercise the real HTTP handler and policy against a stateful MPD model.

The model injects failures before/after individual writes, including lost ACKs.
It is not a test of MPD decoding or physical Snapcast output.
"""
import copy
import http.client
import json
import shlex
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import house_audio_server as h
from test_controllers import NodeMonitor


class MpdModel(h.MpdClient):
    def __init__(self):
        self.paths = ['CDs/Album/A "live".flac', 'Rap/B.mp3', 'CDs/Album/A "live".flac']
        self.ids = [101, 102, 103]
        self.serial = 103
        self.indexed = set(self.paths) | {"MP3s/Default.mp3"}
        self.values = dict(transport="play", queueVersion=1, songPosition=2,
                           elapsedSeconds=47.125, random=True, repeat=False,
                           singleMode="0", single=False, consume=False)
        self.commands = []
        self.fail_command = None
        self.fail_after = False
        self.block = None

    def state(self):
        state = dict(self.values, queueLength=len(self.paths))
        index = state["songPosition"]
        state["songId"] = self.ids[index] if index is not None else None
        state["song"] = self.queue()[index] if index is not None else {}
        return state

    def queue(self):
        return [dict(file=path, id=ident, position=i)
                for i, (path, ident) in enumerate(zip(self.paths, self.ids))]

    def library_files(self, folder):
        return sorted(p for p in self.indexed if not folder or p.startswith(folder + "/"))

    def run(self, *commands):
        for command in commands:
            if self.block is not None:
                self.block(command)
            failing = command == self.fail_command
            if failing and not self.fail_after:
                self.fail_command = None
                raise h.MpdError("injected before " + command)
            self.commands.append(command)
            self.apply(command)
            if failing:
                self.fail_command = None
                raise h.MpdError("lost ACK after " + command)
        return "0.24.4", []

    def apply(self, command):
        op, *args = shlex.split(command)
        v = self.values
        if op == "clear":
            self.paths.clear()
            self.ids.clear()
            v.update(transport="stop", songPosition=None, elapsedSeconds=0)
            v["queueVersion"] += 1
        elif op in {"add", "addid"}:
            if "://" not in args[0] and args[0] not in self.indexed:
                raise h.MpdError("file missing")
            self.paths.append(args[0])
            self.serial += 1
            self.ids.append(self.serial)
            v["queueVersion"] += 1
        elif op == "stop":
            v.update(transport="stop", elapsedSeconds=0)
        elif op == "play":
            if args:
                v.update(songPosition=int(args[0]), elapsedSeconds=0)
            if v["songPosition"] is None:
                v["songPosition"] = 0
            v["transport"] = "play"
        elif op == "pause":
            v["transport"] = "pause" if args[0] == "1" else "play"
        elif op == "seekcur":
            v["elapsedSeconds"] = float(args[0])
        elif op in {"repeat", "random", "consume"}:
            v[op] = args[0] == "1"
        elif op == "single":
            v.update(singleMode=args[0], single=args[0] == "1")
        elif op != "clearerror":
            raise AssertionError("Unexpected MPD command: " + command)


class PlaylistResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.stations = h.StationStore(str(Path(self.temp.name) / "stations.json"))
        self.a, _ = self.stations.add("https://radio.example/a", "Station A", generated_name="A")
        self.b, _ = self.stations.add("https://radio.example/b", "Station B", generated_name="B")
        self.radio = h.RadioPlayback(self.stations)
        self.mpd = MpdModel()
        self.original = copy.deepcopy(self.mpd.paths)
        self.monitor = NodeMonitor()
        self.monitor.set("speaker")
        self.registry = h.ControllerRegistry()
        self.policy = h.PassiveSessionPolicy(self.monitor, mpd_factory=lambda: self.mpd,
                                            controllers=self.registry, radio=self.radio)
        self.startup = h.MpdStartupBoundary()
        self.startup._ready = True
        for name, value in (("RADIO", self.radio), ("RADIO_STATIONS", self.stations),
                            ("CONTROLLERS", self.registry), ("SNAPCAST_MONITOR", self.monitor),
                            ("PASSIVE_SESSION_POLICY", self.policy), ("MPD_STARTUP", self.startup),
                            ("MpdClient", lambda: self.mpd)):
            p = patch.object(h, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(h.ApiHandler, "log_message")
        p.start()
        self.addCleanup(p.stop)
        self.server = h.ThreadingHTTPServer(("127.0.0.1", 0), h.ApiHandler)
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.005})
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, path, payload=None):
        c = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            c.request("GET" if payload is None else "POST", path,
                      json.dumps(payload) if payload is not None else None,
                      {"Content-Type": "application/json"})
            response = c.getresponse()
            return response.status, json.loads(response.read())
        finally:
            c.close()

    def select(self, station=None):
        status, body = self.request("/radio/play", {"stationId": (station or self.a)["id"]})
        self.assertEqual(status, 200, body)
        return body["source"]["resumePlaylist"]["snapshotId"]

    def resume(self, token):
        return self.request("/radio/resume-playlist", {"snapshotId": token})

    def test_roundtrip_preserves_order_duplicate_occurrence_position_and_modes(self):
        for random, repeat in ((True, False), (False, True), (False, False), (True, True)):
            with self.subTest(random=random, repeat=repeat):
                self.mpd.values.update(random=random, repeat=repeat)
                token = self.select()
                self.assertEqual(self.mpd.paths, [self.a["url"]])
                self.assertEqual(self.radio.playlist_resume.saved["positionSeconds"], 47.125)
                status, body = self.resume(token)
                self.assertEqual(status, 200, body)
                self.assertEqual(self.mpd.paths, self.original)
                self.assertEqual((self.mpd.values["songPosition"], self.mpd.values["elapsedSeconds"]), (2, 47.125))
                self.assertEqual((self.mpd.values["random"], self.mpd.values["repeat"]), (random, repeat))
                self.assertEqual(body["source"]["type"], "library")
                self.assertFalse(body["source"]["resumePlaylist"]["available"])
                self.assertEqual(self.mpd.values["transport"], "play")

    def test_station_switch_pause_stop_and_phone_reopen_keep_original_snapshot(self):
        token = self.select()
        saved = copy.deepcopy(self.radio.playlist_resume.saved)
        self.assertEqual(self.select(self.b), token)
        for path in ("/pause", "/play", "/stop", "/play"):
            self.assertEqual(self.request(path, {})[0], 200)
        lease = self.registry.attach({"controllerId": "phone"})
        self.registry.detach({"controllerId": "phone", "leaseId": lease["leaseId"]})
        self.policy._tick()
        self.registry.attach({"controllerId": "phone"})
        body = self.request("/state")[1]
        self.assertEqual(body["source"]["resumePlaylist"]["snapshotId"], token)
        self.assertEqual(self.radio.playlist_resume.saved, saved)
        self.assertEqual(self.resume(token)[0], 200)

    def test_repeat_request_after_playback_progress_or_pause_never_rewinds_or_plays(self):
        token = self.select()
        self.assertEqual(self.resume(token)[0], 200)
        self.mpd.values.update(elapsedSeconds=88, transport="pause")
        before = list(self.mpd.commands)
        self.assertEqual(self.resume(token)[0], 200)
        self.assertEqual(self.mpd.commands, before)
        self.assertEqual(self.mpd.values["elapsedSeconds"], 88)
        self.assertEqual(self.mpd.values["transport"], "pause")

    def test_stale_request_cannot_restore_over_a_new_radio_visit(self):
        old = self.select()
        self.assertEqual(self.resume(old)[0], 200)
        new = self.select(self.b)
        self.assertNotEqual(old, new)
        before = list(self.mpd.commands)
        self.assertEqual(self.resume(old)[0], 409)
        self.assertEqual(self.mpd.commands, before)
        self.assertEqual(self.mpd.paths, [self.b["url"]])

    def test_no_library_queue_means_no_resume(self):
        self.mpd.command("clear")
        token = self.select()
        self.assertIsNone(token)
        self.assertFalse(self.request("/state")[1]["source"]["resumePlaylist"]["available"])
        self.assertEqual(self.resume("a" * 32)[0], 409)

    def test_stopped_queue_without_selection_starts_first_entry(self):
        self.mpd.values.update(transport="stop", songPosition=None)
        token = self.select()
        self.assertEqual(self.resume(token)[0], 200)
        self.assertEqual((self.mpd.values["songPosition"], self.mpd.values["elapsedSeconds"]), (0, 0))

    def test_paused_library_and_other_mpd_modes_survive(self):
        self.mpd.values.update(transport="pause", singleMode="oneshot", consume=True)
        token = self.select()
        self.assertEqual(self.resume(token)[0], 200)
        self.assertEqual(self.mpd.values["singleMode"], "oneshot")
        self.assertTrue(self.mpd.values["consume"])
        self.assertEqual(self.mpd.values["transport"], "play")

    def test_missing_track_leaves_station_playing_and_bookmark_retryable(self):
        token = self.select()
        self.mpd.indexed.remove("Rap/B.mp3")
        before = list(self.mpd.commands)
        status, body = self.resume(token)
        self.assertEqual((status, body["error"]), (409, "playlist_tracks_missing"))
        self.assertEqual(self.mpd.commands, before)
        self.assertEqual(self.mpd.values["transport"], "play")
        self.mpd.indexed.add("Rap/B.mp3")
        self.assertEqual(self.resume(token)[0], 200)

    def test_external_queue_replacement_invalidates_bookmark_without_writes(self):
        token = self.select()
        self.mpd.run("clear", 'add "MP3s/Default.mp3"', "play 0")
        before = list(self.mpd.commands)
        self.assertEqual(self.resume(token)[0], 409)
        self.assertEqual(self.mpd.commands, before)

    def test_explicit_folder_and_clear_discard_bookmark(self):
        for path, payload in (("/queue/replace", {"tracks": ["Rap/B.mp3"]}), ("/queue/clear", {})):
            token = self.select()
            self.assertEqual(self.request(path, payload)[0], 200)
            self.assertEqual(self.resume(token)[0], 409)
            self.assertIsNone(self.radio.playlist_resume.saved)

    def test_session_end_and_restart_expire_bookmark(self):
        token = self.select()
        self.monitor.nodes.clear()
        self.policy._tick()
        self.assertEqual(self.resume(token)[0], 409)
        self.assertEqual(self.mpd.paths, [])
        fresh = h.RadioPlayback(self.stations)
        self.assertFalse(fresh.playlist_resume.view()["available"])
        with self.assertRaises(h.ApiError):
            fresh.resume_playlist(self.mpd, token)

    def test_muted_phone_restore_remains_paused_until_eligible_output_returns(self):
        token = self.select()
        self.monitor.nodes.clear()
        lease = self.registry.attach({"controllerId": "phone", "rendererId": "phone-out"})
        status, body = self.resume(token)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["mpd"]["transport"], "pause")
        self.assertTrue(body["sessionPolicy"]["autoPaused"])
        self.assertTrue(self.registry.snapshot(self.monitor.snapshot())["controllers"][0]["outputMuted"])
        self.policy._tick()
        self.assertEqual(self.mpd.values["elapsedSeconds"], 47.125)
        self.registry.heartbeat({"controllerId": "phone", "leaseId": lease["leaseId"],
                                 "sequence": 1, "outputMuted": False, "outputReady": True})
        self.monitor.set("phone-out")
        self.policy._tick()
        self.assertEqual(self.mpd.values["transport"], "play")
        self.assertEqual(self.mpd.values["elapsedSeconds"], 47.125)

    def test_unknown_presence_is_not_treated_as_muted_only(self):
        token = self.select()
        self.registry.attach({"controllerId": "phone"})
        self.monitor.nodes.clear()
        self.monitor.reachable = False
        self.assertEqual(self.resume(token)[0], 200)
        self.assertEqual(self.mpd.values["transport"], "play")

    def test_radio_selection_write_failure_keeps_original_bookmark(self):
        self.mpd.fail_command = 'add "https://radio.example/a"'
        status, _ = self.request("/radio/play", {"stationId": self.a["id"]})
        self.assertEqual(status, 503)
        token = self.radio.playlist_resume.token
        self.assertEqual(self.radio.playlist_resume.saved["tracks"], self.original)
        self.assertEqual(self.select(self.b), token)
        self.assertEqual(self.resume(token)[0], 200)
        self.assertEqual(self.mpd.values["elapsedSeconds"], 47.125)

    def test_response_read_failure_after_success_is_safe_to_repeat(self):
        token = self.select()
        original_state = self.mpd.state
        failed = False
        def state():
            nonlocal failed
            if self.radio.playlist_resume.status == "completed" and not failed:
                failed = True
                raise OSError("state response lost after successful restore")
            return original_state()
        with patch.object(self.mpd, "state", state):
            self.assertEqual(self.resume(token)[0], 503)
            self.mpd.values["elapsedSeconds"] = 60
            before = list(self.mpd.commands)
            self.assertEqual(self.resume(token)[0], 200)
            self.assertEqual(self.mpd.commands, before)
            self.assertEqual(self.mpd.values["elapsedSeconds"], 60)

    def test_manual_pause_after_muted_restore_cancels_automatic_resume(self):
        token = self.select()
        self.monitor.nodes.clear()
        self.registry.attach({"controllerId": "phone"})
        self.assertEqual(self.resume(token)[0], 200)
        self.assertTrue(self.policy.snapshot()["autoPaused"])
        self.assertEqual(self.request("/pause", {})[0], 200)
        self.assertFalse(self.policy.snapshot()["autoPaused"])
        before = list(self.mpd.commands)
        self.assertEqual(self.resume(token)[0], 200)
        self.assertEqual(self.mpd.commands, before)

    def test_partial_failure_and_lost_ack_never_retry_or_autoplay(self):
        for command, after in (("clear", False), ('addid "Rap/B.mp3"', False),
                               ("seekcur 47.125", True), ("pause 0", True)):
            with self.subTest(command=command, after=after):
                token = self.select()
                self.mpd.fail_command, self.mpd.fail_after = command, after
                self.assertEqual(self.resume(token)[0], 503)
                saved = copy.deepcopy(self.radio.playlist_resume.saved)
                self.assertEqual(saved["tracks"], self.original)
                before = list(self.mpd.commands)
                self.assertEqual(self.request("/state")[1]["source"]["resumePlaylist"]["status"], "uncertain")
                self.assertEqual(self.resume(token)[0], 409)
                self.policy._tick()
                self.assertEqual(self.mpd.commands, before)
                self.assertEqual(self.request("/play", {})[0], 409)
                new = self.select(self.b)  # Explicit recovery keeps the original bookmark.
                self.assertNotEqual(new, token)
                self.assertEqual(self.radio.playlist_resume.saved, saved)
                self.assertEqual(self.resume(new)[0], 200)

    def test_partial_failure_can_be_abandoned_by_clear_or_last_node_departure(self):
        for cleanup in (lambda: self.request("/queue/clear", {}), self.end_session):
            token = self.select()
            self.mpd.fail_command = 'addid "Rap/B.mp3"'
            self.assertEqual(self.resume(token)[0], 503)
            cleanup()
            self.assertEqual(self.radio.playlist_resume.status, "none")
            self.assertEqual(self.mpd.paths, [])
            self.mpd.run('add "Rap/B.mp3"', "play 0")
            self.monitor.set("speaker")

    def end_session(self):
        self.monitor.nodes.clear()
        self.policy._tick()

    def test_invalid_payload_and_startup_guard_do_not_change_playback(self):
        token = self.select()
        before = list(self.mpd.commands)
        for payload in ({}, {"snapshotId": 1}, {"snapshotId": token, "extra": True}):
            self.assertEqual(self.request("/radio/resume-playlist", payload)[0], 400)
        self.startup._ready = False
        self.assertEqual(self.resume(token)[0], 503)
        self.assertEqual(self.mpd.commands, before)

    def test_queue_change_during_capture_aborts_before_clear(self):
        def change(command):
            if command == "pause 1":
                self.mpd.values["queueVersion"] += 1
        self.mpd.block = change
        status, _ = self.request("/radio/play", {"stationId": self.a["id"]})
        self.assertEqual(status, 409)
        self.assertEqual(self.mpd.paths, self.original)
        self.assertNotIn("clear", self.mpd.commands)

    def test_concurrent_duplicate_requests_serialize_without_double_restore(self):
        token = self.select()
        entered, proceed = threading.Event(), threading.Event()
        def block(command):
            if command == "clear":
                entered.set()
                if not proceed.wait(2):
                    raise AssertionError("test synchronization timed out")
        self.mpd.block = block
        results = []
        first = threading.Thread(target=lambda: results.append(self.resume(token)[0]))
        second = threading.Thread(target=lambda: results.append(self.resume(token)[0]))
        clears_before = self.mpd.commands.count("clear")
        first.start()
        self.assertTrue(entered.wait(2))
        second.start()
        proceed.set()
        first.join(3)
        second.join(3)
        self.assertEqual(results, [200, 200])
        self.assertEqual(self.mpd.commands.count("clear"), clears_before + 1)


if __name__ == "__main__":
    unittest.main()
