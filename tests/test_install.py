"""Run the real installer against a temporary filesystem and fake systemctl."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InstallTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config = self.root / "default"
        self.destination = self.root / "installed"
        self.service = self.root / "service"
        self.calls = self.root / "systemctl-calls"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        # Root/account checks and ownership flags are host-specific. All file
        # installation, config migration and shell control flow are real.
        scripts = {
            "id": "#!/bin/sh\nprintf '0\\n'\n",
            "systemctl": '#!/bin/sh\nprintf "%s\\n" "$*" >> "$INSTALL_TEST_CALLS"\n',
            "install": "#!/usr/bin/env python3\nimport os, sys\nargs = []\nit = iter(sys.argv[1:])\nfor value in it:\n    if value in ('-o', '-g'): next(it)\n    else: args.append(value)\nos.execv(" + repr(shutil.which("install")) + ", ['install', *args])\n",
        }
        for name, content in scripts.items():
            path = self.bin / name
            path.write_text(content)
            path.chmod(0o755)
        script = (ROOT / "install.sh").read_text()
        for old, new in (("/opt/house-audio-server", self.destination),
                         ("/etc/systemd/system/house-audio-server.service", self.service),
                         ("/etc/default/house-audio-server", self.config)):
            script = script.replace(old, str(new))
        self.installer = self.root / "install.sh"
        self.installer.write_text(script)

    def install(self):
        env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                   INSTALL_TEST_CALLS=str(self.calls))
        subprocess.run(["sh", str(self.installer)], cwd=ROOT, env=env,
                       check=True, capture_output=True, text=True)

    def test_upgrade_removes_recorder_settings_and_preserves_local_configuration(self):
        kept = "MPD_HOST=192.0.2.10\nHOUSE_AUDIO_PORT=9876\nPASSIVE_DEFAULT_FOLDER=Rap\n# custom comment\nCUSTOM_DIAGNOSTICS_ENABLED=yes\n"
        obsolete = "# Lightweight in-memory renderer diagnostics. Records only state changes and\n# stalls, not every sample.\nDIAGNOSTICS_ENABLED=true\nDIAGNOSTICS_POLL_SECONDS=1.0\n  export DIAGNOSTICS_STALL_WARN_SECONDS = 2.5\nDIAGNOSTICS_HISTORY_LIMIT=200\n"
        self.config.write_text(kept + obsolete)
        self.config.chmod(0o640)
        self.install()
        self.assertEqual(self.config.read_text(), kept)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o640)
        self.assertEqual((self.destination / "house_audio_server.py").read_bytes(),
                         (ROOT / "house_audio_server.py").read_bytes())
        self.assertEqual((self.destination / "radio_stations.py").read_bytes(),
                         (ROOT / "radio_stations.py").read_bytes())
        self.assertEqual(self.calls.read_text().splitlines(), [
            "daemon-reload", "enable house-audio-server.service", "restart house-audio-server.service"])
        self.install()
        self.assertEqual(self.config.read_text(), kept)

    def test_fresh_install_uses_current_defaults_without_recorder(self):
        self.install()
        self.assertEqual(self.config.read_bytes(), (ROOT / "config/house-audio-server.default").read_bytes())
        self.assertNotIn("DIAGNOSTICS_", self.config.read_text())
