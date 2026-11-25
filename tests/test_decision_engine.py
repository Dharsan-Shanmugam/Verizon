"""Decision-engine rules: thresholds, radio block, backhaul block, tiers, verify."""
import unittest

from vdi import decision_engine as de

TH = {"ul_prb_pct_gt": 85, "ul_p10_mbps_lt": 3.0, "consecutive_15s_intervals": 3, "radio_headroom_min_pct": 15,
      "site_backhaul_max_after_move_pct": 85, "aggregation_max_pct": 90, "risk_medium_prb_pct": 70,
      "risk_high_prb_pct": 85, "alert_probability": 0.5}
ST = {"move_fraction": 0.2, "cell_individual_offset_db": -4, "a3_offset_db": [3, 2],
      "ul_load_balance_threshold_pct": [85, 71], "auto_revert_minutes": 30, "verify_window_s": 120, "cooldown_s": 600}


def bh(site_util=None, agg=50.0):
    su = {"S1": 40.0, "S2": 40.0}
    su.update(site_util or {})
    return de.Backhaul(su, {"S1": 10.0, "S2": 5.0}, agg)


def hot(prb=92.0, p10=1.0, run=3, traffic=0.5):
    return de.CellState("C1", "S1", prb, p10, 0.9, 0.95, run, traffic)


class TestDecisionEngine(unittest.TestCase):
    def test_low_risk_monitor_only(self):
        d = de.decide(de.CellState("C1", "S1", 40, 10, 0.01, 0.02, 0), [], bh(), TH, ST)
        self.assertEqual(d.tier, de.TIER0)
        self.assertEqual(d.recommendation, "no_routing")

    def test_risk_levels(self):
        self.assertEqual(de.risk_level(de.CellState("C", "S", 50, 9, 0.0, 0.6, 0), TH), "medium")
        self.assertEqual(de.risk_level(de.CellState("C", "S", 74, 9, 0.0, 0.0, 0), TH), "medium")
        self.assertEqual(de.risk_level(de.CellState("C", "S", 60, 1, 0.7, 0.0, 0), TH), "high")
        self.assertEqual(de.risk_level(de.CellState("C", "S", 88, 2, 0.0, 0.0, 3), TH), "high")

    def test_safe_steer_high_risk_is_tier2(self):
        nb = [de.Neighbor("C2", "S2", 0.9, "eligible", 50.0)]
        d = de.decide(hot(), nb, bh(), TH, ST)
        self.assertEqual(d.recommendation, "steer_to_neighbor")
        self.assertEqual(d.tier, de.TIER2)
        self.assertEqual(d.target_cell_id, "C2")
        params = [a["parameter"] for a in d.actions]
        self.assertIn("cell_individual_offset_db", params)
        self.assertTrue(any(t["step"].startswith("Backhaul check") and t["status"] == "pass" for t in d.trace))

    def test_safe_steer_medium_risk_is_tier1(self):
        nb = [de.Neighbor("C2", "S2", 0.9, "eligible", 40.0)]
        d = de.decide(de.CellState("C1", "S1", 75, 5, 0.1, 0.6, 0, 0.3), nb, bh(), TH, ST)
        self.assertEqual(d.tier, de.TIER1)

    def test_radio_block_falls_back(self):
        nb = [de.Neighbor("C2", "S2", 0.9, "eligible", 90.0)]  # 10 % headroom < 15 %
        d = de.decide(hot(), nb, bh(), TH, ST)
        self.assertEqual(d.recommendation, "fallback")
        self.assertEqual(d.tier, de.TIER3)
        self.assertFalse(d.checks["candidates"][0]["radio_ok"])

    def test_site_backhaul_block(self):
        nb = [de.Neighbor("C2", "S2", 0.9, "eligible", 40.0)]
        # S2: 5 Gbps, 80 % used; moving 0.2*1.0 Gbps adds 4 pts -> 84 % ok; with 2 Gbps traffic -> 88 % blocked
        d_ok = de.decide(hot(traffic=1.0), nb, bh({"S2": 80.0}), TH, ST)
        self.assertEqual(d_ok.recommendation, "steer_to_neighbor")
        d_block = de.decide(hot(traffic=2.0), nb, bh({"S2": 80.0}), TH, ST)
        self.assertEqual(d_block.recommendation, "fallback")
        c = d_block.checks["candidates"][0]
        self.assertTrue(c["radio_ok"])
        self.assertFalse(c["backhaul_ok"])
        self.assertIn("site backhaul", c["reason"])

    def test_aggregation_block(self):
        nb = [de.Neighbor("C2", "S2", 0.9, "eligible", 40.0)]
        d = de.decide(hot(), nb, bh(agg=93.0), TH, ST)
        self.assertEqual(d.tier, de.TIER3)
        self.assertIn("aggregation", d.checks["candidates"][0]["reason"])

    def test_same_site_move_does_not_load_site_link(self):
        nb = [de.Neighbor("C3", "S1", 0.9, "eligible", 40.0)]
        d = de.decide(hot(traffic=5.0), nb, bh({"S1": 84.0}), TH, ST)
        self.assertEqual(d.recommendation, "steer_to_neighbor")
        self.assertTrue(d.checks["chosen"]["same_site_backhaul"])

    def test_restricted_neighbor_skipped_and_next_used(self):
        nb = [de.Neighbor("C2", "S2", 0.99, "restricted", 10.0), de.Neighbor("C4", "S2", 0.5, "eligible", 30.0)]
        d = de.decide(hot(), nb, bh(), TH, ST)
        self.assertEqual(d.target_cell_id, "C4")

    def test_verify_pass_and_rollback(self):
        nb = [de.Neighbor("C2", "S2", 0.9, "eligible", 40.0)]
        d = de.decide(hot(), nb, bh(), TH, ST)
        self.assertEqual(de.verify(d, 90.0, 40.0, TH)["result"], "pass")      # 90*0.8 = 72
        r = de.verify(d, 100.0, 80.0, TH)                                       # target 80+16 = 96
        self.assertEqual(r["result"], "rollback")
        self.assertEqual(r["state"], "rolled_back_fallback")
        self.assertIn("fallback_actions", r)

    def test_trace_is_explainable(self):
        nb = [de.Neighbor("C2", "S2", 0.9, "eligible", 50.0)]
        d = de.decide(hot(), nb, bh(), TH, ST).to_dict()
        steps = [t["step"] for t in d["trace"]]
        self.assertEqual(steps[0], "Threshold check")
        self.assertIn("Approval tier", steps)
        self.assertIn("Action spec", steps)


if __name__ == "__main__":
    unittest.main()
