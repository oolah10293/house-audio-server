import copy
import shlex
import unittest
from unittest.mock import Mock, patch

import house_audio_server as h


class ParsingTests(unittest.TestCase):
    def test_mpd_quote(self):
        self.assertEqual(h.mpd_quote('Rap/A "B".mp3'), '"Rap/A \\"B\\".mp3"')

    def test_relative_path(self):
        self.assertEqual(h.validate_relative_path("Rap/Test.mp3"), "Rap/Test.mp3")
        self.assertEqual(h.validate_relative_path(""), "")

    def test_reject_parent_path(self):
        with self.assertRaises(h.ApiError):
            h.validate_relative_path("../secret")

    def test_reject_absolute_path(self):
        with self.assertRaises(h.ApiError):
            h.validate_relative_path("/etc/passwd")

    def test_snapcast_status_normalization(self):
        monitor = h.SnapcastMonitor()
        monitor._apply_status(
            {
                "groups": [
                    {
                        "id": "g1",
                        "name": "Room",
                        "muted": False,
                        "stream_id": "default",
                        "clients": [
                            {
                                "id": "node-1",
                                "connected": True,
                                "lastSeen": {"sec": int(h.time.time()), "usec": 0},
                                "config": {
                                    "name": "Kitchen",
                                    "latency": 10,
                                    "volume": {"muted": False, "percent": 80},
                                },
                                "host": {
                                    "name": "esp32",
                                    "ip": "192.0.2.10",
                                    "mac": "00:11:22:33:44:55",
                                    "os": "ESPHome",
                                    "arch": "esp32",
                                },
                                "snapclient": {
                                    "name": "Snapclient",
                                    "version": "test",
                                    "protocolVersion": 2,
                                },
                            }
                        ],
                    }
                ],
                "server": {
                    "snapserver": {
                        "version": "0.31.0",
                        "controlProtocolVersion": 2,
                    }
                },
                "streams": [
                    {
                        "id": "default",
                        "status": "playing",
                        "uri": {"raw": "pipe:///tmp/snapfifo"},
                    }
                ],
            }
        )
        snap = monitor.snapshot()
        self.assertTrue(snap["reachable"])
        self.assertEqual(snap["connectedCount"], 1)
        self.assertEqual(snap["audibleCount"], 1)
        self.assertEqual(snap["clients"][0]["name"], "Kitchen")
        self.assertEqual(snap["clients"][0]["streamId"], "default")
        self.assertEqual(snap["serverVersion"], "0.31.0")

    def test_stale_snapcast_client_is_not_present(self):
        monitor = h.SnapcastMonitor(stale_after_seconds=5.0)
        old = h.time.time() - 30.0
        sec = int(old)
        usec = int((old - sec) * 1_000_000)
        monitor._apply_status(
            {
                "groups": [
                    {
                        "id": "g1",
                        "muted": False,
                        "stream_id": "default",
                        "clients": [
                            {
                                "id": "node-1",
                                "connected": True,
                                "lastSeen": {"sec": sec, "usec": usec},
                                "config": {"volume": {"muted": False, "percent": 100}},
                            }
                        ],
                    }
                ],
                "server": {"snapserver": {}},
                "streams": [],
            }
        )
        snap = monitor.snapshot()
        self.assertEqual(snap["connectedCount"], 1)
        self.assertEqual(snap["presentCount"], 0)
        self.assertEqual(snap["audibleCount"], 0)
        self.assertFalse(snap["clients"][0]["present"])

    def test_snapcast_group_mute_makes_client_inaudible(self):
        monitor = h.SnapcastMonitor()
        monitor._apply_status(
            {
                "groups": [
                    {
                        "id": "g1",
                        "muted": True,
                        "stream_id": "default",
                        "clients": [
                            {
                                "id": "node-1",
                                "connected": True,
                                "lastSeen": {"sec": int(h.time.time()), "usec": 0},
                                "config": {"volume": {"muted": False, "percent": 100}},
                            }
                        ],
                    }
                ],
                "server": {"snapserver": {}},
                "streams": [],
            }
        )
        snap = monitor.snapshot()
        self.assertEqual(snap["connectedCount"], 1)
        self.assertEqual(snap["audibleCount"], 0)

    def test_parse_records(self):
        lines = [
            "file: Rap/A.mp3",
            "Title: A",
            "Pos: 0",
            "Id: 4",
            "file: Rap/B.mp3",
            "Title: B",
            "Pos: 1",
            "Id: 5",
        ]
        records = h.parse_mpd_records(lines, {"file"})
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["file"], "Rap/A.mp3")
        self.assertEqual(records[1]["title"], "B")

    def test_library_files_includes_nested_files_but_not_directories(self):
        mpd = h.MpdClient()
        with patch.object(mpd, "run", return_value=("0.24.4", [h.MpdResponse([
            "directory: MP3s", "file: MP3s/A.mp3",
            "directory: MP3s/Sub", "file: MP3s/Sub/B.flac",
        ])])) as run:
            self.assertEqual(mpd.library_files("MP3s"),
                             ["MP3s/A.mp3", "MP3s/Sub/B.flac"])
        run.assert_called_once_with('listall "MP3s"')


