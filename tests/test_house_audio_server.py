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


if __name__ == "__main__":
    unittest.main()
