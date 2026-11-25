"""Model sanity on the real (synthetic) data + trained artifacts."""
import json
import unittest

import numpy as np

from vdi import config
from vdi import data_loader as dl
from tests._util import HAS_ARTIFACTS, SKIP_ART


@unittest.skipUnless(HAS_ARTIFACTS, SKIP_ART)
class TestModels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from vdi import pipeline
        cls.models = pipeline.load_models()
        cls.metrics = json.loads((config.artifacts_dir() / "metrics.json").read_text())
        cls.ev = dl.events()

    def test_metrics_reasonable(self):
        m = self.metrics
        self.assertLess(m["peak_uplink"]["test"]["mape_pct"], 15)
        self.assertGreater(m["peak_uplink"]["test"]["interval_coverage"], 0.75)
        self.assertLess(m["attendance"]["test"]["mape_pct"], 10)
        for h in ("60s", "120s", "180s"):
            self.assertGreater(m["short_horizon"]["test"][h]["auc"], 0.9)

    def test_forbidden_feature_never_used(self):
        for name, mdl in self.models.items():
            self.assertFalse(any("occupancy" in c for c in mdl.cols), name)
        self.assertNotIn("true_occupancy_sim_only", set(dl.telemetry().columns))

    def test_attendance_band_contains_point(self):
        up = self.ev[self.ev["is_upcoming"]]
        p = self.models["attendance"].predict(up)
        self.assertTrue((p["attendance_low"] <= p["attendance_pred"]).all())
        self.assertTrue((p["attendance_pred"] <= p["attendance_high"]).all())

    def test_home_beats_away(self):
        up = self.ev[self.ev["event_id"] == config.LIVE_EVENT_ID].copy()
        away = up.copy()
        away["anchor_team_home_away"] = "away"
        away["home_crowd_multiplier"] = 0.33
        am = self.models["attendance"]
        self.assertGreater(float(am.predict(up)["attendance_pred"].iloc[0]),
                           float(am.predict(away)["attendance_pred"].iloc[0]))

    def test_peak_increases_with_attendance(self):
        pm = self.models["peak_uplink"]
        import pandas as pd
        pairs = pd.DataFrame({"event_id": config.LIVE_EVENT_ID, "cell_id": ["V01-C01", "V01-C05"]})
        base = dl.daily()["baseline"]
        lo = pm.predict(pairs, np.array([30000, 30000]), self.ev, base)["peak_pred"].to_numpy()
        hi = pm.predict(pairs, np.array([68000, 68000]), self.ev, base)["peak_pred"].to_numpy()
        self.assertTrue(np.all(hi >= lo))
        self.assertTrue(np.all((hi > 0) & (hi <= 100)))

    def test_short_horizon_on_live_stream(self):
        s = dl.live_stream().sort_values(["event_id", "cell_id", "seconds_from_start"]).reset_index(drop=True)
        P = self.models["short_horizon"].predict_proba(s)
        for h in (60, 120, 180):
            self.assertEqual(len(P[h]), len(s))
            self.assertTrue(np.all((P[h] >= 0) & (P[h] <= 1)))
        cong = s["congestion_flag"].to_numpy().astype(bool)
        self.assertGreater(P[60][cong].mean(), P[60][~cong].mean())


if __name__ == "__main__":
    unittest.main()
