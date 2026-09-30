import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from tools.house_buffer import update_buffer


class HouseBufferTests(unittest.TestCase):
    def test_changes_only_active_stream_buffer(self):
        original = "# buffer = 1000\n[http]\nbuffer = 5\n[stream]\nsource = pipe:///tmp/snapfifo?name=default&chunk_ms=20\nbuffer = 1000 # baseline\nchunk_ms = 20\n[tcp]\nport = 1705\n"
        self.assertEqual(update_buffer(original, 3000), original.replace("buffer = 1000 # baseline", "buffer = 3000 # baseline"))

    def test_inserts_missing_setting_and_preserves_crlf(self):
        self.assertEqual(update_buffer("[stream]\r\n# buffer = 1000\r\n", 3000),
                         "[stream]\r\nbuffer = 3000\r\n# buffer = 1000\r\n")
        self.assertEqual(update_buffer("[stream]", 3000), "[stream]\nbuffer = 3000\n")

    def test_refuses_ambiguous_or_unexpected_configuration(self):
        for config in ["[http]\n", "[stream]\n[stream]\n", "[stream]\nbuffer=1000\nbuffer=2000\n",
                       "[stream]\nchunk_ms=100\n", "[stream]\nsource=pipe:///fifo?chunk_ms=100\n",
                       "[stream]\nbuffer=oops\n"]:
            with self.subTest(config=config), self.assertRaises(ValueError):
                update_buffer(config, 3000)
        for milliseconds in [0, 999, 5001]:
            with self.assertRaises(ValueError):
                update_buffer("[stream]\n", milliseconds)

    def test_trial_is_idempotent_and_can_return_to_baseline(self):
        original = "[stream]\nbuffer=1000\n"
        updated = update_buffer(original, 3000)
        self.assertEqual(updated, update_buffer(updated, 3000))
        self.assertEqual(original, update_buffer(updated, 1000))

    def test_preview_does_not_write_and_apply_preserves_backup_and_mode(self):
        script = Path(__file__).resolve().parents[1] / "tools/house_buffer.py"
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "snapserver.conf"
            original = b"[stream]\nbuffer=1000\n"
            config.write_bytes(original)
            config.chmod(0o640)
            command = [sys.executable, str(script), "--config", str(config)]
            subprocess.run(command, check=True, capture_output=True)
            self.assertEqual(original, config.read_bytes())
            self.assertEqual([config], list(Path(folder).iterdir()))
            subprocess.run(command + ["--apply"], check=True, capture_output=True)
            self.assertEqual(b"[stream]\nbuffer=3000\n", config.read_bytes())
            self.assertEqual(0o640, config.stat().st_mode & 0o777)
            self.assertEqual(original, next(Path(folder).glob("*.bak")).read_bytes())
