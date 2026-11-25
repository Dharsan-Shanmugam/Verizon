"""API smoke test: start the server on a free port and hit every endpoint."""
import json
import http.client
import os
import shutil
import socket
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from tests._util import HAS_ARTIFACTS, SKIP_ART

AUDIT_TMP = Path(__file__).resolve().parent / "_audit_tmp"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipUnless(HAS_ARTIFACTS, SKIP_ART)
class TestApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["VDI_AUDIT_DIR"] = str(AUDIT_TMP)
        from vdi import server
        server.build_state()
        cls.port = free_port()
        cls.httpd = server.make_server("127.0.0.1", cls.port)
        cls.th = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.th.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        os.environ.pop("VDI_AUDIT_DIR", None)
        shutil.rmtree(AUDIT_TMP, ignore_errors=True)

    def get(self, path, data=None):
        req = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def jget(self, path, data=None):
        code, body = self.get(path, data)
        self.assertEqual(code, 200, path)
        return json.loads(body)

    def test_every_endpoint(self):
        for p in ["/api/health", "/api/overview", "/api/events", "/api/live", "/api/live/decisions", "/api/audit",
                  "/api/routing", "/api/simulate", "/api/simulate?attendance_pct=110&traffic_moved_pct=20",
                  "/api/simulate/events", "/api/gatherings", "/api/model-health", "/api/metrics", "/api/catalog",
                  "/api/catalog/columns?q=uplink"]:
            js = self.jget(p)
            self.assertIsInstance(js, dict, p)
        ev = self.jget("/api/events")["events"]
        self.assertGreater(len(ev), 10)
        d = self.jget("/api/events/" + ev[0]["event_id"])
        self.assertIn("heatmap", d)
        g = self.jget("/api/gatherings")["v01"]
        r = self.jget(f"/api/gatherings/{g[0]['gathering_id']}/replay")
        self.assertEqual(len(r["t"]), len(r["prb_max"]))

    def test_overview_has_six_kpis_and_13_venues(self):
        o = self.jget("/api/overview")
        self.assertEqual(len(o["kpis"]), 6)
        self.assertEqual(len(o["venue_board"]), 13)
        self.assertEqual(len(o["headlines"]), 3)

    def test_catalog_lists_38_files_including_service_scenarios(self):
        catalog = self.jget("/api/catalog")
        self.assertEqual(len(catalog["files"]), 38)
        quality = next(row for row in catalog["files"] if row["file"].startswith("36_"))
        self.assertGreater(quality["rows"], 0)
        self.assertIn("synthetic", quality["source"])

    def test_approve_reject_audit(self):
        live = self.jget("/api/live")
        dec = live["decisions"][0]["decision_id"]
        r = self.jget(f"/api/live/decisions/{dec}/approve", json.dumps({"user": "unittest"}).encode())
        self.assertEqual(r["action"], "approve")
        r = self.jget(f"/api/live/decisions/{dec}/reject", json.dumps({"user": "unittest"}).encode())
        self.assertEqual(r["action"], "reject")
        entries = self.jget("/api/audit")["entries"]
        self.assertEqual(entries[0]["decision_id"], dec)
        self.assertEqual(self.jget("/api/live")["approvals"][dec]["action"], "reject")
        code, _ = self.get("/api/live/decisions/NOPE/approve", b"{}")
        self.assertEqual(code, 404)

    def test_simulate_post(self):
        r = self.jget("/api/simulate", json.dumps({"attendance_pct": 120, "temp_capacity_mbps": 300}).encode())
        self.assertLessEqual(r["kpis"]["after"]["max_prb"], r["kpis"]["before"]["max_prb"])

    def test_live_snapshot_and_event_drilldown(self):
        live = self.jget("/api/live")
        self.assertEqual(live["source"]["mode"], "synthetic_replay")
        snap = self.jget(f"/api/live/snapshot?seconds={live['times'][0]}&cell_id={live['cells'][-1]}")
        self.assertEqual(snap["cell_id"], live["cells"][-1])
        self.assertIsNone(snap["errors"]["adaptive60"]["mae"])
        self.assertEqual(len(snap["routing"]), 8)
        for query in ("", "?seconds=nan", "?seconds=1e9", "?seconds=0&cell_id=UNKNOWN"):
            self.assertEqual(self.get("/api/live/snapshot" + query)[0], 400)
        events = {event["event_id"] for event in self.jget("/api/events")["events"]}
        for venue in self.jget("/api/overview")["venue_board"]:
            for event in venue["timeline"]:
                self.assertIn(event["event_id"], events)

    def test_executive_operations_and_two_tab_shell(self):
        live = self.jget("/api/live")
        plan = self.jget("/api/operations/plan/" + live["event_id"])
        self.assertTrue(plan["recommendations"])
        self.assertFalse(plan["approval_required"])
        self.assertEqual(self.get("/api/operations/plan/" + live["event_id"], b'{"action":"approve"}')[0], 400)
        self.assertEqual(self.jget("/api/operations/plans")["mode"], "advisory_only")
        session = self.jget("/api/operations/session", b"{}")
        body = {"session_id": session["id"], "seconds": session["seconds"], "cell_id": live["cells"][0]}
        tick = self.jget("/api/operations/tick", json.dumps(body).encode())
        self.assertEqual(tick["session"]["id"], session["id"])
        self.assertEqual([row["minutes"] for row in tick["outlook"]], [0, 5, 10, 15, 20, 25, 30])
        self.assertTrue(all(0 <= row["congestion_probability"] <= 1 for row in tick["outlook"][1:]))
        report = self.jget("/api/operations/model")
        self.assertEqual(len(report["split"]["test"]), 8)
        self.assertIn("1200", report["horizons"])
        self.assertEqual(self.get("/api/operations/respond", json.dumps({
            "session_id": session["id"], "cell_id": live["cells"][0], "action": "approve"}).encode())[0], 400)
        self.assertEqual(self.get("/api/operations/tick", b'{"session_id":"bad","seconds":0}')[0], 400)
        request = urllib.request.Request(self.base + "/api/operations/session", data=b"{}",
                                         headers={"Origin": "https://other.example"})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(error.exception.code, 403)
        html = self.get("/")[1].decode()
        self.assertEqual(html.count('data-view="'), 2)
        self.assertIn("executive-forecast.js", html)
        self.assertIn("executive-live.js", html)

    def test_static_and_errors(self):
        for p in ["/", "/css/app.css", "/js/app.js", "/js/charts.js", "/js/views/live.js", "/assets/logo-placeholder.svg"]:
            code, body = self.get(p)
            self.assertEqual(code, 200, p)
            self.assertGreater(len(body), 100)
        self.assertEqual(self.get("/api/unknown")[0], 404)
        self.assertEqual(self.get("/api/events/NOPE")[0], 404)
        self.assertEqual(self.get("/%2e%2e/config/settings.json")[0], 404)
        self.assertEqual(self.get("/api/live/decisions/x/approve", b"not json")[0], 400)

    def test_denied_operations_post_does_not_corrupt_keep_alive_connection(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.request("POST", "/api/operations/session", body=b"{}",
                               headers={"Origin": "https://other.example", "Content-Type": "application/json"})
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
            connection.request("GET", "/api/health")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
        finally:
            connection.close()

    def test_ai_routes_without_real_provider_calls(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "ANTHROPIC_MODEL": ""}):
            status = self.jget("/api/ai/status")
            self.assertFalse(status["configured"])
            self.assertTrue(status["advisory_only"])
            self.assertEqual(self.get("/api/ai/models", b"{}")[0], 503)
        with patch("vdi.ai.models", return_value={"models": [{"id": "test-model", "name": "Test"}]}):
            self.assertEqual(self.jget("/api/ai/models", b"{}")["models"][0]["id"], "test-model")
            req = urllib.request.Request(self.base + "/api/ai/models", data=b"{}",
                                         headers={"Origin": "https://other.example"})
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(req, timeout=10)
            self.assertEqual(error.exception.code, 403)
        with patch("vdi.ai.explain", return_value={"text": "Synthetic advice", "advisory_only": True}) as explain:
            body = json.dumps({"view": "overview", "model": "test-model", "question": "Summarize"}).encode()
            answer = self.jget("/api/ai/explain", body)
            self.assertEqual(answer["text"], "Synthetic advice")
            self.assertEqual(explain.call_args.args[0]["view"], "overview")
        self.assertEqual(self.get("/api/ai/models")[0], 405)
        self.assertEqual(self.get("/api/ai/explain", b'{"view":"invalid"}')[0], 400)


if __name__ == "__main__":
    unittest.main()
