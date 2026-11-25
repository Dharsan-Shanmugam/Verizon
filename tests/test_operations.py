"""Deterministic operational gates, persistence, approval and counterfactual validation."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

from tests._util import HAS_ARTIFACTS, SKIP_ART
from vdi.operations import workflow_status


class TestWorkflowStatus(unittest.TestCase):
    def setUp(self):
        self.issue = {"cell_id": "C01", "tier": 2}
        self.action = {"id": "a1", "cell_id": "C01", "target": "C02",
                       "seconds": 0, "verify_at": 120, "status": "verifying"}

    def status(self, seconds=0, actions=None):
        return workflow_status(self.issue, {"actions": actions or []}, seconds, 3600)

    def test_approval_moves_review_to_validation_and_counts_down(self):
        self.assertEqual(self.status()["state"], "engineer_review")
        self.assertTrue(self.status()["can_approve"])
        before = self.status(30, actions=[self.action])
        self.assertEqual(before["state"], "verifying")
        self.assertEqual(before["remaining_s"], 90)
        self.assertEqual(before["progress_pct"], 25)
        self.assertFalse(before["needs_review"])
        self.assertFalse(before["can_approve"])
        self.assertFalse(before["can_respond"])

    def test_validation_retains_visible_success_even_if_alarm_clears(self):
        self.action["status"] = "verified"
        self.issue["tier"] = 0
        result = self.status(120, actions=[self.action])
        self.assertEqual(result["state"], "verified")
        self.assertEqual(result["progress_pct"], 100)
        self.assertEqual(result["action_id"], "a1")
        self.assertEqual(self.status(600, actions=[self.action])["state"], "monitoring")
        self.assertIsNone(self.status(600, actions=[self.action])["action_id"])
        self.issue["tier"] = 2
        fresh = self.status(600, actions=[self.action])
        self.assertEqual(fresh["state"], "engineer_review")
        self.assertIsNone(fresh["action_id"])

    def test_holds_failures_and_target_reservations_cannot_look_approvable(self):
        for status in ("rejected", "escalated", "rolled_back", "validation_blocked"):
            self.action["status"] = status
            result = self.status(900, actions=[self.action])
            self.assertEqual(result["state"], status)
            self.assertFalse(result["can_approve"])
        self.action["status"] = "verifying"
        self.issue["cell_id"] = "C02"
        result = self.status(30, actions=[self.action])
        self.assertEqual(result["state"], "reserved")
        self.assertFalse(result["can_approve"])

    def test_new_critical_issue_is_not_hidden_by_verified_cooldown(self):
        self.action["status"] = "verified"
        self.issue["tier"] = 3
        self.assertEqual(self.status(180, actions=[self.action])["state"], "escalation_required")

    def test_only_live_tier_controls_approval_and_recording_end(self):
        self.assertEqual(self.status()["state"], "engineer_review")
        self.assertEqual(self.status(3500)["state"], "recording_ending")
        self.assertFalse(self.status(3500)["can_approve"])


@unittest.skipUnless(HAS_ARTIFACTS, SKIP_ART)
class TestOperations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from vdi.api import AppState
        cls.temp = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {"VDI_AUDIT_DIR": cls.temp.name}):
            cls.state = AppState(auto_train=False)
        cls.ops = cls.state.operations

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.ops.data = {"plans": {}, "sessions": {}}
        self.original = copy.deepcopy(self.state.live["series"])
        self.backhaul = copy.deepcopy(self.state.live["backhaul"]["util"])
        for series in self.state.live["series"].values():
            series["prb"] = [25.0] * len(series["prb"])
            series["p10"] = [8.0] * len(series["p10"])
        for key in self.state.live["backhaul"]["util"]:
            self.state.live["backhaul"]["util"][key] = [40.] * len(self.state.live["times"])
        self.cell = self.state.live["cells"][0]
        self.seconds = self.state.live["times"][20]
        self.event_id = self.state.live["event_id"]

    def tearDown(self):
        self.state.live["series"] = self.original
        self.state.live["backhaul"]["util"] = self.backhaul

    def pressure(self, load=75):
        series = self.state.live["series"][self.cell]
        series["prb"] = [float(load)] * len(series["prb"])
        series["p10"] = [1.] * len(series["p10"])

    def snapshot(self):
        return self.ops.snapshot(self.seconds, self.cell)

    def tick(self, session, seconds=None):
        return self.ops.advance({"session_id": session["id"], "seconds": self.seconds if seconds is None else seconds,
                                 "cell_id": self.cell})

    def test_automatic_policy_needs_no_preparation_approval(self):
        self.pressure()
        session = self.ops.start()
        result = self.tick(session)
        self.assertEqual(len(result["session"]["actions"]), 1)
        self.assertTrue(result["session"]["actions"][0]["automatic"])
        self.assertEqual(result["session"]["actions"][0]["mode"], "simulation")
        self.assertEqual(result["issues"][0]["workflow"]["state"], "verifying")

    def test_persistence_duration_ranked_candidates_and_post_move_limits(self):
        self.pressure()
        result = self.snapshot()
        issue = result["issues"][0]
        self.assertEqual(issue["tier"], 1)
        self.assertTrue(issue["candidates"])
        for rank, candidate in enumerate(issue["candidates"], 1):
            self.assertEqual(candidate["rank"], rank)
            self.assertGreaterEqual(candidate["headroom_after"], 15)
            self.assertLessEqual(candidate["site_after"], 85)
            self.assertLessEqual(candidate["agg_after"], 90)
        self.state.live["series"][self.cell]["prb"][19] = 50
        self.assertEqual(self.snapshot()["issues"][0]["tier"], 0)

    def test_engineer_approval_is_rechecked_and_automatic_changes_are_not_duplicated(self):
        self.pressure(95)
        session = self.ops.start()
        result = self.tick(session)
        self.assertEqual(result["issues"][0]["tier"], 2)
        self.assertEqual(result["session"]["actions"], [])
        body = {"session_id": session["id"], "cell_id": self.cell, "action": "approve",
                "review_token": result["issues"][0]["review_token"]}
        approved = self.ops.respond(body)
        self.assertFalse(approved["automatic"])
        with self.assertRaisesRegex(ValueError, "cooldown"):
            self.ops.respond(body)
        self.tick(session, self.seconds + 120)
        self.assertEqual(session["actions"][0]["status"], "verified")
        self.assertEqual(session["actions"][0]["validation"]["result"], "pass")
        self.assertEqual(self.ops.snapshot(self.seconds + 120, self.cell, session["id"])["issues"][0]["workflow"]["state"], "verified")
        self.assertEqual(self.state.live["series"][self.cell]["prb"][28], 95)

    def test_no_headroom_and_transport_pressure_escalate(self):
        self.pressure(95)
        for series in self.state.live["series"].values():
            series["prb"] = [95.] * len(series["prb"])
        issue = self.snapshot()["issues"][0]
        self.assertEqual(issue["tier"], 3)
        self.assertIsNone(issue["remedy"])
        agg = next(key for key in self.state.live["backhaul"]["links"] if key.endswith("-AGG"))
        self.state.live["backhaul"]["util"][agg] = [95.] * len(self.state.live["times"])
        issue = self.snapshot()["issues"][0]
        self.assertEqual(issue["rule"], "TRANSPORT_CAPACITY")
        self.assertEqual(issue["tier"], 3)

    def test_failed_validation_blocks_retries_and_becomes_escalation(self):
        self.pressure()
        session = self.ops.start()
        self.tick(session)
        action = session["actions"][0]
        self.state.live["series"][action["target"]]["prb"][28] = 95
        self.tick(session, self.seconds + 120)
        self.assertEqual(action["status"], "rolled_back")
        result = self.ops.snapshot(self.seconds + 120, self.cell, session["id"])
        self.assertEqual(result["issues"][0]["tier"], 3)
        self.tick(session, self.seconds + 720)
        self.assertEqual(len(session["actions"]), 1)

    def test_planning_is_advisory_and_legacy_gate_cannot_be_used(self):
        self.assertEqual(self.ops.plan(self.event_id)["mode"], "advisory_only")
        self.assertFalse(self.ops.plan(self.event_id)["approval_required"])
        self.assertTrue(self.ops.plan(self.event_id)["recommendations"])
        for action in ("approve", "reject"):
            with self.assertRaisesRegex(ValueError, "advisory only"):
                self.ops.approve_plan(self.event_id, {"action": action})

    def test_rewind_invalid_session_unknown_cell_and_unavailable_metrics(self):
        session = self.ops.start()
        result = self.tick(session)
        self.assertGreaterEqual(result["quality"]["packet_loss"], 0)
        self.assertGreater(result["quality"]["latency"], 0)
        self.assertEqual(result["quality"]["source"], "synthetic_service_scenario_v2")
        self.assertEqual([p["minutes"] for p in result["outlook"]], [0, 5, 10, 15, 20, 25, 30])
        with self.assertRaisesRegex(ValueError, "rewind"):
            self.tick(session, self.seconds - 15)
        current = self.ops.snapshot(self.seconds - 15, self.cell, session["id"])
        self.assertEqual(current["seconds"], session["seconds"])
        with self.assertRaises(ValueError):
            self.ops.snapshot(self.seconds, "unknown")
        with self.assertRaises(ValueError):
            self.ops.advance({"session_id": "bad", "seconds": self.seconds})
        with self.assertRaises(ValueError):
            self.ops.approve_plan(self.event_id, {"action": "arbitrary"})
        with self.assertRaises(ValueError):
            self.ops.snapshot(True, self.cell)

    def test_bad_samples_block_automatic_actions(self):
        self.pressure()
        session = self.ops.start()
        self.state.live["series"][self.cell]["prb"][20] = float("nan")
        result = self.tick(session)
        self.assertEqual(result["issues"][0]["rule"], "DATA_QUALITY")
        self.assertEqual(result["issues"][0]["tier"], 3)
        self.assertEqual(session["actions"], [])

    def test_invalid_validation_data_cannot_claim_success(self):
        self.pressure()
        session = self.ops.start()
        self.tick(session)
        action = session["actions"][0]
        self.state.live["series"][action["target"]]["prb"][28] = float("nan")
        result = self.tick(session, self.seconds + 120)
        self.assertEqual(action["status"], "validation_blocked")
        self.assertEqual(result["issues"][0]["tier"], 3)
        json.dumps(json.loads(self.ops.file.read_text()), allow_nan=False)

    def test_human_hold_cannot_be_bypassed_by_automatic_retry(self):
        self.pressure(95)
        session = self.ops.start()
        self.tick(session)
        body = {"session_id": session["id"], "cell_id": self.cell, "action": "reject"}
        first = self.ops.respond(body)
        self.assertEqual(self.ops.respond(body)["id"], first["id"])
        self.assertEqual(len(session["actions"]), 1)
        self.pressure(75)
        result = self.tick(session, self.seconds + 720)
        self.assertEqual(len(session["actions"]), 1)
        self.assertEqual(result["issues"][0]["tier"], 3)

    def test_investigation_approval_requires_current_evidence(self):
        self.pressure(95)
        session = self.ops.start()
        result = self.tick(session)
        body = {"session_id": session["id"], "cell_id": self.cell, "action": "approve"}
        with self.assertRaisesRegex(ValueError, "Investigation changed"):
            self.ops.respond(body)
        body["review_token"] = result["issues"][0]["review_token"]
        self.tick(session, self.seconds + 15)
        with self.assertRaisesRegex(ValueError, "Investigation changed"):
            self.ops.respond(body)
        self.assertEqual(session["actions"], [])
        fresh = self.ops.snapshot(self.seconds + 15, self.cell, session["id"])
        body["review_token"] = fresh["issues"][0]["review_token"]
        self.assertEqual(self.ops.respond(body)["tier"], 2)

    def test_investigation_cannot_approve_automatic_or_unsafe_rank(self):
        self.pressure()
        session = self.ops.start()
        # Inspect without advancing the automatic simulation.
        session["seconds"] = self.seconds
        issue = self.ops.snapshot(self.seconds, self.cell, session["id"])["issues"][0]
        self.assertEqual(issue["tier"], 1)
        body = {"session_id": session["id"], "cell_id": self.cell, "action": "approve",
                "review_token": issue["review_token"]}
        with self.assertRaisesRegex(ValueError, "Tier 2"):
            self.ops.respond(body)
        for series in self.state.live["series"].values():
            series["prb"] = [95.] * len(series["prb"])
        issue = self.ops.snapshot(self.seconds, self.cell, session["id"])["issues"][0]
        self.assertEqual(issue["tier"], 3)
        body["review_token"] = issue["review_token"]
        with self.assertRaisesRegex(ValueError, "Tier 2"):
            self.ops.respond(body)
        self.assertEqual(session["actions"], [])

    def test_ranked_alternatives_and_live_case_history_are_persisted(self):
        self.pressure()
        issue = self.snapshot()["issues"][0]
        self.assertGreaterEqual(len(issue["candidates"]), 2)
        self.assertEqual(issue["candidates"][0]["fraction"], .1)
        self.assertGreaterEqual(len(issue["hypotheses"]), 4)
        self.assertTrue(any(r["tier"] == 3 and not r["executable"] for r in issue["remedies"]))
        session = self.ops.start()
        result = self.tick(session)
        action = session["actions"][0]
        case = session["cases"][action["case_id"]]
        self.assertEqual(case["title"], "Radio surge")
        self.assertEqual(action["parameters"][0]["new"], 10)
        self.assertTrue({"detected", "ranked", "agent_authorized", "parameters_changed"}
                        <= {entry["kind"] for entry in case["timeline"]})
        self.tick(session, self.seconds + 30)
        self.assertEqual(action["status"], "verifying")
        self.assertEqual(len(action["validation_samples"]), 2)
        self.tick(session, self.seconds + 120)
        self.assertEqual(action["status"], "verified")
        self.assertEqual(len(action["validation_samples"]), 8)
        self.assertEqual(case["timeline"][-1]["kind"], "verified")
        persisted = json.loads(self.ops.file.read_text())
        self.assertEqual(persisted["sessions"][session["id"]]["cases"][case["id"]]["status"], "verified")
        self.assertEqual(action["validation"]["source_before"], 75)
        self.assertEqual(self.state.live["series"][self.cell]["prb"][28], 75)

    def test_engineer_can_choose_an_eligible_ranked_alternative(self):
        self.pressure(90)
        session = self.ops.start()
        result = self.tick(session)
        issue = result["issues"][0]
        self.assertEqual(issue["review_token"], self.ops.snapshot(self.seconds, self.cell, session["id"])["issues"][0]["review_token"])
        self.assertGreaterEqual(len(issue["candidates"]), 2)
        selected = issue["candidates"][1]
        action = self.ops.respond({"session_id": session["id"], "cell_id": self.cell,
                                   "action": "approve", "review_token": issue["review_token"],
                                   "choice_id": selected["choice_id"]})
        self.assertEqual(action["remedy"]["choice_id"], selected["choice_id"])
        self.assertEqual(action["parameters"], selected["parameters"])
        self.assertFalse(action["automatic"])
        case = session["cases"][action["case_id"]]
        self.assertTrue(any(t["kind"] == "engineer_approved" for t in case["timeline"]))

    def test_unsafe_live_check_rolls_back_before_final_validation(self):
        self.pressure()
        session = self.ops.start()
        self.tick(session)
        action = session["actions"][0]
        self.state.live["series"][action["target"]]["prb"][21] = 95
        self.tick(session, self.seconds + 15)
        self.assertEqual(action["status"], "rolled_back")
        self.assertEqual(action["validation"]["at"], self.seconds + 15)
        self.assertEqual(len(action["validation_samples"]), 1)
        timeline = session["cases"][action["case_id"]]["timeline"]
        self.assertTrue(any(t["kind"] == "rolled_back" and t["seconds"] == self.seconds + 15 for t in timeline))
        self.tick(session, self.seconds + 120)
        self.assertEqual(len(action["validation_samples"]), 1)
        self.assertEqual(len(session["actions"]), 1)

    def test_tdd_issue_requires_engineer_approval_and_validates_service(self):
        self.cell = "V01-C04"
        self.pressure(60)
        quality = self.ops.quality[self.cell].copy()
        quality["latency_ms"], quality["packet_loss_pct"] = 70., 2.
        quality["tdd_ul_share_pct"], quality["tdd_ul_demand_pct"] = 20., 60.
        quality["tdd_alignment_ok"] = True
        raw = self.ops.raw[self.cell].copy()
        raw["dl_prb_pct"] = 40.
        with patch.dict(self.ops.quality, {self.cell: quality}), patch.dict(self.ops.raw, {self.cell: raw}):
            session = self.ops.start()
            result = self.tick(session)
            issue = next(row for row in result["issues"] if row["cell_id"] == self.cell)
            self.assertEqual(issue["rule"], "TDD_UPLINK")
            self.assertEqual(issue["tier"], 2)
            self.assertEqual(issue["remedy"]["id"], "tdd_ul_profile")
            self.assertEqual(session["actions"], [])
            action = self.ops.respond({"session_id": session["id"], "cell_id": self.cell,
                                       "action": "approve", "review_token": issue["review_token"]})
            self.assertIsNone(action["target"])
            self.tick(session, self.seconds + 120)
            self.assertEqual(action["status"], "verified")
            self.assertLessEqual(action["validation"]["service_after"]["latency_ms"], 50)
            self.assertLessEqual(action["validation"]["service_after"]["packet_loss_pct"], 1)
            self.assertEqual(action["intent"]["approval"], "engineer_required")
            self.assertIn("Simulation", action["intent"]["adapter"])

    def test_poor_radio_quality_is_not_misdiagnosed_as_capacity(self):
        self.pressure(95)
        quality = self.ops.quality[self.cell].copy()
        quality["pusch_sinr_db"], quality["ul_bler_pct"], quality["harq_retrans_pct"] = -2., 25., 40.
        quality["rx_interference_dbm"], quality["latency_ms"], quality["packet_loss_pct"] = -82., 75., 2.
        with patch.dict(self.ops.quality, {self.cell: quality}):
            issue = self.snapshot()["issues"][0]
            self.assertEqual(issue["rule"], "RADIO_ERRORS")
            self.assertEqual(issue["tier"], 3)
            self.assertIsNone(issue["remedy"])
            self.assertEqual({g["id"] for g in issue["diagnostic_groups"]}, {"demand", "radio", "mobility", "transport"})
            self.assertEqual(issue["diagnostic_groups"][1]["assessment"], "Degraded")

    def test_neighbor_mobility_and_availability_gate_steering(self):
        self.pressure()
        quality = {cell: rows.copy() for cell, rows in self.ops.quality.items()}
        for cell, rows in quality.items():
            if cell != self.cell:
                rows["handover_success_pct"] = 50.
        with patch.dict(self.ops.quality, quality):
            issue = self.snapshot()["issues"][0]
            self.assertEqual(issue["tier"], 3)
            self.assertEqual(issue["candidates"], [])

    def test_historical_evidence_and_intents_are_not_invented_commands(self):
        self.pressure()
        issue = self.snapshot()["issues"][0]
        remedy = issue["remedy"]
        evidence = remedy["historical_evidence"]
        self.assertGreater(evidence["matches"], 0)
        self.assertLessEqual(evidence["successes"], evidence["matches"])
        self.assertIn("synthetic", evidence["source"])
        self.assertEqual(remedy["intent"]["action"], "apply_preapproved_temporary_steering_profile")
        self.assertNotIn("vendor_command", remedy["intent"])
        missing = self.ops.outcome_evidence.lookup("unsupported_action", "V01", 75, {})
        self.assertEqual(missing["matches"], 0)
        self.assertIsNone(missing["success_rate_pct"])
        self.assertEqual(missing["confidence"], "Low")

    def test_forecast_watch_triggers_read_only_rca_not_execution(self):
        risks = {h: np.ones_like(values) for h, values in self.ops.risk_outlooks[self.cell].items()}
        with patch.dict(self.ops.risk_outlooks, {self.cell: risks}):
            session = self.ops.start()
            result = self.tick(session)
            issue = next(i for i in result["issues"] if i["cell_id"] == self.cell)
            self.assertEqual(issue["rule"], "FORECAST_WATCH")
            self.assertEqual(issue["tier"], 0)
            self.assertEqual(issue["workflow"]["state"], "forecast_watch")
            self.assertFalse(issue["workflow"]["can_approve"])
            self.assertEqual(session["actions"], [])
            case = session["cases"][session["active_cases"][self.cell]]
            for h, values in risks.items():
                risks[h] = np.zeros_like(values)
            self.tick(session, self.seconds + 15)
            self.assertEqual(case["status"], "watch_cleared")
            self.assertNotIn(self.cell, session["active_cases"])

    def test_tdd_alignment_or_downlink_failure_escalates(self):
        self.cell = "V01-C04"
        self.pressure(60)
        quality = self.ops.quality[self.cell].copy()
        quality["latency_ms"], quality["packet_loss_pct"] = 70., 2.
        quality["tdd_ul_share_pct"], quality["tdd_ul_demand_pct"] = 20., 60.
        quality["tdd_alignment_ok"] = False
        with patch.dict(self.ops.quality, {self.cell: quality}):
            issue = next(row for row in self.snapshot()["issues"] if row["cell_id"] == self.cell)
            self.assertEqual(issue["rule"], "TDD_UPLINK")
            self.assertEqual(issue["tier"], 3)
            self.assertIsNone(issue["remedy"])
