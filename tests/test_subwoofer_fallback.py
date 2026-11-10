"""Per-subwoofer, one-shot Snapcast fallback volume regression tests."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import house_audio_server as h
from subwoofers import SubwooferError, Subwoofers


SUB = "44:BD:8D:EC:51:80"
RADIO = "11:22:33:44:55:66"
PHONE = "AA:BB:CC:DD:EE:FF"


class SubFallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "subwoofers.json"
        self.subs = Subwoofers(self.path)
        self.snap = {"reachable": True, "clients": [
            {"id": SUB, "present": True, "volumePercent": 80, "muted": True},
            {"id": RADIO, "present": True},
            {"id": PHONE, "present": False},
        ]}
        self.subs.save({"name": "Rio", "rendererId": SUB,
                        "parentRendererIds": [RADIO, PHONE]}, self.snap)
        self.sub_id = self.subs._configs[0]["id"]

    def targets(self):
        return self.subs.fallback_targets(self.snap)

    def apply(self):
        result = self.targets()
        for sub_id, _, _, _, token in result:
            self.subs.mark_fallback_applied(sub_id, token)
        return result

    def test_default_is_25_and_persisted_on_registration(self):
        self.assertEqual(self.subs._configs[0]["fallbackVolumePercent"], 25)
        self.assertEqual(Subwoofers(self.path)._configs[0]["fallbackVolumePercent"], 25)

    def test_legacy_persisted_settings_migrate_in_memory_without_overwrite(self):
        old = dict(self.subs._configs[0])
        old.pop("fallbackVolumePercent")
        raw = json.dumps({"version": 1, "subwoofers": [old]})
        self.path.write_text(raw)
        restored = Subwoofers(self.path)
        self.assertEqual(restored._configs[0]["fallbackVolumePercent"], 25)
        self.assertEqual(self.path.read_text(), raw)

    def test_invalid_percent_is_rejected_without_overwriting_good_data(self):
        for invalid in (-1, 101, 25.5, True, "25", None):
            with self.subTest(invalid=invalid):
                with self.assertRaises(SubwooferError):
                    self.subs.save({"id": self.sub_id, "name": "Rio", "rendererId": SUB,
                                    "parentRendererIds": [RADIO],
                                    "fallbackVolumePercent": invalid}, self.snap)
                self.assertEqual(self.subs._configs[0]["fallbackVolumePercent"], 25)

    def test_parent_present_prevents_fallback_even_if_muted(self):
        self.snap["clients"][1]["muted"] = True
        self.assertEqual(self.targets(), [])

    def test_last_parent_leaves_once_preserves_mute_and_rearms_on_return(self):
        self.snap["clients"][1]["present"] = False
        action = self.apply()
        self.assertEqual(len(action), 1)
        self.assertEqual(action[0][1:4], (SUB, 25, True))
        self.assertEqual(self.targets(), [])
        # User changes the volume manually while the parent remains absent.
        self.snap["clients"][0]["volumePercent"] = 70
        self.assertEqual(self.targets(), [])
        self.snap["clients"][1]["present"] = True
        self.assertEqual(self.targets(), [])
        self.snap["clients"][1]["present"] = False
        self.assertEqual(self.apply()[0][2], 25)

    def test_other_assigned_parent_prevents_reset(self):
        self.snap["clients"][1]["present"] = False
        self.snap["clients"][2]["present"] = True
        self.assertEqual(self.targets(), [])

    def test_unknown_snapserver_never_changes_volume(self):
        self.snap["clients"][1]["present"] = False
        self.snap["reachable"] = False
        self.assertEqual(self.targets(), [])
        self.snap["reachable"] = True
        self.assertEqual(len(self.targets()), 1)

    def test_server_outage_does_not_reapply_previous_reset(self):
        self.snap["clients"][1]["present"] = False
        self.apply()
        self.snap["clients"][0]["volumePercent"] = 70
        self.snap["reachable"] = False
        self.assertEqual(self.targets(), [])
        self.snap["reachable"] = True
        # Restored server visibility alone must not reset the manually set 70%.
        self.assertEqual(self.targets(), [])

    def test_unmuted_sub_remains_unmuted_after_reset(self):
        self.snap["clients"][1]["present"] = False
        self.snap["clients"][0]["muted"] = False
        action = self.apply()
        self.assertEqual(action[0][1:4], (SUB, 25, False))

    def test_offline_sub_waits_until_present(self):
        self.snap["clients"][1]["present"] = False
        self.snap["clients"][0]["present"] = False
        self.assertEqual(self.targets(), [])
        self.snap["clients"][0]["present"] = True
        self.assertEqual(len(self.apply()), 1)
        self.snap["clients"][0]["present"] = False
        self.assertEqual(self.targets(), [])
        self.snap["clients"][0]["present"] = True
        self.assertEqual(len(self.targets()), 1)

    def test_failed_write_retries_and_equal_value_does_not_send(self):
        self.snap["clients"][1]["present"] = False
        self.assertEqual(len(self.targets()), 1)
        self.assertEqual(len(self.targets()), 1)  # No success acknowledgment.
        self.snap["clients"][0]["volumePercent"] = 25
        self.assertEqual(self.targets(), [])
        self.snap["clients"][0]["volumePercent"] = 60
        self.assertEqual(self.targets(), [])  # One-shot already satisfied.

    def test_setting_update_retargets_and_survives_restart(self):
        self.snap["clients"][1]["present"] = False
        self.apply()
        cfg = self.subs._configs[0]
        self.subs.save(dict(cfg, fallbackVolumePercent=50), self.snap)
        self.assertEqual(self.targets()[0][2], 50)
        self.assertEqual(Subwoofers(self.path)._configs[0]["fallbackVolumePercent"], 50)

    def test_omitting_field_on_existing_save_preserves_custom_percent(self):
        cfg = self.subs._configs[0]
        self.subs.save(dict(cfg, fallbackVolumePercent=60), self.snap)
        self.subs.save({k: v for k, v in cfg.items() if k != "fallbackVolumePercent"}, self.snap)
        self.assertEqual(self.subs._configs[0]["fallbackVolumePercent"], 60)


class FakeRpcSocket:
    def __init__(self, response):
        self.response = response
        self.sent = b""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def settimeout(self, timeout):
        pass

    def makefile(self, *args, **kwargs):
        return self

    def write(self, data):
        self.sent = data

    def flush(self):
        pass

    def readline(self):
        result = self.response
        self.response = b""
        return result


class SnapcastVolumeRpcTests(unittest.TestCase):
    def test_set_volume_calls_expected_rpc_and_preserves_muted(self):
        monitor = h.SnapcastMonitor()
        sock = FakeRpcSocket(b'{"id":1,"jsonrpc":"2.0","result":{"volume":{"percent":25,"muted":true}}}\n')
        with patch.object(h.socket, "create_connection", return_value=sock) as create:
            monitor._set_client_volume(SUB, 25, True)
        create.assert_called_once()
        request = json.loads(sock.sent)
        self.assertEqual(request["method"], "Client.SetVolume")
        self.assertEqual(request["params"], {
            "id": SUB, "volume": {"percent": 25, "muted": True},
        })

    def test_rpc_error_cannot_be_treated_as_applied(self):
        monitor = h.SnapcastMonitor()
        sock = FakeRpcSocket(b'{"id":1,"error":{"code":-32602,"message":"Invalid params"}}\n')
        with patch.object(h.socket, "create_connection", return_value=sock):
            with self.assertRaises(h.SnapcastError):
                monitor._set_client_volume(SUB, 25, False)


if __name__ == "__main__":
    unittest.main()
