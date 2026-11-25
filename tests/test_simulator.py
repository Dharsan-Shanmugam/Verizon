"""Digital-twin simulator monotonicity."""
import unittest

from tests._util import HAS_ARTIFACTS, SKIP_ART


@unittest.skipUnless(HAS_ARTIFACTS, SKIP_ART)
class TestSimulator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from vdi import pipeline
        from vdi.simulator import Simulator
        cls.sim = Simulator(pipeline.load_models(), pipeline.load_curves())

    def k(self, side="after", **kw):
        return self.sim.simulate(**kw)["kpis"][side]

    def test_attendance_monotone(self):
        vals = [self.k("before", attendance_pct=a) for a in (60, 80, 100, 120)]
        for a, b in zip(vals, vals[1:]):
            self.assertLessEqual(a["avg_prb"], b["avg_prb"])
            self.assertLessEqual(a["congestion_min"], b["congestion_min"])
            self.assertLessEqual(a["drop_rate"], b["drop_rate"])
            self.assertLessEqual(a["agg_bh"], b["agg_bh"])

    def test_temp_capacity_monotone(self):
        vals = [self.k(temp_capacity_mbps=c) for c in (0, 150, 300, 600)]
        for a, b in zip(vals, vals[1:]):
            self.assertGreaterEqual(a["max_prb"], b["max_prb"])
            self.assertGreaterEqual(a["congestion_min"], b["congestion_min"])

    def test_traffic_moved_reduces_hot_cell_load(self):
        r0 = self.sim.simulate(traffic_moved_pct=0)
        r1 = self.sim.simulate(traffic_moved_pct=25)
        hot = [c["cell_id"] for c in r0["cells"] if c["before"] >= 70]
        a0 = {c["cell_id"]: c["after"] for c in r0["cells"]}
        a1 = {c["cell_id"]: c["after"] for c in r1["cells"]}
        self.assertTrue(hot)
        for c in hot:
            self.assertLessEqual(a1[c], a0[c] + 1e-9)
        self.assertLessEqual(r1["kpis"]["after"]["congestion_min"], r0["kpis"]["after"]["congestion_min"])
        # load is moved, not deleted
        self.assertAlmostEqual(sum(a1.values()) >= sum(a0.values()) * 0.9, True)

    def test_away_crowd_lower_than_home(self):
        self.assertLess(self.k("before", home_away="away")["avg_prb"], self.k("before", home_away="home")["avg_prb"])

    def test_no_mitigation_means_no_change(self):
        r = self.sim.simulate()
        self.assertEqual(r["kpis"]["before"]["max_prb"], r["kpis"]["after"]["max_prb"])


if __name__ == "__main__":
    unittest.main()
