"""Explicit synthetic service data, advisory recommendations and monotone response."""
import json
import unittest
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

from tests._util import HAS_ARTIFACTS, SKIP_ART
from vdi import config, data_loader as dl
from vdi import service_quality as sq


class TestServiceResponse(unittest.TestCase):
    def test_device_count_requires_valid_event_estimate(self):
        state = Mock()
        state.event_detail.return_value = {}
        for value in (np.nan, -1, 1.5):
            with self.subTest(value=value), patch.object(dl, "events", return_value=pd.DataFrame(
                    {"event_id": ["TEST"], "connected_devices_est": [value]})):
                with self.assertRaises(ValueError):
                    sq.recommendations(state, "TEST")
        with patch.object(dl, "events", return_value=pd.DataFrame(
                {"event_id": ["OTHER"], "connected_devices_est": [20]})):
            with self.assertRaises(ValueError):
                sq.recommendations(state, "TEST")
        state.sim.simulate.assert_not_called()

    def test_quality_worsens_with_pressure_and_slot_shortage(self):
        baseline = sq.estimate(50)
        congested = sq.estimate(95)
        tdd = sq.estimate(50, tdd_gap=40)
        fault = sq.estimate(50, interference=True)
        for result in (congested, tdd, fault):
            self.assertGreater(result["latency_ms"], baseline["latency_ms"])
            self.assertGreater(result["packet_loss_pct"], baseline["packet_loss_pct"])
        self.assertGreater(sq.estimate(50, transport_loss=2)["packet_loss_pct"], 2)

    def test_loss_remains_a_percentage(self):
        quality = sq.estimate(np.linspace(0, 100, 1000), transport_loss=10, tdd_gap=100)
        self.assertTrue(np.all(quality["packet_loss_pct"] >= 0))
        self.assertTrue(np.all(quality["packet_loss_pct"] <= 100))
        self.assertTrue(np.isfinite(quality["latency_ms"]).all())

class TestReadinessChecks(unittest.TestCase):
    def setUp(self):
        self.event = {"venue_name": "Test stadium", "start": "2026-10-28 12:00"}
        self.cells = [{"cell_id": "C1", "peak_prb": 90}, {"cell_id": "C2", "peak_prb": 50}]
        self.neighbors = [{"cell_id": "C1", "neighbor_cell_id": "C2", "steering_eligibility": "eligible"}]

    def checks(self, site=50, shared=50, tdd=()):
        return {r["id"]: r for r in sq.readiness_checks(self.event, self.cells, self.neighbors, site, shared, tdd)}

    def test_forecast_venue_and_tdd_change_assessments(self):
        high = self.checks(tdd=["C1"])
        self.assertEqual(high["radio_capacity"]["status"], "priority")
        self.assertEqual(high["steering_policy"]["status"], "review")
        self.assertIn("C1 to C2", high["steering_policy"]["action"])
        self.assertEqual(high["tdd_allocation"]["status"], "review")
        self.assertIn("C1", high["tdd_allocation"]["evidence"])
        self.assertIn("Slot shortage is not established", high["tdd_allocation"]["evidence"])
        self.cells[0]["peak_prb"] = 60
        self.event["venue_name"] = "Another stadium"
        low = self.checks()
        self.assertEqual(low["radio_capacity"]["status"], "baseline")
        self.assertEqual(low["steering_policy"]["status"], "not_triggered")
        self.assertEqual(low["tdd_allocation"]["status"], "not_triggered")
        self.assertIn("Another stadium", low["radio_capacity"]["evidence"])
        self.assertEqual(low["guardrails"]["status"], "baseline")
        self.assertNotEqual(high, low)

    def test_transport_thresholds_match_demo_limits(self):
        for site, shared, expected in [(74.9, 79.9, "baseline"), (75, 50, "review"),
                                       (50, 80, "review"), (85, 90, "review"),
                                       (85.1, 50, "priority"), (50, 90.1, "priority")]:
            with self.subTest(site=site, shared=shared):
                self.assertEqual(self.checks(site, shared)["transport"]["status"], expected)

    def test_target_gap_does_not_claim_safe_steering(self):
        self.cells[1]["peak_prb"] = 85
        checks = self.checks()
        self.assertEqual(checks["steering_policy"]["status"], "blocked")
        self.assertEqual(checks["physical_capacity"]["status"], "review")
        self.assertIn("No forecast-supported target", checks["physical_capacity"]["evidence"])
        self.assertIn("live", checks["steering_policy"]["evidence"])

    def test_one_target_does_not_cover_all_hot_cells(self):
        self.cells.append({"cell_id": "C3", "peak_prb": 95})
        self.assertIn("C3", self.checks()["physical_capacity"]["evidence"])
        self.assertEqual(self.checks()["physical_capacity"]["status"], "review")

    def test_out_of_venue_and_ineligible_targets_are_not_counted(self):
        self.neighbors[0]["steering_eligibility"] = "ineligible"
        self.neighbors.append({"cell_id": "C1", "neighbor_cell_id": "Outside", "steering_eligibility": "eligible"})
        self.assertEqual(self.checks()["steering_policy"]["status"], "blocked")

    def test_missing_forecasts_fail_explicitly(self):
        self.cells[0]["peak_prb"] = np.nan
        with self.assertRaisesRegex(ValueError, "finite"):
            self.checks()


