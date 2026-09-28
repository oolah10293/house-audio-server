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
        }
        self.commands = []

    def state(self):
        return dict(self.state_data)

    def run(self, *commands):
        self.commands.extend(commands)
        for command in commands:
            if command.startswith("repeat "):
                self.state_data["repeat"] = command.endswith("1")
            elif command.startswith("single "):
                mode = command.split(" ", 1)[1]
                self.state_data["singleMode"] = mode
                self.state_data["single"] = mode == "1"
        return "0.24.4", []


class SessionPolicyTests(unittest.TestCase):
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



if __name__ == "__main__":
    unittest.main()
