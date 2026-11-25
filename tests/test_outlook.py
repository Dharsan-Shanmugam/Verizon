"""Long-horizon targets, chronological isolation and causal inference."""
import unittest

import numpy as np

from tests._util import HAS_ARTIFACTS, SKIP_ART
from vdi import data_loader as dl
from vdi.models.outlook import HORIZONS, OutlookModel, design, ordered, targets, risk_targets


@unittest.skipUnless(HAS_ARTIFACTS, SKIP_ART)
class TestOutlook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = OutlookModel.load_or_train()
        cls.frame = ordered(dl.telemetry())

    def test_exact_target_alignment_and_no_event_crossing(self):
        frame = self.frame
        group = frame[(frame["event_id"] == frame.iloc[0]["event_id"])
                      & (frame["cell_id"] == frame.iloc[0]["cell_id"])].copy().reset_index(drop=True)
        for horizon in HORIZONS:
            target = targets(group, horizon)
            steps = horizon // 15
            np.testing.assert_array_equal(target[:-steps], group["ul_prb_pct"].to_numpy()[steps:])
            self.assertTrue(np.isnan(target[-steps:]).all())
        group.loc[60, "timestamp"] = "2099-01-01 10:00:00"
        self.assertTrue(np.isnan(targets(group, 900)[0]))

    def test_features_and_predictions_do_not_use_future_observations(self):
        frame = ordered(dl.live_stream())
        frame = frame[frame["cell_id"] == frame.iloc[0]["cell_id"]].reset_index(drop=True)
        before = self.model.predict(frame.iloc[:101].copy())
        changed = frame.copy()
        changed.loc[101:, "ul_prb_pct"] = 100
        after = self.model.predict(changed)
        risk_before = self.model.predict_risk(frame.iloc[:101].copy())
        risk_after = self.model.predict_risk(changed)
        for horizon in HORIZONS:
            np.testing.assert_allclose(before[horizon], after[horizon][:101])
            np.testing.assert_allclose(risk_before[horizon], risk_after[horizon][:101])
        self.assertNotIn("true_occupancy_sim_only", design(frame).columns)
        self.assertFalse(any("label_congestion" in column for column in design(frame).columns))

    def test_event_held_out_metrics_and_baseline_are_reproducible(self):
        report = self.model.report()
        train, calibration, test = [set(report["split"][key]) for key in ("train", "calibration", "test")]
        self.assertFalse(train & calibration or train & test or calibration & test)
        first = self.frame.groupby("event_id", observed=True)["timestamp"].min()
        self.assertLess(first.loc[list(train)].max(), first.loc[list(calibration)].min())
        self.assertLess(first.loc[list(calibration)].max(), first.loc[list(test)].min())
        frame = self.frame[self.frame["event_id"].astype(str).isin(test)].reset_index(drop=True)
        predictions = self.model.predict(frame)
        for horizon in (900, 1200, 1800):
            y = targets(frame, horizon)
            valid = np.isfinite(y)
            metric = report["horizons"][str(horizon)]
            self.assertEqual(metric["n"], int(valid.sum()))
            self.assertEqual(metric["mae"], round(float(np.abs(predictions[horizon][valid] - y[valid]).mean()), 2))
            baseline = np.abs(frame["ul_prb_pct"].to_numpy()[valid] - y[valid]).mean()
            self.assertEqual(metric["persistence_mae"], round(float(baseline), 2))
            self.assertLess(metric["mae"], metric["persistence_mae"])
            self.assertGreaterEqual(metric["coverage_pct"], 85)
            self.assertLessEqual(metric["coverage_pct"], 95)

    def test_risk_targets_are_exact_future_five_minute_bins(self):
        frame = self.frame[(self.frame["event_id"] == self.frame.iloc[0]["event_id"])
                           & (self.frame["cell_id"] == self.frame.iloc[0]["cell_id"])].copy().reset_index(drop=True)
        frame["congestion_flag"] = False
        frame.loc[5, "congestion_flag"] = True
        self.assertEqual(risk_targets(frame, 300)[0], 1)
        self.assertEqual(risk_targets(frame, 600)[0], 0)
        self.assertTrue(np.isnan(risk_targets(frame, 1800)[-120:]).all())
        report = self.model.report()["horizons"]["1800"]["congestion_risk"]
        self.assertGreater(report["auc"], .8)
        self.assertGreaterEqual(report["precision"], 0)
        self.assertLessEqual(report["recall"], 1)
        self.assertLess(report["brier"], .25)
