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