class FakeMonitor:
    def __init__(self, present=0, reachable=True):
        self.present = present
        self.reachable = reachable

    def snapshot(self):
        return {
            "reachable": self.reachable,
            "presentCount": self.present,
            "audibleCount": self.present,
            "clients": [{"id": f"radio-{index}", "present": True, "audible": True}
                        for index in range(self.present)],
        }


class FakeMpd:
    def __init__(self):
        self.state_data = {
            "transport": "play",
            "repeat": True,
            "single": False,
            "singleMode": "0",
            "songId": 42,
            "queueLength": 3,
            "random": True,
            "consume": False,
            "elapsedSeconds": 90.5,
        }
        self.commands = []
        self.queue_files = ["CDs/Old/A.mp3", "CDs/Old/B.mp3", "CDs/Old/C.mp3"]
        self.library = {
            folder: [f"{folder}/{name}.mp3" for name in ("A", "B", "C")]
            for folder in ("MP3s", "Rap")
        }

    def library_files(self, folder):
        return list(self.library[folder])

    def state(self):
        return dict(self.state_data)

    def command(self, command):
        self.run(command)

    def run(self, *commands):
        self.commands.extend(commands)
        for command in commands:
            if command == "clear":
                self.queue_files = []
                self.state_data["queueLength"] = 0
                self.state_data["transport"] = "stop"
            elif command.startswith("add "):
                self.queue_files.append(shlex.split(command)[1])
                self.state_data["queueLength"] = len(self.queue_files)
            elif command == "stop":
                self.state_data["transport"] = "stop"
            elif command == "pause 1":
                self.state_data["transport"] = "pause"
            elif command == "play" or command.startswith("play "):
                if self.state_data.get("queueLength", 0) > 0:
                    self.state_data["transport"] = "play"
                    if command == "play 0":
                        self.state_data["songId"] = 42
                        self.state_data["elapsedSeconds"] = 0.0
            elif command.startswith("repeat "):
                self.state_data["repeat"] = command.endswith("1")
            elif command.startswith("random "):
                self.state_data["random"] = command.endswith("1")
            elif command.startswith("consume "):
                self.state_data["consume"] = command.endswith("1")
            elif command.startswith("single "):
                mode = command.split(" ", 1)[1]
                self.state_data["singleMode"] = mode
                self.state_data["single"] = mode == "1"
        return "0.24.4", []


