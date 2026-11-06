"""Subwoofer presence and software-audio feedback are separate signals."""
import unittest

from subwoofers import Subwoofers, SubwooferError


RIO = "44:BD:8D:EC:51:80"
MOTOROLA = "11:22:33:44:55:66"


class RioStatusTests(unittest.TestCase):
    def setUp(self):
        self.subs = Subwoofers()
        self.subs._configs = [{
            "id": "sub-rio", "name": "Rio sub",
            "rendererId": RIO, "parentRendererIds": [MOTOROLA],
        }]
        self.snap = {
            "reachable": True,
            "clients": [{"id": RIO, "name": "Rio Subwoofer", "present": True}],
        }
        self.presence = {
            "snapserverReachable": True,
            "renderers": [
                {"rendererId": RIO, "audible": False, "dependent": True},
                {"rendererId": MOTOROLA, "present": False, "eligibleForSubwoofer": False},
            ],
        }

    def test_connected_renderer_without_report_is_still_present(self):
        result = self.subs.snapshot(self.snap, self.presence)
        rio = next(r for r in result["renderers"] if r["id"] == RIO)
        self.assertTrue(rio["present"])
        self.assertFalse(result["subwoofers"][0]["status"]["fresh"])
        self.assertEqual(result["subwoofers"][0]["status"]["detail"], "No status report yet")

    def test_software_muting_is_reported_without_claiming_power_standby(self):
        payload = {
            "rendererId": RIO, "mode": "auto", "state": "audio_muted",
            "acknowledged": False,
            "detail": "DSP audio muted; amplifier remains powered",
        }
        report = self.subs.report("sub-rio", payload)
        self.assertTrue(report["fresh"])
        self.assertEqual(report["state"], "audio_muted")
        self.assertFalse(report["acknowledged"])
        payload["state"] = "audio_active"
        self.assertEqual(self.subs.report("sub-rio", payload)["state"], "audio_active")

    def test_software_audio_state_must_not_claim_amp_acknowledgment(self):
        with self.assertRaises(SubwooferError):
            self.subs.report("sub-rio", {
                "rendererId": RIO, "mode": "auto", "state": "audio_muted",
                "acknowledged": True,
            })

    def test_unknown_parent_state_never_asserts_automatic_standby(self):
        demand = self.subs.demand("sub-rio", self.presence)
        self.assertTrue(demand["known"])
        self.assertFalse(demand["needed"])
        self.presence["snapserverReachable"] = False
        self.assertIsNone(self.subs.demand("sub-rio", self.presence)["needed"])


if __name__ == "__main__":
    unittest.main()
