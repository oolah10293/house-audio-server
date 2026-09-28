import copy
import unittest

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


class FakeMonitor:
    def __init__(self, present=0, reachable=True):
        self.present = present
        self.reachable = reachable

    def snapshot(self):
        return {
            "reachable": self.reachable,
            "presentCount": self.present,
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
        }
        self.commands = []

    def state(self):
        return dict(self.state_data)

    def command(self, command):
        self.run(command)

    def run(self, *commands):
        self.commands.extend(commands)
        for command in commands:
            if command == "clear":
                self.state_data["queueLength"] = 0
            elif command.startswith("add "):
                self.state_data["queueLength"] = 3
            elif command == "play":
                if self.state_data.get("queueLength", 0) > 0:
                    self.state_data["transport"] = "play"
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
    def test_present_renderer_at_fresh_idle_starts_default_folder(self):
        monitor = FakeMonitor(present=1)
        mpd = FakeMpd()
        mpd.state_data["transport"] = "stop"
        policy = h.PassiveSessionPolicy(
            monitor,
            mpd_factory=lambda: mpd,
            enabled=True,
            poll_seconds=0.01,
            default_folder="MP3s",
        )

        policy._tick()

        self.assertEqual(
            mpd.commands,
            [
                "random 0",
                "clear",
                'add "MP3s"',
                "repeat 1",
                "single 0",
                "consume 0",
                "random 1",
                "play",
            ],
        )
        self.assertEqual(mpd.state_data["transport"], "play")
        self.assertEqual(policy.snapshot()["lastAction"], "started_default_session")

    def test_renderer_arrival_from_zero_starts_default_folder(self):
        monitor = FakeMonitor(present=0)
        mpd = FakeMpd()
        mpd.state_data["transport"] = "stop"
        policy = h.PassiveSessionPolicy(
            monitor,
            mpd_factory=lambda: mpd,
            enabled=True,
            poll_seconds=0.01,
            default_folder="MP3s",
        )

        policy._tick()
        self.assertEqual(mpd.commands, [])

        monitor.present = 1
        policy._tick()

        self.assertEqual(mpd.state_data["transport"], "play")
        self.assertIn('add "MP3s"', mpd.commands)
        self.assertEqual(policy.snapshot()["lastAction"], "started_default_session")

    def test_renderer_arrival_resumes_paused_session_without_replacing_queue(self):
        monitor = FakeMonitor(present=0)
        mpd = FakeMpd()
        mpd.state_data["transport"] = "pause"
        mpd.state_data["queueLength"] = 3
        policy = h.PassiveSessionPolicy(
            monitor,
            mpd_factory=lambda: mpd,
            enabled=True,
            poll_seconds=0.01,
            default_folder="MP3s",
        )

        policy._tick()
        monitor.present = 1
        policy._tick()

        self.assertEqual(mpd.commands, ["play"])
        self.assertEqual(mpd.state_data["transport"], "play")
        self.assertEqual(mpd.state_data["queueLength"], 3)
        self.assertEqual(
            policy.snapshot()["lastAction"],
            "renderer_resumed_paused_session",
        )

    def test_last_renderer_leaving_arms_finish_track_stop(self):
        monitor = FakeMonitor(present=1)
        mpd = FakeMpd()
        policy = h.PassiveSessionPolicy(
            monitor,
            mpd_factory=lambda: mpd,
            enabled=True,
            poll_seconds=0.01,
        )

        policy._tick()  # establish baseline at one renderer
        monitor.present = 0
        policy._tick()

        self.assertEqual(mpd.commands, ["repeat 0", "single oneshot"])
        snapshot = policy.snapshot()
        self.assertTrue(snapshot["pendingFinalStop"])
        self.assertEqual(snapshot["pendingSongId"], 42)

    def test_renderer_return_cancels_pending_stop_and_restores_options(self):
        monitor = FakeMonitor(present=1)
        mpd = FakeMpd()
        policy = h.PassiveSessionPolicy(
            monitor,
            mpd_factory=lambda: mpd,
            enabled=True,
            poll_seconds=0.01,
        )

        policy._tick()
        monitor.present = 0
        policy._tick()
        monitor.present = 1
        policy._tick()

        self.assertEqual(
            mpd.commands,
            ["repeat 0", "single oneshot", "single 0", "repeat 1"],
        )
        snapshot = policy.snapshot()
        self.assertFalse(snapshot["pendingFinalStop"])
        self.assertEqual(
            snapshot["lastAction"],
            "pending_stop_cancelled_renderer_returned",
        )

    def test_completed_final_track_restores_options_and_clears_pending(self):
        monitor = FakeMonitor(present=1)
        mpd = FakeMpd()
        policy = h.PassiveSessionPolicy(
            monitor,
            mpd_factory=lambda: mpd,
            enabled=True,
            poll_seconds=0.01,
        )

        policy._tick()
        monitor.present = 0
        policy._tick()
        mpd.state_data["transport"] = "stop"
        mpd.state_data["singleMode"] = "0"
        policy._tick()

        self.assertEqual(
            mpd.commands,
            ["repeat 0", "single oneshot", "single 0", "repeat 1"],
        )
        snapshot = policy.snapshot()
        self.assertFalse(snapshot["pendingFinalStop"])
        self.assertEqual(
            snapshot["lastAction"],
            "final_track_completed_session_stopped",
        )

    def test_snapserver_outage_does_not_look_like_departure(self):
        monitor = FakeMonitor(present=1)
        mpd = FakeMpd()
        policy = h.PassiveSessionPolicy(
            monitor,
            mpd_factory=lambda: mpd,
            enabled=True,
            poll_seconds=0.01,
        )

        policy._tick()
        monitor.reachable = False
        monitor.present = 0
        policy._tick()
        self.assertEqual(mpd.commands, [])

        monitor.reachable = True
        policy._tick()  # new baseline only
        self.assertEqual(mpd.commands, [])



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
