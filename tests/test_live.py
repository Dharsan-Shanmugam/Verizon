"""Causal replay forecasting, cursor errors and simulation-only routing."""
import json
import unittest

import numpy as np

from tests._util import HAS_ARTIFACTS, SKIP_ART
from vdi.live import forecast_curves, forecast_error


class TestReplayForecast(unittest.TestCase):
    def test_forecast_uses_only_observations_through_issue_time(self):
        times = np.arange(0, 360, 15)
        observed = np.linspace(20, 80, len(times))
        original = forecast_curves(times, observed, [0, 1], [30, 60])
        changed = observed.copy()
        changed[11:] = 99
        revised = forecast_curves(times, changed, [0, 1], [30, 60])
        self.assertEqual(original["adaptive60"][:11], revised["adaptive60"][:11])
        self.assertNotEqual(original["adaptive60"][11], revised["adaptive60"][11])
        self.assertEqual(original["history_fc"], revised["history_fc"])
        self.assertEqual(original["adaptive60_aligned"][:4], [None] * 4)
        self.assertEqual(original["adaptive60_aligned"][4:], original["adaptive60"][:-4])

    def test_trailing_residual_and_error_formula(self):
        times = np.arange(0, 180, 15)
        observed = np.arange(12) + 30
        curves = forecast_curves(times, observed, [0, 1], [30, 30])
        self.assertEqual(curves["adaptive60"][0], 30)
        self.assertEqual(curves["adaptive60"][9], 35.5)
        self.assertEqual(forecast_error([20, 30, 40], [None, 34, 30]),
                         {"n": 2, "mae": 7, "bias": 3, "current": 10})
        self.assertEqual(forecast_error([20], [None]),
                         {"n": 0, "mae": None, "bias": None, "current": None})


@unittest.skipUnless(HAS_ARTIFACTS, SKIP_ART)
class TestReplaySnapshot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from vdi.api import AppState
        cls.state = AppState(auto_train=False)

    def test_snapshot_matches_cursor_and_has_no_unmatured_error(self):
        replay, live = self.state.replay, self.state.live
        first = replay.snapshot(live["times"][0])
        self.assertEqual(first["source"]["mode"], "synthetic_replay")
        self.assertEqual(first["errors"]["adaptive60"]["n"], 0)
        self.assertIsNone(first["rolling_5min"]["mae"])
        index = 30
        cell = live["cells"][-1]
        snap = replay.snapshot(live["times"][index] + 7, cell)
        self.assertEqual(snap["index"], index)
        self.assertEqual(snap["cell_id"], cell)
        self.assertEqual(snap["forecast_target_s"], live["times"][index] + 60)
        self.assertEqual(snap["observed_prb"], live["series"][cell]["prb"][index])
        self.assertEqual(snap["errors"]["historical"]["n"], index + 1)
        self.assertEqual(snap["errors"]["adaptive60"]["n"], index - 3)
        self.assertEqual(snap["rolling_5min"]["n"], 20)
        self.assertEqual(len(snap["routing"]), len(live["cells"]))
        json.dumps(snap, allow_nan=False)

    def test_future_observations_do_not_change_current_metrics_or_routing(self):
        replay, live = self.state.replay, self.state.live
        index = 20
        before = replay.snapshot(live["times"][index])
        series = live["series"][live["cells"][0]]
        old = series["prb"]
        try:
            series["prb"] = old[:index + 1] + [100] * (len(old) - index - 1)
            self.assertEqual(before, replay.snapshot(live["times"][index]))
        finally:
            series["prb"] = old

    def test_recommendation_flags_and_comparison_match_engine(self):
        for seconds in self.state.live["times"][::100]:
            snap = self.state.replay.snapshot(seconds)
            for row in snap["routing"]:
                self.assertEqual(row["routing_required"], row["recommendation"] != "no_routing")
                self.assertEqual(row["automatic_eligible"], row["tier"].startswith("tier 1"))
                self.assertEqual(row["human_approval_required"], row["tier"].startswith("tier 2"))
                if row["recommendation"] != "steer_to_neighbor":
                    self.assertIsNone(row["projected_relief_sufficient"])
            selected, comparison = snap["selected"], snap["comparison"]
            self.assertIn("not executed", comparison["kind"])
            self.assertEqual(comparison["source_before"], snap["observed_prb"])
            self.assertEqual(comparison["target_cell_id"], selected["target_cell_id"])
            if selected["target_cell_id"]:
                self.assertEqual(comparison["source_after"], selected["projected"]["source_prb_after"])
                self.assertEqual(comparison["target_after"], selected["projected"]["target_prb_after"])

    def test_invalid_positions_and_cells(self):
        for seconds, cell in [(None, None), ("bad", None), (float("nan"), None),
                              (float("inf"), None), (1e9, None), (0, "unknown")]:
            with self.subTest(seconds=seconds, cell=cell), self.assertRaises(ValueError):
                self.state.replay.snapshot(seconds, cell)
