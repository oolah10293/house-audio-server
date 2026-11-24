"""Validate the installer's final checks without touching real services/files."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class InstallerVerificationTests(unittest.TestCase):
    def setUp(self):
        source = (Path(__file__).resolve().parents[1] / 'install-cd.sh').read_text()
        self.check = source.split("python3 - <<'PY'\n")[-1].split('\nPY\n')[0]
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for directory in ('web', 'pi_input', 'installed/web', 'live-gateway'):
            (self.root / directory).mkdir(parents=True)
        for name in ('house_audio_server.py', 'auto_cd.py', 'web/house.js'):
            (self.root / name).write_text('same source bytes\n')
            (self.root / 'installed' / name).write_text('same source bytes\n')
        (self.root / 'pi_input/gateway.py').write_text('gateway bytes\n')
        (self.root / 'live-gateway/gateway.py').write_text('gateway bytes\n')
        self.check = self.check.replace('/opt/house-audio-server', str(self.root / 'installed'))
        self.check = self.check.replace('/home/ads-suck/gateway.py', str(self.root / 'live-gateway/gateway.py'))
        self.health = dict(version='0.14.6', status='ok')
        self.session = dict(sessionPolicy=dict(unmutedControllerCount=0))
        self.browser = ('<div data-view="inputs"></div>'
                        '<label id="default-playlist-control"></label>'
                        '<main id="main-panel"></main>')

    def response(self, url, **kwargs):
        body = {
            '/health': json.dumps(self.health),
            '/': self.browser,
            '/house.js': 'function drawInputs() {}',
            '/session': json.dumps(self.session),
        }[url.removeprefix('http://127.0.0.1:8787')]
        return io.BytesIO(body.encode())

    def verify(self):
        with (contextlib.chdir(self.root), contextlib.redirect_stdout(io.StringIO()),
              patch('urllib.request.urlopen', side_effect=self.response), patch('time.sleep')):
            exec(compile(self.check, 'install-cd.sh:final-check', 'exec'), {})

    def test_accepts_matching_files_healthy_service_and_updated_policy(self):
        self.verify()

    def test_rejects_changed_installed_server_or_gateway_bytes(self):
        for name in ('installed/house_audio_server.py', 'live-gateway/gateway.py'):
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                path.write_text('wrong installed bytes\n')
                with self.assertRaisesRegex(SystemExit, 'differs from package'):
                    self.verify()
                path.write_bytes(original)

    def test_rejects_old_version_or_degraded_dependencies(self):
        for health in (dict(version='0.14.2-cd1', status='ok'),
                       dict(version='0.14.6', status='degraded')):
            with self.subTest(health=health):
                self.health = health
                with self.assertRaisesRegex(SystemExit, 'Installed server validation failed'):
                    self.verify()

    def test_rejects_missing_session_policy_update(self):
        self.session = dict(sessionPolicy={})
        with self.assertRaisesRegex(SystemExit, 'Updated session policy is missing'):
            self.verify()

    def test_rejects_missing_updated_browser_layout(self):
        for missing_id in ('default-playlist-control', 'main-panel'):
            with self.subTest(missing_id=missing_id):
                saved = self.browser
                self.browser = self.browser.replace('id="' + missing_id + '"', '')
                with self.assertRaisesRegex(SystemExit, 'Updated Library layout is missing'):
                    self.verify()
                self.browser = saved