class SessionPolicyTests(unittest.TestCase):
    def make_policy(self, present=1, transport="play", folder="MP3s", shuffle=None):
        monitor = FakeMonitor(present=present)
        mpd = FakeMpd()
        mpd.state_data["transport"] = transport
        if shuffle is None:
            shuffle = Mock(side_effect=lambda tracks: tracks.reverse())
        policy = h.PassiveSessionPolicy(
            monitor, mpd_factory=lambda: mpd, enabled=True,
            default_folder=folder, shuffle=shuffle,
        )
        return monitor, mpd, policy, shuffle

    def begin_drain(self, monitor, policy):
        policy._tick()
        monitor.present = 0
        policy._tick()
        self.assertTrue(policy.snapshot()["pendingFinalStop"])

    def complete_boundary(self, mpd, transport="pause"):
        mpd.state_data.update(transport=transport, songId=99, elapsedSeconds=0.0)

    def assert_default_session(self, mpd, folder):
        self.assertEqual(mpd.state_data["transport"], "play")
        self.assertEqual(mpd.queue_files, list(reversed(mpd.library[folder])))
        self.assertTrue(mpd.state_data["repeat"])
        self.assertTrue(mpd.state_data["random"])
        self.assertEqual(mpd.state_data["singleMode"], "0")
        self.assertFalse(mpd.state_data["consume"])
        self.assertEqual(mpd.state_data["elapsedSeconds"], 0.0)

    def test_present_renderer_at_fresh_idle_starts_shuffled_default(self):
        for folder in ("MP3s", "Rap"):
            with self.subTest(folder=folder):
                _, mpd, policy, shuffle = self.make_policy(transport="stop", folder=folder)
                policy._tick()
                self.assert_default_session(mpd, folder)
                shuffle.assert_called_once()
                self.assertEqual(policy.snapshot()["lastAction"], "started_default_session")

    def test_renderer_arrival_from_zero_starts_default_folder(self):
        monitor, mpd, policy, _ = self.make_policy(present=0, transport="stop")
        policy._tick()
        self.assertEqual(mpd.commands, [])
        monitor.present = 1
        policy._tick()
        self.assert_default_session(mpd, "MP3s")

    def test_ordinary_paused_session_resumes_without_replacing_or_shuffling(self):
        for initial_presence in (0, 1):
            with self.subTest(initial_presence=initial_presence):
                monitor, mpd, policy, shuffle = self.make_policy(
                    present=initial_presence, transport="pause")
                old_queue = list(mpd.queue_files)
                policy._tick()
                if not initial_presence:
                    monitor.present = 1
                    policy._tick()
                self.assertEqual(mpd.commands, ["play"])
                self.assertEqual(mpd.queue_files, old_queue)
                self.assertEqual(mpd.state_data["elapsedSeconds"], 90.5)
                shuffle.assert_not_called()

    def test_last_renderer_leaving_arms_finish_track_stop(self):
        monitor, mpd, policy, _ = self.make_policy()
        self.begin_drain(monitor, policy)
        self.assertEqual(mpd.commands, ["repeat 0", "single oneshot"])
        self.assertEqual(policy.snapshot()["pendingSongId"], 42)

    def test_return_before_boundary_preserves_queue_song_position_and_options(self):
        for repeat, single in ((True, "0"), (False, "1")):
            with self.subTest(repeat=repeat, single=single):
                monitor, mpd, policy, shuffle = self.make_policy()
                mpd.state_data.update(repeat=repeat, singleMode=single)
                old_queue = list(mpd.queue_files)
                self.begin_drain(monitor, policy)
                monitor.present = 1
                policy._tick()
                self.assertEqual(mpd.queue_files, old_queue)
                self.assertEqual(mpd.state_data["songId"], 42)
                self.assertEqual(mpd.state_data["elapsedSeconds"], 90.5)
                self.assertEqual(mpd.state_data["transport"], "play")
                self.assertEqual(mpd.state_data["repeat"], repeat)
                self.assertEqual(mpd.state_data["singleMode"], single)
                self.assertFalse(policy.snapshot()["pendingFinalStop"])
                self.assertFalse(any(c.startswith(("clear", "add ", "play", "stop"))
                                     for c in mpd.commands))
                shuffle.assert_not_called()

    def test_completed_drain_stops_and_later_return_uses_current_default(self):
        for transport in ("pause", "stop"):
            for folder in ("MP3s", "Rap"):
                with self.subTest(transport=transport, folder=folder):
                    monitor, mpd, policy, shuffle = self.make_policy()
                    self.begin_drain(monitor, policy)
                    self.complete_boundary(mpd, transport)
                    policy._tick()
                    self.assertEqual(mpd.state_data["transport"], "stop")
                    self.assertFalse(policy.snapshot()["pendingFinalStop"])
                    self.assertEqual(policy.snapshot()["lastAction"],
                                     "final_track_completed_session_idle")
                    self.assertEqual(mpd.commands[-3:], ["stop", "single 0", "repeat 1"])
                    shuffle.assert_not_called()
                    # A later startup consumes the current setting, not the old CD queue.
                    policy.default_folder = folder
                    monitor.present = 1
                    policy._tick()
                    self.assert_default_session(mpd, folder)
                    shuffle.assert_called_once()

    def test_return_first_observed_after_boundary_starts_fresh(self):
        for transport in ("pause", "stop"):
            with self.subTest(transport=transport):
                monitor, mpd, policy, shuffle = self.make_policy(folder="Rap")
                self.begin_drain(monitor, policy)
                self.complete_boundary(mpd, transport)
                monitor.present = 1
                policy._tick()
                self.assert_default_session(mpd, "Rap")
                self.assertFalse(policy.snapshot()["pendingFinalStop"])
                self.assertNotIn("play", mpd.commands)  # never resume old selection
                shuffle.assert_called_once()

    def test_pause_of_unfinished_song_during_drain_is_not_completed(self):
        monitor, mpd, policy, shuffle = self.make_policy()
        old_queue = list(mpd.queue_files)
        self.begin_drain(monitor, policy)
        mpd.state_data["transport"] = "pause"
        policy._tick()
        self.assertTrue(policy.snapshot()["pendingFinalStop"])
        monitor.present = 1
        policy._tick()
        self.assertEqual(mpd.state_data["transport"], "play")
        self.assertEqual(mpd.state_data["elapsedSeconds"], 90.5)
        self.assertEqual(mpd.queue_files, old_queue)
        shuffle.assert_not_called()

    def test_restart_after_completed_drain_does_not_resurrect_old_queue(self):
        monitor, mpd, policy, shuffle = self.make_policy()
        self.begin_drain(monitor, policy)
        self.complete_boundary(mpd)
        policy._tick()
        restarted = h.PassiveSessionPolicy(
            monitor, mpd_factory=lambda: mpd, enabled=True,
            default_folder="Rap", shuffle=shuffle,
        )
        monitor.present = 1
        restarted._tick()
        self.assert_default_session(mpd, "Rap")

    def test_snapserver_outage_does_not_look_like_departure(self):
        monitor, mpd, policy, shuffle = self.make_policy()
        policy._tick()
        monitor.reachable = False
        monitor.present = 0
        policy._tick()
        monitor.reachable = True
        policy._tick()
        self.assertEqual(mpd.commands, [])
        shuffle.assert_not_called()

    def test_pending_drain_survives_snapserver_outage_before_or_after_boundary(self):
        for completed in (False, True):
            with self.subTest(completed=completed):
                monitor, mpd, policy, shuffle = self.make_policy()
                old_queue = list(mpd.queue_files)
                self.begin_drain(monitor, policy)
                monitor.reachable = False
                policy._tick()
                if completed:
                    self.complete_boundary(mpd)
                monitor.reachable = True
                monitor.present = 1
                policy._tick()
                self.assertFalse(policy.snapshot()["pendingFinalStop"])
                if completed:
                    self.assert_default_session(mpd, "MP3s")
                else:
                    self.assertEqual(mpd.queue_files, old_queue)
                    self.assertEqual(mpd.state_data["elapsedSeconds"], 90.5)
                    shuffle.assert_not_called()

    def test_failed_drain_cleanup_is_retried_without_resuming_old_queue(self):
        monitor, mpd, policy, _ = self.make_policy()
        self.begin_drain(monitor, policy)
        self.complete_boundary(mpd)
        with patch.object(mpd, "command", side_effect=OSError("offline")):
            policy._tick()
        self.assertTrue(policy.snapshot()["pendingFinalStop"])
        monitor.present = 1
        policy._tick()
        self.assert_default_session(mpd, "MP3s")
        self.assertFalse(policy.snapshot()["pendingFinalStop"])

    def test_each_fresh_session_reshuffles_and_chance_repeat_is_allowed(self):
        # Two different orders share their first song; the third repeats the
        # entire second order by chance. Never reject or reroll either result.
        orders = [["A", "B", "C"], ["A", "C", "B"], ["A", "C", "B"]]
        remaining = iter(orders)
        shuffle = Mock(side_effect=lambda tracks: tracks.__setitem__(
            slice(None), [f"MP3s/{name}.mp3" for name in next(remaining)]))
        monitor, mpd, policy, _ = self.make_policy(transport="stop", shuffle=shuffle)
        for number, order in enumerate(orders, 1):
            monitor.present = 1
            policy._tick()
            self.assertEqual(mpd.queue_files, [f"MP3s/{name}.mp3" for name in order])
            self.assertEqual(shuffle.call_count, number)
            monitor.present = 0
            policy._tick()
            self.complete_boundary(mpd)
            policy._tick()
        self.assertEqual(shuffle.call_count, 3)

    def test_empty_default_does_not_clear_queue_and_retries_when_available(self):
        monitor, mpd, policy, shuffle = self.make_policy(transport="stop")
        old_queue = list(mpd.queue_files)
        mpd.library["MP3s"] = []
        policy._tick()
        self.assertEqual(mpd.commands, [])
        self.assertEqual(mpd.queue_files, old_queue)
        shuffle.assert_not_called()
        mpd.library["MP3s"] = ["MP3s/A.mp3"]
        policy._tick()
        self.assert_default_session(mpd, "MP3s")

    def test_production_shuffle_uses_system_entropy(self):
        policy = h.PassiveSessionPolicy(FakeMonitor(), enabled=False)
        self.assertIsInstance(policy._shuffle.__self__, h.random.SystemRandom)
        self.assertEqual(policy.snapshot()["defaultShufflePolicy"], "new_each_fresh_session")


