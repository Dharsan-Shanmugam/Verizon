"""HTTP server: JSON API under /api/... + static UI.  python -m vdi.server

Stdlib only (ThreadingHTTPServer).  Payloads are precomputed at startup;
responses are gzip-compressed when the client accepts it.
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import mimetypes
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import config
from . import ai
from . import data_loader as dl
from .pipeline import jsonable

log = logging.getLogger("vdi.server")
STATE = None  # AppState, set in build_state()

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("image/svg+xml", ".svg")


def build_state():
    global STATE
    from .api import AppState
    STATE = AppState()
    return STATE


def _routes():
    S = lambda: STATE  # noqa: E731
    return [
        ("GET", r"/api/operations/plans$", lambda m, q, b: S().operations.plans()),
        ("GET", r"/api/operations/plan/(?P<id>[\w-]+)$", lambda m, q, b: S().operations.plan(m["id"])),
        ("POST", r"/api/operations/plan/(?P<id>[\w-]+)$", lambda m, q, b: S().operations.approve_plan(m["id"], b)),
        ("POST", r"/api/operations/session$", lambda m, q, b: S().operations.start()),
        ("GET", r"/api/operations/live$", lambda m, q, b: S().operations.snapshot(
            q.get("seconds"), q.get("cell_id"), q.get("session_id"))),
        ("POST", r"/api/operations/tick$", lambda m, q, b: S().operations.advance(b)),
        ("POST", r"/api/operations/respond$", lambda m, q, b: S().operations.respond(b)),
        ("GET", r"/api/operations/model$", lambda m, q, b: S().operations.model.report()),
        ("GET", r"/api/ai/status$", lambda m, q, b: ai.status()),
        ("POST", r"/api/ai/models$", lambda m, q, b: ai.models()),
        ("POST", r"/api/ai/explain$", lambda m, q, b: S().ai_explain(b)),
        ("GET", r"/api/health$", lambda m, q, b: S().health()),
        ("GET", r"/api/overview$", lambda m, q, b: S().cache["overview"]),
        ("GET", r"/api/events$", lambda m, q, b: S().cache["events"]),
        ("GET", r"/api/events/(?P<id>[\w-]+)$", lambda m, q, b: S().event_detail(m["id"])),
        ("GET", r"/api/live$", lambda m, q, b: S().live_payload()),
        ("GET", r"/api/live/snapshot$", lambda m, q, b: S().replay.snapshot(q.get("seconds"), q.get("cell_id"))),
        ("GET", r"/api/live/decisions$", lambda m, q, b: {"decisions": S().live["decisions"],
                                                          "approvals": S().live_payload()["approvals"]}),
        ("POST", r"/api/live/decisions/(?P<id>[\w-]+)/(?P<action>approve|reject)$",
         lambda m, q, b: S().record_approval(m["id"], m["action"], b.get("user", "exec-demo"), b.get("comment", ""))),
        ("GET", r"/api/audit$", lambda m, q, b: {"entries": S().audit()}),
        ("GET", r"/api/routing$", lambda m, q, b: S().cache["routing"]),
        ("GET", r"/api/simulate$", lambda m, q, b: S().cache["simulate_default"] if not q else S().simulate(q)),
        ("POST", r"/api/simulate$", lambda m, q, b: S().simulate(b)),
        ("GET", r"/api/simulate/events$", lambda m, q, b: {"events": S().simulate_events()}),
        ("GET", r"/api/gatherings$", lambda m, q, b: S().cache["gatherings"]),
        ("GET", r"/api/gatherings/(?P<id>[\w-]+)/replay$", lambda m, q, b: S().gathering_replay(m["id"])),
        ("GET", r"/api/model-health$", lambda m, q, b: S().cache["model_health"]),
        ("GET", r"/api/metrics$", lambda m, q, b: S().metrics),
        ("GET", r"/api/catalog$", lambda m, q, b: S().cache["catalog"]),
        ("GET", r"/api/catalog/columns$", lambda m, q, b: {"columns": [
            c for c in S().cache["catalog"]["columns"]
            if not q.get("q") or q["q"].lower() in (c["file"] + " " + c["column"] + " " + c["description"]).lower()]}),
    ]


ROUTES = [(meth, re.compile(p), fn) for meth, p, fn in _routes()]


class Handler(BaseHTTPRequestHandler):
    server_version = "VDI/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _send(self, code: int, body: bytes, ctype: str, cache: str = "no-cache"):
        if len(body) > 1024 and "gzip" in self.headers.get("Accept-Encoding", ""):
            body = gzip.compress(body, compresslevel=5)
            self.send_response(code)
            self.send_header("Content-Encoding", "gzip")
        else:
            self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(jsonable(obj), separators=(",", ":")).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _api(self, method: str):
        u = urlparse(self.path)
        denied = False
        if u.path.startswith(("/api/ai/", "/api/operations/")):
            origin = self.headers.get("Origin")
            denied = self.client_address[0] not in ("127.0.0.1", "::1") or bool(
                origin and origin != "http://" + self.headers.get("Host", ""))
            if denied and method != "POST":
                return self._json(403, {"error": "These endpoints are available only from the local dashboard."})
        q = {k: v[-1] for k, v in parse_qs(u.query).items()}
        body = {}
        if method == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            if n > 100_000:
                return self._json(413, {"error": "payload too large"})
            raw = self.rfile.read(n) if n else b""
            # Consume bounded request bytes before rejecting a keep-alive POST.
            if denied:
                return self._json(403, {"error": "These endpoints are available only from the local dashboard."})
            try:
                body = json.loads(raw or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._json(400, {"error": "invalid JSON body"})
        for meth, rx, fn in ROUTES:
            m = rx.match(u.path)
            if m and meth == method:
                t = time.time()
                try:
                    res = fn(m.groupdict(), q, body)
                except ai.AIError as e:
                    log.warning("AI request failed: %s", e)
                    return self._json(e.status, {"error": str(e)})
                except KeyError as e:
                    return self._json(404, {"error": f"not found: {e}"})
                except ValueError as e:
                    return self._json(400, {"error": str(e)})
                except Exception as e:  # pragma: no cover
                    log.exception("API error")
                    return self._json(500, {"error": str(e)})
                self._json(200, res)
                log.info("%s %s %.0f ms", method, u.path, (time.time() - t) * 1000)
                return
        if any(rx.match(u.path) for _, rx, _ in ROUTES):
            return self._json(405, {"error": "method not allowed"})
        return self._json(404, {"error": "unknown endpoint"})

    def _static(self):
        path = unquote(urlparse(self.path).path)
        if path in ("", "/"):
            path = "/index.html"
        root = config.static_dir().resolve()
        fp = (root / path.lstrip("/")).resolve()
        if root not in fp.parents and fp != root or not fp.is_file():
            return self._send(404, b"Not found", "text/plain")
        ctype = mimetypes.guess_type(str(fp))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
            ctype += "; charset=utf-8"
        self._send(200, fp.read_bytes(), ctype)

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self._api("GET")
        return self._static()

    def do_HEAD(self):
        return self.do_GET()

    def do_POST(self):
        if self.path.startswith("/api/"):
            return self._api("POST")
        self._json(405, {"error": "method not allowed"})


def make_server(host: str, port: int) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    return httpd


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="python -m vdi.server")
    ap.add_argument("--host", default=config.host())
    ap.add_argument("--port", type=int, default=config.port())
    a = ap.parse_args(argv)
    missing = dl.check_data()
    if missing:
        log.error("Data folder %s is missing %d files (e.g. %s). Unzip 'Verizon data.zip' into ./data/ "
                  "or set VDI_DATA_DIR.", config.data_dir(), len(missing), missing[0])
        sys.exit(2)
    log.info("loading data + models (first run builds cache/ and trains - a few minutes) ...")
    st = build_state()
    log.info("ready in %.1fs", st.startup_s)
    httpd = make_server(a.host, a.port)
    log.info("Venue Demand Intelligence running on http://%s:%d", "localhost" if a.host in ("127.0.0.1", "0.0.0.0") else a.host, a.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
