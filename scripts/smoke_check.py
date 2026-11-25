"""Hit every API endpoint + static asset of a running server and report status.

Usage:  python scripts/smoke_check.py [base_url]   (default http://127.0.0.1:8050)
Note: it POSTs one approval (user "smoke-test") to the audit log.
Exit code 1 if any check fails.
"""
from __future__ import annotations

import gzip
import json
import sys
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8050").rstrip("/")

GET_JSON = [
    "/api/health", "/api/overview", "/api/events", "/api/events/EVT-LIVE-20261028", "/api/live",
    "/api/live/decisions", "/api/audit", "/api/routing", "/api/simulate",
    "/api/simulate?attendance_pct=120&traffic_moved_pct=20&temp_capacity_mbps=200&home_away=away",
    "/api/simulate/events", "/api/gatherings", "/api/model-health", "/api/metrics", "/api/catalog",
    "/api/catalog/columns?q=prb",
]
STATIC = ["/", "/index.html", "/css/app.css", "/js/app.js", "/js/charts.js", "/assets/logo-placeholder.svg",
          "/assets/favicon.svg"] + [f"/js/views/{v}.js" for v in
                                    ["overview", "forecast", "live", "routing", "simulator", "gatherings", "health", "catalog"]]
EXPECT_404 = ["/api/nope", "/api/events/NOPE", "/../config/settings.json", "/nope.html"]


def fetch(path, data=None):
    req = urllib.request.Request(BASE + path, data=data, headers={"Accept-Encoding": "gzip",
                                                                   "Content-Type": "application/json"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return r.status, body, time.time() - t
    except urllib.error.HTTPError as e:
        return e.code, e.read(), time.time() - t


def main():
    ok = True
    rows = []
    for p in GET_JSON:
        code, body, dt = fetch(p)
        good = code == 200
        try:
            js = json.loads(body)
            good = good and isinstance(js, dict) and "error" not in js
        except ValueError:
            good = False
        rows.append((good, code, len(body), dt, p))
    events = json.loads(fetch("/api/events")[1])["events"]
    for e in events[:3]:
        code, body, dt = fetch("/api/events/" + e["event_id"])
        rows.append((code == 200, code, len(body), dt, "/api/events/" + e["event_id"]))
    g = json.loads(fetch("/api/gatherings")[1])["v01"]
    if g:
        p = f"/api/gatherings/{g[0]['gathering_id']}/replay"
        code, body, dt = fetch(p)
        rows.append((code == 200, code, len(body), dt, p))
    live = json.loads(fetch("/api/live")[1])
    tier2 = [d for d in live["decisions"] if d["tier"].startswith("tier 2")]
    if tier2:
        p = f"/api/live/decisions/{tier2[0]['decision_id']}/approve"
        code, body, dt = fetch(p, json.dumps({"user": "smoke-test"}).encode())
        rows.append((code == 200, code, len(body), dt, "POST " + p))
    code, body, dt = fetch("/api/simulate", json.dumps({"attendance_pct": 110}).encode())
    rows.append((code == 200, code, len(body), dt, "POST /api/simulate"))
    for p in STATIC:
        code, body, dt = fetch(p)
        rows.append((code == 200 and len(body) > 0, code, len(body), dt, p))
    for p in EXPECT_404:
        code, body, dt = fetch(p)
        rows.append((code in (400, 404), code, len(body), dt, p + "  (expect 404)"))
    for good, code, n, dt, p in rows:
        ok &= good
        print(f"{'OK ' if good else 'FAIL'} {code} {n:>9,d} B {dt * 1000:7.0f} ms  {p}")
    print("ALL OK" if ok else "SOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