@unittest.skipUnless(HAS_ARTIFACTS, SKIP_ART)
class TestPopulatedServiceData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from vdi.api import AppState
        cls.state = AppState(auto_train=False)

    def test_persistent_csv_has_one_row_per_cell_and_timestamp(self):
        frame = pd.read_csv(dl.path_of("service_quality"))
        self.assertEqual(len(frame), len(self.state.live["times"]) * len(self.state.live["cells"]))
        self.assertEqual(set(frame.columns), set(sq.SCHEMA))
        self.assertFalse(frame.duplicated(["event_id", "cell_id", "timestamp"]).any())
        self.assertFalse(frame.isna().any().any())
        self.assertEqual(set(frame.data_source), {"synthetic_service_scenario_v2"})
        self.assertTrue(frame.packet_loss_pct.between(0, 100).all())
        self.assertTrue((frame.latency_ms > 0).all())
        self.assertTrue(frame.ul_bler_pct.between(0, 100).all())
        self.assertTrue(frame.harq_retrans_pct.between(0, 100).all())
        self.assertTrue(frame.handover_success_pct.between(0, 100).all())
        self.assertTrue(frame.compute_util_pct.between(0, 100).all())

    def test_advisory_page_explains_issues_and_remedies_without_approval(self):
        for event in self.state.scores["events"][::12]:
            plan = self.state.operations.plan(event["event_id"])
            self.assertEqual(plan["mode"], "advisory_only")
            self.assertFalse(plan["approval_required"])
            self.assertNotIn("approval", plan)
            self.assertNotRegex(json.dumps(plan), r"(?i)\bdemo\b")
            source = dl.events().set_index("event_id").loc[event["event_id"], "connected_devices_est"]
            self.assertIsInstance(plan["connected_devices_est"], int)
            self.assertEqual(plan["connected_devices_est"], source)
            self.assertIn("Not a live device count", plan["connected_devices_method"])
            self.assertGreater(plan["service_forecast"]["latency_ms"], 0)
            self.assertEqual(len(plan["five_minute_profile"]["seconds"]), 145)
            self.assertEqual(len(plan["cell_forecasts"]), len(plan["event"]["cells"]))
            self.assertEqual(len(plan["readiness_checks"]), 8)
            self.assertTrue(all(c["id"] and c["status"] and c["evidence"] for c in plan["readiness_checks"]))
            self.assertIn(plan["event"]["venue_name"],
                          next(c for c in plan["readiness_checks"] if c["id"] == "guardrails")["evidence"])
            self.assertTrue(all(c["aggregate_ul_mbps"] >= 0 and c["user_p05_mbps"] <= c["user_p50_mbps"]
                                for c in plan["cell_forecasts"]))
            for recommendation in plan["recommendations"]:
                for key in ("issue", "evidence", "impact", "remedy", "owner"):
                    self.assertTrue(recommendation[key])
        plan = self.state.operations.plan(config.LIVE_EVENT_ID)
        ids = {row["id"] for row in plan["recommendations"]}
        self.assertTrue({"radio_headroom", "backhaul_capacity", "tdd_slots"} <= ids)
        recommendations = {row["id"]: row for row in plan["recommendations"]}
        self.assertEqual(recommendations["radio_headroom"]["issue"], "Uplink PRB utilization pressure")
        self.assertEqual(recommendations["tdd_slots"]["issue"], "TDD uplink slot allocation readiness")
        self.assertEqual(recommendations["backhaul_capacity"]["issue"], "Backhaul capacity pressure")
        self.assertIn("user throughput", recommendations["radio_headroom"]["impact"])
        self.assertIn("engineer review", recommendations["tdd_slots"]["remedy"])
        self.assertIn("not established", recommendations["tdd_slots"]["evidence"])