class FakeClock:
    def __init__(self, value=1000.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class DiagnosticsTests(unittest.TestCase):
    def test_timesync_stall_and_recovery_are_recorded(self):
        clock = FakeClock()
        monitor = FakeMonitor()
        recorder = h.DiagnosticsRecorder(
            monitor,
            enabled=True,
            poll_seconds=1.0,
            stall_warn_seconds=2.5,
            history_limit=20,
            clock=clock,
        )

        baseline = {
            "reachable": True,
            "clients": [
                {
                    "id": "node-1",
                    "name": "Radio 1",
                    "connected": True,
                    "present": True,
                    "audible": True,
                    "lastSeenAgeSeconds": 0.4,
                    "host": {"ip": "192.0.2.10"},
                }
            ],
            "streams": [{"id": "default", "status": "playing"}],
        }
        recorder._process_snapshot(baseline)

        clock.advance(3)
        stalled = copy.deepcopy(baseline)
        stalled["clients"][0]["lastSeenAgeSeconds"] = 3.2
        recorder._process_snapshot(stalled)

        clock.advance(1)
        recovered = copy.deepcopy(baseline)
        recovered["clients"][0]["lastSeenAgeSeconds"] = 0.3
        recorder._process_snapshot(recovered)

        diag = recorder.snapshot()
        types = [event["type"] for event in diag["events"]]
        self.assertEqual(
            types,
            ["timesync_stall_started", "timesync_stall_recovered"],
        )
        self.assertEqual(diag["clients"][0]["timesyncStallCount"], 1)
        self.assertEqual(diag["clients"][0]["maxLastSeenAgeSeconds"], 3.2)

    def test_presence_and_stream_state_changes_are_recorded(self):
        clock = FakeClock()
        monitor = FakeMonitor()
        recorder = h.DiagnosticsRecorder(
            monitor,
            enabled=True,
            history_limit=20,
            clock=clock,
        )

        baseline = {
            "reachable": True,
            "clients": [
                {
                    "id": "node-1",
                    "name": "Radio 1",
                    "connected": True,
                    "present": True,
                    "audible": True,
                    "lastSeenAgeSeconds": 0.2,
                    "host": {"ip": "192.0.2.10"},
                }
            ],
            "streams": [{"id": "default", "status": "playing"}],
        }
        recorder._process_snapshot(baseline)

        changed = copy.deepcopy(baseline)
        changed["clients"][0]["present"] = False
        changed["clients"][0]["audible"] = False
        changed["clients"][0]["lastSeenAgeSeconds"] = 6.0
        changed["streams"][0]["status"] = "idle"
        clock.advance(6)
        recorder._process_snapshot(changed)

        diag = recorder.snapshot()
        types = [event["type"] for event in diag["events"]]
        self.assertIn("client_present_changed", types)
        self.assertIn("client_audible_changed", types)
        self.assertIn("stream_status_changed", types)
        self.assertIn("timesync_stall_started", types)
        self.assertEqual(diag["clients"][0]["presentDropCount"], 1)

    def test_first_snapshot_is_baseline_not_noise(self):
        clock = FakeClock()
        monitor = FakeMonitor()
        recorder = h.DiagnosticsRecorder(
            monitor,
            enabled=True,
            history_limit=20,
            clock=clock,
        )
        recorder._process_snapshot(
            {
                "reachable": True,
                "clients": [],
                "streams": [{"id": "default", "status": "idle"}],
            }
        )
        self.assertEqual(recorder.snapshot()["eventCount"], 0)



if __name__ == "__main__":
    unittest.main()
