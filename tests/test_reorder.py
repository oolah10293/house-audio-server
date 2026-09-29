import copy
import unittest
from unittest.mock import patch

import house_audio_server as h


class QueueReorderTests(unittest.TestCase):
    def setUp(self):
        self.mpd = h.MpdClient()
        self.ids = [8, 4, 9]
        self.snapshot = {"queueVersion": 7, "songId": 4, "transport": "pause",
                         "elapsedSeconds": 32.5, "random": True, "repeat": True}
        self.commands = []
        for name, value in (("state", lambda: dict(self.snapshot)),
                            ("queue", lambda: [{"id": i, "file": "same.mp3"} for i in self.ids]),
                            ("run", self.run_commands)):
            patcher = patch.object(self.mpd, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_commands(self, *commands):
        self.commands.extend(commands)
        for command in commands:
            operation, song, position = command.split()
            self.assertEqual(operation, "moveid")
            self.ids.insert(int(position), self.ids.pop(self.ids.index(int(song))))
            self.snapshot["queueVersion"] += 1

    def test_reorder_preserves_paused_playing_and_stopped_track_position_options(self):
        for transport in ("pause", "play", "stop"):
            self.ids = [8, 4, 9]
            self.snapshot.update(transport=transport, queueVersion=7)
            before = copy.deepcopy(self.snapshot)
            self.mpd.reorder_queue([4, 9, 8], 7)
            self.assertEqual(self.ids, [4, 9, 8])
            before["queueVersion"] = self.snapshot["queueVersion"]
            self.assertEqual(self.snapshot, before)

    def test_same_order_is_no_write_and_duplicate_files_keep_distinct_ids(self):
        self.mpd.reorder_queue([8, 4, 9], 7)
        self.assertEqual(self.commands, [])
        self.mpd.reorder_queue([9, 4, 8], 7)
        self.assertEqual(self.ids, [9, 4, 8])

    def test_stale_revision_or_incomplete_ids_make_no_change(self):
        for ids, version in (([8, 4, 9], 6), ([8, 4], 7), ([8, 4, 10], 7)):
            with self.assertRaises(h.ApiError) as caught:
                self.mpd.reorder_queue(ids, version)
            self.assertEqual(caught.exception.code, "stale_queue")
        self.assertEqual(self.commands, [])

    def test_invalid_payloads_make_no_change(self):
        for ids, version in (([True, 4, 9], 7), ([8, 8, 4], 7), ([8, -4, 9], 7),
                             ("8,4,9", 7), ([8, 4, 9], True), ([8, 4, 9], -1)):
            with self.assertRaises(h.ApiError) as caught:
                self.mpd.reorder_queue(ids, version)
            self.assertEqual(caught.exception.status, 400)
        self.assertEqual(self.commands, [])

    def test_concurrent_native_queue_change_during_read_is_rejected(self):
        with patch.object(self.mpd, "state", side_effect=[{"queueVersion": 7}, {"queueVersion": 8}]):
            with self.assertRaises(h.ApiError):
                self.mpd.reorder_queue([4, 9, 8], 7)
        self.assertEqual(self.commands, [])


if __name__ == "__main__":
    unittest.main()
