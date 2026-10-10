"""Radio HTTP and session-policy integration with deterministic MPD failures.

The fake models MPD queue identity and a finite/live distinction but never opens
network audio. Station discovery is mocked at the HTTP boundary.
"""

import copy
import http.client
import json
import shlex
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import house_audio_server as h
from test_controllers import NodeMonitor
from test_house_audio_server import FakeClock, FakeMpd


REAL_MPD_CLIENT = h.MpdClient
ROCK_URL = "https://radio.example/rock-192"
ALT_URL = "https://radio.example/alternative"


class RadioMpd(FakeMpd):
    """Preserve MPD record IDs/version across stop/play, as a real stream does."""

    replace_queue = REAL_MPD_CLIENT.replace_queue
    reorder_queue = REAL_MPD_CLIENT.reorder_queue

    def __init__(self):
        super().__init__()
        self.ids = [42, 43, 44]
        self.next_id = 45
        self.state_data.update(queueVersion=1, songPosition=0, error=None)
        self.fail_command = None
        self.fail_remaining = 0
        self.station_name = None
        self.song_title = None

    def state(self):
        state = super().state()
        state["song"] = next((item for item in self.queue()
                              if item["id"] == state.get("songId")), None)
        return state

    def queue(self):
        return [{"id": song_id, "file": file, "pos": index,
                 "stationName": self.station_name if file.startswith("https://") else None,
                 "title": self.song_title if file.startswith("https://") else None}
                for index, (song_id, file) in enumerate(zip(self.ids, self.queue_files))]

    def run(self, *commands):
        for command in commands:
            if command == self.fail_command and self.fail_remaining:
                self.fail_remaining -= 1
                self.commands.append(command)
                raise h.MpdError("injected MPD failure")
            super().run(command)
            if command == "clear":
                self.ids = []
                self.state_data.update(songId=None, songPosition=None)
                self.state_data["queueVersion"] += 1
            elif command.startswith("add "):
                self.ids.append(self.next_id)
                self.next_id += 1
                self.state_data["queueVersion"] += 1
            elif command == "clearerror":
                self.state_data["error"] = None
            elif (command == "play" or command.startswith("play ")) and self.ids:
                current = self.state_data.get("songId")
                position = int(command.split()[1]) if command != "play" else (
                    self.ids.index(current) if current in self.ids else 0)
                self.state_data.update(songId=self.ids[position], songPosition=position)
            elif command.startswith("seekcur "):
                self.state_data["elapsedSeconds"] = float(shlex.split(command)[1])
        return "0.24.4", []

    def fail(self, command, times=1):
        self.fail_command = command
        self.fail_remaining = times


class RadioHttpTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.stations = h.StationStore(str(Path(self.directory.name) / "stations.json"))
        self.history_path = Path(self.directory.name) / "history.json"
        self.radio = h.RadioPlayback(self.stations, clock=self.clock,
                                    history=h.RadioHistory(self.history_path, clock=self.clock))
        self.monitor = NodeMonitor()
        self.controllers = h.ControllerRegistry(clock=self.clock)
        self.mpd = RadioMpd()
        self.shuffle = Mock(side_effect=lambda tracks: tracks.reverse())
        self.startup = h.MpdStartupBoundary()
        self.startup.ensure_ready(RadioMpd())
        self.policy = h.PassiveSessionPolicy(
            self.monitor, mpd_factory=lambda: self.mpd, controllers=self.controllers,
            radio=self.radio, enabled=True, startup=self.startup, shuffle=self.shuffle)
        for name, value in (("RADIO_STATIONS", self.stations), ("RADIO", self.radio),
                            ("CONTROLLERS", self.controllers),
                            ("SNAPCAST_MONITOR", self.monitor),
                            ("PASSIVE_SESSION_POLICY", self.policy),
                            ("MPD_STARTUP", self.startup),
                            ("MpdClient", lambda: self.mpd)):
            patcher = patch.object(h, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        log = patch.object(h.ApiHandler, "log_message")
        log.start()
        self.addCleanup(log.stop)
        self.server = h.ThreadingHTTPServer(("127.0.0.1", 0), h.ApiHandler)
        thread = threading.Thread(target=self.server.serve_forever,
                                  kwargs={"poll_interval": 0.01})
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, path, payload=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request("POST" if payload is not None else "GET", path,
                body=json.dumps(payload) if payload is not None else None,
                headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def source(self):
        status, body = self.request("/state")
        self.assertEqual(status, 200, body)
        return body["source"]

    def add_station(self, url=ROCK_URL, name="Example Rock"):
        with patch.object(h, "probe_station", return_value=name):
            status, body = self.request("/radio/stations", {"url": url})
        self.assertEqual(status, 201, body)
        self.assertTrue(body["created"])
        return body["station"]

    def play_radio(self, station=None, establish_presence=True):
        if establish_presence:
            self.monitor.set("kitchen")
            self.policy._tick()
        station = station or self.add_station()
        status, body = self.request("/radio/play", {"stationId": station["id"]})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.mpd.queue_files, [station["url"]])
        self.assertEqual(self.mpd.state_data["transport"], "play")
        return station

    def outage(self):
        self.mpd.state_data.update(transport="stop", error="HTTP connection closed")
        self.policy._tick()

    def hear_song(self, title, seconds=11):
        self.mpd.song_title = title
        self.policy._tick()
        for _ in range(seconds):
            self.clock.advance(1)
            self.mpd.state_data["elapsedSeconds"] += 1
            self.policy._tick()

    def test_history_is_observed_without_controller_or_http_polling(self):
        self.play_radio()
        original = self.mpd.state()
        self.hear_song("First song")
        self.assertIsNone(self.radio.history.snapshot()["lastPlayed"])
        self.mpd.song_title = "Second song"
        self.policy._tick()
        code, state = self.request("/state")
        self.assertEqual(code, 200)
        self.assertEqual(state["radioHistory"]["lastPlayed"]["title"], "First song")
        self.assertEqual(state["mpd"]["queueVersion"], original["queueVersion"])
        self.assertEqual(state["mpd"]["songId"], original["songId"])
        self.assertEqual(h.RadioHistory(self.history_path).snapshot()["lastPlayed"]["title"], "First song")

    def test_history_captures_station_change_and_return_to_library(self):
        self.play_radio()
        self.hear_song("First song")
        other = self.add_station(ALT_URL, "Other station")
        self.play_radio(other, establish_presence=False)
        self.assertEqual(self.radio.history.snapshot()["lastPlayed"]["title"], "First song")
        self.hear_song("Second song")
        self.radio.release(self.mpd, clear=True)
        code, state = self.request("/state")
        self.assertEqual(code, 200)
        self.assertNotEqual(state["source"]["type"], "radio")
        self.assertEqual(state["radioHistory"]["lastPlayed"]["title"], "Second song")
        self.assertEqual(state["radioHistory"]["lastPlayed"]["station"]["name"], "Other station")

    def test_history_continues_with_presence_policy_disabled(self):
        self.play_radio()
        self.policy.enabled = False
        self.hear_song("First song")
        self.mpd.song_title = "Second song"
        self.policy._tick()
        self.assertEqual(self.radio.history.snapshot()["lastPlayed"]["title"], "First song")

    def test_save_station_is_persistent_and_does_not_change_playback(self):
        before = copy.deepcopy((self.mpd.state(), self.mpd.queue_files))
        station = self.add_station()
        status, body = self.request("/radio/stations")
        self.assertEqual(status, 200)
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["stations"], [station])
        self.assertEqual((self.mpd.state(), self.mpd.queue_files), before)
        self.assertEqual(self.mpd.commands, [])
        self.assertTrue((Path(self.directory.name) / "stations.json").exists())

    def test_duplicate_url_returns_existing_station_without_probe_or_playback(self):
        station = self.add_station()
        self.request("/radio/stations/rename", {"stationId": station["id"], "name": "My Rock"})
        with patch.object(h, "probe_station", side_effect=AssertionError("duplicate must not probe")):
            status, body = self.request("/radio/stations", {"url": ROCK_URL})
        self.assertEqual(status, 200, body)
        self.assertFalse(body["created"])
        self.assertEqual(body["station"]["id"], station["id"])
        self.assertEqual(body["station"]["name"], "My Rock")
        self.assertEqual(self.request("/radio/stations")[1]["count"], 1)
        self.assertEqual(self.mpd.commands, [])

    def test_failed_station_probe_reports_error_without_saving_or_playback(self):
        with patch.object(h, "probe_station", side_effect=h.RadioError(422, "invalid_stream", "HTML response")):
            status, body = self.request("/radio/stations", {"url": ROCK_URL})
        self.assertEqual((status, body["error"]), (422, "invalid_stream"))
        self.assertEqual(self.request("/radio/stations")[1]["count"], 0)
        self.assertEqual(self.mpd.commands, [])

    def test_stream_metadata_updates_generated_name_but_preserves_user_rename(self):
        station = self.play_radio()
        self.mpd.station_name = "Station's Own Name"
        self.policy._tick()
        self.assertEqual(self.source()["station"]["name"], "Station's Own Name")
        self.request("/radio/stations/rename", {"stationId": station["id"], "name": "My Rock"})
        self.mpd.station_name = "Rotating Station Metadata"
        self.policy._tick()
        self.assertEqual(self.source()["station"]["name"], "My Rock")

    def test_play_station_exposes_live_source_and_blocks_file_controls(self):
        station = self.play_radio()
        source = self.source()
        self.assertEqual(source["type"], "radio")
        self.assertTrue(source["live"])
        self.assertEqual(source["station"]["id"], station["id"])
        for capability in ("canSeek", "canSkip", "canShuffle", "canRepeat"):
            self.assertFalse(source[capability])
        before = list(self.mpd.commands)
        for path, payload in (("/next", {}), ("/previous", {}),
                              ("/seek", {"seconds": 30}),
                              ("/shuffle", {"enabled": True}),
                              ("/repeat", {"enabled": False}),
                              ("/queue/reorder", {"songIds": list(self.mpd.ids),
                                                  "queueVersion": self.mpd.state_data["queueVersion"]})):
            with self.subTest(path=path):
                status, body = self.request(path, payload)
                self.assertEqual((status, body["error"]), (409, "live_stream_operation"))
        self.assertEqual(self.mpd.commands, before)

    def test_station_and_folder_switch_restore_pre_radio_library_modes(self):
        self.mpd.state_data.update(random=True, repeat=False, singleMode="oneshot", consume=True)
        first = self.play_radio()
        second = self.add_station(ALT_URL, "Alternative")
        self.play_radio(second)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.source()["station"]["id"], second["id"])
        status, body = self.request("/queue/replace", {"tracks": ["Rap/A.mp3", "Rap/B.mp3"]})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.mpd.queue_files, ["Rap/A.mp3", "Rap/B.mp3"])
        state = self.mpd.state_data
        self.assertTrue(state["random"])
        self.assertFalse(state["repeat"])
        self.assertEqual(state["singleMode"], "oneshot")
        self.assertTrue(state["consume"])
        self.assertEqual(self.source()["type"], "library")

    def test_pause_stops_live_connection_but_play_reconnects_same_station(self):
        station = self.play_radio()
        before_queue = self.mpd.queue()
        status, body = self.request("/pause", {})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.mpd.state_data["transport"], "stop")
        self.assertNotIn("pause 1", self.mpd.commands)
        self.assertEqual(self.source()["station"]["id"], station["id"])
        self.clock.advance(60)
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "stop")
        self.assertEqual(self.request("/play", {})[0], 200)
        self.assertEqual(self.mpd.state_data["transport"], "play")
        self.assertEqual(self.mpd.queue(), before_queue)

    def test_last_listener_ends_radio_immediately_then_passive_starts_default(self):
        self.play_radio()
        self.mpd.commands.clear()
        self.monitor.set("kitchen", present=False)
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "stop")
        self.assertEqual(self.mpd.queue_files, [])
        self.assertFalse(self.policy.snapshot()["pendingFinalStop"])
        self.assertNotIn("single oneshot", self.mpd.commands)
        self.assertEqual(self.source()["type"], "none")
        self.monitor.set("kitchen")
        self.policy._tick()
        self.assertEqual(self.mpd.queue_files, list(reversed(self.mpd.library["MP3s"])))
        self.assertEqual(self.mpd.state_data["transport"], "play")
        self.shuffle.assert_called_once()

    def test_unknown_snapserver_presence_cannot_end_radio(self):
        self.play_radio()
        self.monitor.reachable = False
        self.monitor.nodes.clear()
        before = list(self.mpd.commands)
        self.policy._tick()
        self.assertEqual(self.mpd.commands, before)
        self.assertEqual(self.source()["type"], "radio")

    def test_radio_started_before_first_policy_tick_is_not_replaced_by_default(self):
        self.play_radio(establish_presence=False)
        self.monitor.set("kitchen")
        self.policy._tick()
        self.assertEqual(self.mpd.queue_files, [ROCK_URL])
        self.shuffle.assert_not_called()

    def test_radio_selected_without_nodes_ends_on_first_presence_sample(self):
        self.play_radio(establish_presence=False)
        self.policy._tick()
        self.assertEqual(self.mpd.queue_files, [])
        self.assertEqual(self.source()["type"], "none")
        self.assertFalse(self.policy.snapshot()["pendingFinalStop"])

    def test_muted_controller_holds_radio_and_audible_return_reconnects(self):
        lease = self.controllers.attach({"controllerId": "phone", "rendererId": "phone-audio",
                                         "outputMuted": True, "outputReady": True})
        station = self.play_radio()
        self.monitor.set("kitchen", present=False)
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "stop")
        self.assertEqual(self.source()["station"]["id"], station["id"])
        self.assertTrue(self.policy.snapshot()["autoPaused"])
        self.controllers.heartbeat({"controllerId": "phone", "leaseId": lease["leaseId"],
                                    "outputMuted": False, "outputReady": True, "sequence": 1})
        self.monitor.set("phone-audio")
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "play")
        self.assertFalse(self.policy.snapshot()["autoPaused"])
        self.shuffle.assert_not_called()

    def test_final_muted_controller_departure_ends_retained_radio(self):
        lease = self.controllers.attach({"controllerId": "phone", "rendererId": None})
        self.play_radio()
        self.monitor.set("kitchen", present=False)
        self.policy._tick()
        self.controllers.detach({"controllerId": "phone", "leaseId": lease["leaseId"]})
        self.policy._tick()
        self.assertEqual(self.mpd.queue_files, [])
        self.assertEqual(self.source()["type"], "none")
        self.assertFalse(self.policy.snapshot()["pendingFinalStop"])

    def test_renaming_and_deleting_playing_bookmark_do_not_interrupt_station(self):
        station = self.play_radio()
        before = list(self.mpd.commands)
        status, body = self.request("/radio/stations/rename", {"stationId": station["id"], "name": "My Rock"})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.source()["station"]["name"], "My Rock")
        status, body = self.request("/radio/stations/delete", {"stationId": station["id"]})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.request("/radio/stations")[1]["count"], 0)
        self.assertEqual(self.mpd.commands, before)
        self.assertEqual(self.source()["station"]["id"], station["id"])
        self.assertEqual(self.mpd.state_data["transport"], "play")

    def test_startup_pending_blocks_play_without_queue_mutation(self):
        station = self.add_station()
        with patch.object(h, "MPD_STARTUP", h.MpdStartupBoundary()):
            status, body = self.request("/radio/play", {"stationId": station["id"]})
        self.assertEqual((status, body["error"]), (503, "startup_pending"))
        self.assertEqual(self.mpd.commands, [])

    def test_invalid_radio_payloads_never_mutate_mpd_or_probe_urls(self):
        bad_payloads = [({}, "/radio/stations"), ({"url": "file:///etc/passwd"}, "/radio/stations"),
                        ({"url": "https://radio.example/\nstop"}, "/radio/stations"),
                        ({"url": ROCK_URL, "extra": True}, "/radio/stations"),
                        ({"url": ROCK_URL, "name": []}, "/radio/stations"),
                        ({}, "/radio/play"), ({"stationId": []}, "/radio/play"),
                        ({"stationId": "absent", "extra": True}, "/radio/play")]
        with patch.object(h, "probe_station", side_effect=AssertionError("must validate before probing")):
            for payload, path in bad_payloads:
                with self.subTest(path=path, payload=payload):
                    status, body = self.request(path, payload)
                    self.assertEqual(status, 400, body)
        self.assertEqual(self.mpd.commands, [])

    def test_unknown_station_does_not_interrupt_existing_playback(self):
        before = copy.deepcopy(self.mpd.state())
        status, body = self.request("/radio/play", {"stationId": "missing"})
        self.assertEqual(status, 404, body)
        self.assertEqual(self.mpd.state(), before)
        self.assertEqual(self.mpd.commands, [])

    def test_station_probe_runs_outside_mpd_write_lock(self):
        acquired = []
        def probe(url):
            def concurrent_transport():
                locked = h.MPD_WRITE_LOCK.acquire(timeout=0.5)
                acquired.append(locked)
                if locked:
                    h.MPD_WRITE_LOCK.release()
            thread = threading.Thread(target=concurrent_transport)
            thread.start()
            thread.join()
            return "Example Rock"
        with patch.object(h, "probe_station", side_effect=probe):
            status, body = self.request("/radio/stations", {"url": ROCK_URL})
        self.assertEqual(status, 201, body)
        self.assertEqual(acquired, [True])

    def test_reconnect_backoff_is_bounded_and_never_replaces_queue(self):
        self.play_radio()
        queue = self.mpd.queue()
        self.mpd.commands.clear()
        for attempt, delay in enumerate((2, 5, 10, 30, 30), start=1):
            with self.subTest(attempt=attempt):
                self.outage()
                source = self.source()
                self.assertEqual(source["status"], "retrying")
                self.assertEqual(source["retryInSeconds"], delay)
                previous = list(self.mpd.commands)
                self.clock.advance(delay - 0.25)
                self.policy._tick()
                self.assertEqual(self.mpd.commands, previous)
                self.clock.advance(0.25)
                self.policy._tick()
                self.assertEqual(self.mpd.state_data["transport"], "play")
                self.assertEqual(self.source()["retryAttempt"], attempt)
                self.assertEqual(self.mpd.queue(), queue)
        self.assertNotIn("clear", self.mpd.commands)
        self.assertFalse(any(command.startswith("add ") for command in self.mpd.commands))

    def test_explicit_pause_and_stop_cancel_retry_even_after_long_wait(self):
        self.play_radio()
        for action in ("/pause", "/stop"):
            with self.subTest(action=action):
                self.assertEqual(self.request("/play", {})[0], 200)
                self.outage()
                self.assertIsNotNone(self.source()["retryInSeconds"])
                self.assertEqual(self.request(action, {})[0], 200)
                self.assertIsNone(self.source()["retryInSeconds"])
                self.assertEqual(self.source()["playIntent"], action.lstrip("/"))
                before = list(self.mpd.commands)
                self.clock.advance(120)
                self.policy._tick()
                self.assertEqual(self.mpd.commands, before)
                self.assertEqual(self.mpd.state_data["transport"], "stop")

    def test_passive_arrival_resumes_pause_but_stopped_radio_starts_default(self):
        self.play_radio()
        self.assertEqual(self.request("/pause", {})[0], 200)
        self.monitor.set("garage")
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "play")
        self.assertEqual(self.mpd.queue_files, [ROCK_URL])
        self.assertEqual(self.request("/stop", {})[0], 200)
        self.monitor.set("basement")
        self.policy._tick()
        self.assertEqual(self.mpd.queue_files, list(reversed(self.mpd.library["MP3s"])))
        self.assertEqual(self.source()["type"], "library")

    def test_queue_clear_ends_radio_and_cancels_retry(self):
        self.play_radio()
        self.outage()
        self.assertEqual(self.request("/queue/clear", {})[0], 200)
        self.assertEqual(self.source()["type"], "none")
        self.assertEqual(self.mpd.queue_files, [])
        before = list(self.mpd.commands)
        self.clock.advance(60)
        self.policy._tick()
        self.assertEqual(self.mpd.commands, before)

    def test_switching_to_folder_cancels_pending_radio_retry(self):
        self.play_radio()
        self.outage()
        self.assertEqual(self.request("/queue/replace", {"tracks": ["Rap/A.mp3"]})[0], 200)
        before = list(self.mpd.commands)
        self.clock.advance(120)
        self.policy._tick()
        self.assertEqual(self.mpd.commands, before)
        self.assertEqual(self.mpd.queue_files, ["Rap/A.mp3"])
        self.assertEqual(self.source()["type"], "library")

    def test_switching_station_cancels_retry_of_previous_station(self):
        self.play_radio()
        self.outage()
        second = self.add_station(ALT_URL)
        self.play_radio(second)
        self.assertIsNone(self.source()["retryInSeconds"])
        self.assertEqual(self.source()["retryAttempt"], 0)
        self.clock.advance(2)
        self.mpd.state_data["elapsedSeconds"] += 2
        before = list(self.mpd.commands)
        self.policy._tick()
        self.assertEqual(self.mpd.commands, before)
        self.assertEqual(self.mpd.queue_files, [ALT_URL])

    def test_failed_play_ack_does_not_silently_turn_on_automatic_retry(self):
        station = self.add_station()
        self.monitor.set("kitchen")
        self.policy._tick()
        self.mpd.fail("play 0")
        status, body = self.request("/radio/play", {"stationId": station["id"]})
        self.assertEqual((status, body["error"]), (503, "mpd_unavailable"))
        self.assertEqual(self.source()["playIntent"], "stop")
        self.assertIsNone(self.source()["retryInSeconds"])
        before = list(self.mpd.commands)
        self.clock.advance(60)
        self.policy._tick()
        self.assertEqual(self.mpd.commands, before)
        self.assertEqual(self.mpd.state_data["transport"], "stop")

    def test_failed_stop_ack_still_cancels_reconnect_and_next_tick_retries_stop(self):
        self.play_radio()
        self.mpd.fail("stop")
        status, body = self.request("/stop", {})
        self.assertEqual((status, body["error"]), (503, "mpd_unavailable"))
        self.assertEqual(self.source()["playIntent"], "stop")
        self.assertIsNone(self.source()["retryInSeconds"])
        self.mpd.commands.clear()
        self.clock.advance(60)
        self.policy._tick()
        self.assertEqual(self.mpd.commands, ["stop"])
        self.assertEqual(self.mpd.state_data["transport"], "stop")

    def test_failed_automatic_reconnect_keeps_backoff_and_queue_identity(self):
        self.play_radio()
        queue = self.mpd.queue()
        self.outage()
        self.mpd.fail("play 0")
        self.clock.advance(2)
        self.policy._tick()
        source = self.source()
        self.assertEqual(source["retryAttempt"], 1)
        self.assertEqual(source["retryInSeconds"], 5)
        self.assertIn("injected MPD failure", source["lastError"])
        self.assertEqual(self.mpd.queue(), queue)
        before = list(self.mpd.commands)
        self.clock.advance(4)
        self.policy._tick()
        self.assertEqual(self.mpd.commands, before)
        self.clock.advance(1)
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "play")
        self.assertEqual(self.mpd.queue(), queue)

    def test_stalled_stream_is_retried_after_twenty_seconds_without_progress(self):
        self.play_radio()
        self.policy._tick()
        self.clock.advance(19)
        self.policy._tick()
        self.assertIsNone(self.source()["retryInSeconds"])
        self.clock.advance(1)
        self.policy._tick()
        self.assertEqual(self.source()["retryInSeconds"], 2)

    def test_native_queue_replacement_cancels_radio_ownership_and_retry(self):
        self.play_radio()
        self.outage()
        self.mpd.replace_queue(["CDs/New/A.mp3"])
        before = list(self.mpd.commands)
        self.clock.advance(60)
        self.policy._tick()
        self.assertEqual(self.mpd.commands, before)
        self.assertEqual(self.mpd.queue_files, ["CDs/New/A.mp3"])
        self.assertEqual(self.source()["type"], "library")

    def test_bad_library_replacement_keeps_radio_and_pending_retry_intact(self):
        self.play_radio()
        self.outage()
        before = list(self.mpd.commands)
        for tracks in ([], ["../bad.mp3"], [ROCK_URL]):
            status, body = self.request("/queue/replace", {"tracks": tracks})
            self.assertEqual(status, 400, body)
        self.assertEqual(self.mpd.commands, before)
        self.assertEqual(self.source()["type"], "radio")
        self.assertEqual(self.source()["retryInSeconds"], 2)

    def test_failed_station_switch_retains_old_radio_ownership_until_departure(self):
        first = self.play_radio()
        second = self.add_station(ALT_URL, "Alternative")
        self.mpd.fail("stop")
        status, body = self.request("/radio/play", {"stationId": second["id"]})
        self.assertEqual((status, body["error"]), (503, "mpd_unavailable"))
        self.assertEqual(self.mpd.state_data["transport"], "play")
        self.assertEqual(self.mpd.queue_files, [first["url"]])
        source = self.source()
        self.assertEqual(source["type"], "radio")
        self.assertEqual(source["station"]["id"], first["id"])
        self.mpd.commands.clear()
        self.monitor.set("kitchen", present=False)
        self.policy._tick()
        self.assertEqual(self.mpd.queue_files, [])
        self.assertEqual(self.mpd.state_data["transport"], "stop")
        self.assertFalse(self.policy.snapshot()["pendingFinalStop"])
        self.assertNotIn("single oneshot", self.mpd.commands)

    def test_failed_passive_radio_resume_retries_live_without_starting_default(self):
        station = self.play_radio()
        self.assertEqual(self.request("/pause", {})[0], 200)
        queue = self.mpd.queue()
        self.monitor.set("garage")
        self.mpd.fail("play 0")
        self.mpd.commands.clear()
        self.policy._tick()
        source = self.source()
        self.assertEqual(source["station"]["id"], station["id"])
        self.assertEqual(source["playIntent"], "play")
        self.assertEqual(source["retryInSeconds"], 2)
        self.assertEqual(self.mpd.state_data["transport"], "stop")
        self.policy._tick()
        self.assertEqual(self.mpd.queue(), queue)
        self.clock.advance(2)
        self.policy._tick()
        self.assertEqual(self.mpd.state_data["transport"], "play")
        self.assertEqual(self.mpd.queue(), queue)
        self.assertEqual(self.source()["station"]["id"], station["id"])
        self.assertNotIn("clear", self.mpd.commands)
        self.assertFalse(any(command.startswith("add ") for command in self.mpd.commands))
        self.shuffle.assert_not_called()

    def check_failed_final_cleanup(self, command, returning=False):
        self.mpd.state_data.update(random=False, repeat=True, singleMode="0", consume=True)
        station = self.play_radio()
        saved_modes = copy.deepcopy(self.radio.saved_modes)
        self.mpd.fail(command)
        self.monitor.set("kitchen", present=False)
        self.policy._tick()
        # A polling controller reading state must not erase cleanup ownership
        # after MPD has stopped but restoration has only partly succeeded.
        source = self.source()
        self.assertEqual(source["type"], "radio")
        self.assertEqual(source["station"]["id"], station["id"])
        self.assertEqual(self.radio.saved_modes, saved_modes)
        self.assertEqual(self.mpd.queue_files, [station["url"]])
        self.assertFalse(self.policy.snapshot()["pendingFinalStop"])
        self.mpd.commands.clear()
        if returning:
            self.monitor.set("kitchen")
        self.policy._tick()
        # Restoration must complete before the retained live queue is cleared.
        clear_index = self.mpd.commands.index("clear")
        for restored in ("single 0", "consume 1", "random 0", "repeat 1"):
            self.assertIn(restored, self.mpd.commands[:clear_index])
        if returning:
            self.assertEqual(self.mpd.queue_files, list(reversed(self.mpd.library["MP3s"])))
            self.assertEqual(self.mpd.state_data["transport"], "play")
            self.assertEqual(self.source()["type"], "library")
            self.shuffle.assert_called_once()
        else:
            self.assertEqual(self.mpd.queue_files, [])
            self.assertEqual(self.mpd.state_data["transport"], "stop")
            self.assertEqual(self.source()["type"], "none")
            self.assertTrue(self.mpd.state_data["consume"])
            self.assertFalse(self.mpd.state_data["random"])
            self.assertTrue(self.mpd.state_data["repeat"])
            self.assertEqual(self.mpd.state_data["singleMode"], "0")

    def test_failed_first_mode_restore_retains_cleanup_across_state_poll(self):
        self.check_failed_final_cleanup("single 0")

    def test_failed_last_mode_restore_retains_cleanup_across_state_poll(self):
        self.check_failed_final_cleanup("repeat 1")

    def test_returning_listener_finishes_failed_cleanup_then_starts_default(self):
        self.check_failed_final_cleanup("repeat 1", returning=True)

    def test_lost_clear_ack_keeps_cleanup_intent_for_returning_listener(self):
        self.play_radio()
        original_run = self.mpd.run
        lost_ack = False

        def run_with_lost_clear_ack(*commands):
            nonlocal lost_ack
            for command in commands:
                original_run(command)
                if command == "clear" and not lost_ack:
                    lost_ack = True
                    raise h.MpdError("clear succeeded but acknowledgement was lost")
            return "0.24.4", []

        self.monitor.set("kitchen", present=False)
        with patch.object(self.mpd, "run", side_effect=run_with_lost_clear_ack):
            self.policy._tick()
        self.assertTrue(lost_ack)
        self.assertEqual(self.mpd.queue_files, [])
        # State polling sees an empty queue but must retain the interrupted end
        # operation until it can complete and account for a returning listener.
        self.assertEqual(self.source()["type"], "radio")
        self.assertTrue(self.radio.ending)
        self.monitor.set("kitchen")
        self.policy._tick()
        self.assertEqual(self.mpd.queue_files, list(reversed(self.mpd.library["MP3s"])))
        self.assertEqual(self.mpd.state_data["transport"], "play")
        self.assertEqual(self.source()["type"], "library")
        self.assertFalse(self.radio.ending)
        self.shuffle.assert_called_once()


if __name__ == "__main__":
    unittest.main()
