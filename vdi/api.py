"""Payload builders for the JSON API (pure functions over data + artifacts)."""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import business_value as bv
from . import ai
from . import config
from . import data_loader as dl
from . import monitoring as mon
from . import pipeline
from .pipeline import jsonable
from .simulator import Simulator
from .live import LiveReplay
from .operations import Operations
from . import service_quality as sq


def _fmt_money(x):
    return f"${x / 1e6:.1f}M" if x >= 1e6 else f"${x:,.0f}"


def downsample(n: int, max_points: int) -> np.ndarray:
    if n <= max_points:
        return np.arange(n)
    return np.unique(np.linspace(0, n - 1, max_points).round().astype(int))


class AppState:
    """Everything the server needs, precomputed once at startup."""

    def __init__(self, auto_train: bool = True):
        t0 = time.time()
        if not pipeline.artifacts_ready():
            if not auto_train:
                raise RuntimeError("artifacts missing - run `python -m vdi.pipeline train`")
            pipeline.train()
        art = config.artifacts_dir()
        if not (art / "scores.json").exists() or not (art / "live.json").exists():
            pipeline.score()
        self.models = pipeline.load_models()
        self.curves = pipeline.load_curves()
        self.metrics = json.loads((art / "metrics.json").read_text())
        self.scores = json.loads((art / "scores.json").read_text())
        self.live = json.loads((art / "live.json").read_text())
        live_event = next(event for event in self.scores["events"] if event["event_id"] == config.LIVE_EVENT_ID)
        self.replay = LiveReplay(self.live, live_event)
        self.sim = Simulator(self.models, self.curves)
        self.lock = threading.Lock()
        self.audit_file = config.audit_dir() / "approvals.jsonl"
        self.approvals = self._load_approvals()
        self.value = bv.summary()
        self.operations = Operations(self)
        self.cache: dict[str, object] = {}
        for name, fn in [("overview", self.overview), ("events", self.events_list), ("routing", self.routing),
                         ("gatherings", self.gatherings), ("model_health", self.model_health),
                         ("catalog", self.catalog), ("simulate_default", lambda: self.simulate({}))]:
            self.cache[name] = jsonable(fn())
        self.startup_s = round(time.time() - t0, 2)

    # ------------------------------------------------------------ audit
    def _load_approvals(self) -> dict:
        out = {}
        if self.audit_file.exists():
            for line in self.audit_file.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                    out[r["decision_id"]] = r
                except Exception:
                    continue
        return out

    def record_approval(self, decision_id: str, action: str, user: str = "exec-demo", comment: str = "") -> dict:
        dec = next((d for d in self.live["decisions"] if d["decision_id"] == decision_id), None)
        if dec is None:
            raise KeyError(decision_id)
        if action not in ("approve", "reject"):
            raise ValueError("action must be approve or reject")
        rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "decision_id": decision_id,
               "action": action, "user": str(user)[:60], "comment": str(comment)[:300], "cell_id": dec["cell_id"],
               "tier": dec["tier"], "recommendation": dec["recommendation"], "target": dec["target_cell_id"],
               "event_id": self.live["event_id"]}
        with self.lock:
            with open(self.audit_file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
            self.approvals[decision_id] = rec
        return rec

    def audit(self) -> list:
        if not self.audit_file.exists():
            return []
        rows = [json.loads(x) for x in self.audit_file.read_text(encoding="utf-8").splitlines() if x.strip()]
        return rows[-200:][::-1]

    def live_payload(self) -> dict:
        lv = dict(self.live)
        lv["approvals"] = {k: {"action": v["action"], "ts": v["ts"], "user": v["user"]}
                           for k, v in self.approvals.items()}
        return lv

    def ai_explain(self, body: dict) -> dict:
        view = body.get("view")
        if not isinstance(view, str) or view not in (
                "overview", "forecast", "live", "routing", "simulator", "gatherings", "health", "catalog"):
            raise ValueError("Select a supported dashboard view.")
        question = body.get("question", "Summarize the key facts, risks, and safe next steps.")
        model = body.get("model") or ai.status()["model"]
        if not isinstance(question, str) or not question.strip() or len(question) > 2000:
            raise ValueError("Question must contain 1-2000 characters.")
        if not isinstance(model, str) or not model.strip() or len(model) > 150:
            raise ValueError("Load models and select an available Claude model ID.")
        selection = body.get("selection", {})
        if not isinstance(selection, dict):
            raise ValueError("Selection must be an object.")
        history = ai.validate_history(body.get("history", []))
        context = {"view": view, "synthetic": True, "as_of": config.TODAY}
        if view == "forecast":
            event_id = selection.get("event_id")
            if not isinstance(event_id, str):
                raise ValueError("Select an event before explaining its forecast.")
            event = self.event_detail(event_id)
            context["facts"] = {k: v for k, v in event.items() if k != "heatmap"}
        elif view == "simulator":
            context["facts"] = self.simulate(selection)
            comparison = selection.get("comparison")
            if comparison is not None:
                if not isinstance(comparison, dict):
                    raise ValueError("Comparison must contain scenario inputs.")
                context["comparison"] = self.simulate(comparison)
        elif view == "live":
            seconds = selection.get("seconds")
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not np.isfinite(seconds):
                raise ValueError("Select a finite live replay position.")
            times = self.live["times"]
            if seconds < times[0] or seconds > times[-1]:
                raise ValueError("Replay position is outside the recorded stream.")
            index = int(np.searchsorted(times, seconds, side="right") - 1)
            decisions = []
            decision_id = selection.get("decision_id")
            for decision in self.live["decisions"]:
                if decision["t"] > seconds or (decision_id and decision["decision_id"] != decision_id):
                    continue
                record = {k: v for k, v in decision.items() if k != "verify"}
                if decision["verify_t"] <= seconds:
                    record["simulated_verify"] = decision["verify"]
                record["operator_response"] = self.approvals.get(decision["decision_id"], {}).get("action")
                decisions.append(record)
            if decision_id and not decisions:
                raise ValueError("Selected decision is not visible at this replay position.")
            context["facts"] = {
                "event_id": self.live["event_id"], "seconds": times[index],
                "cells": {cell: {key: values[index] for key, values in series.items()}
                          for cell, series in self.live["series"].items()},
                "backhaul_util": {key: values[index] for key, values in self.live["backhaul"]["util"].items()},
                "decisions": decisions[-3:], "execution": "simulation only",
            }
            context["facts"]["current_snapshot"] = self.replay.snapshot(seconds, selection.get("cell_id"))
        elif view == "catalog":
            catalog = self.cache["catalog"]
            context["facts"] = {"files": catalog["files"], "documented_columns": len(catalog["columns"])}
        else:
            cache_key = "model_health" if view == "health" else view
            facts = self.cache[cache_key]
            if view == "overview":
                venue, risk = selection.get("venue", "all"), selection.get("risk", "all")
                if risk not in ("all", "high", "medium", "low", "none"):
                    raise ValueError("Select a supported risk filter.")
                board = [row for row in facts["venue_board"]
                         if (venue == "all" or row["venue_id"] == venue)
                         and (risk == "all" or row["risk"] == risk)]
                facts = {**facts, "venue_board": board, "filters": {"venue": venue, "risk": risk},
                         "scope_note": "KPI tiles, growth and headlines are portfolio-wide; board is filtered."}
            if view == "routing":
                facts = {k: v for k, v in facts.items() if k not in ("live_backhaul", "actions")}
            elif view == "gatherings":
                facts = {k: v for k, v in facts.items() if k != "v01"}
            elif view == "health":
                facts = {k: v for k, v in facts.items() if k != "history"}
                model_history = self.cache["model_health"]["history"]
                facts["history"] = {**model_history, "series": model_history["series"][-12:]}
            context["facts"] = facts
        if history:
            return ai.explain(jsonable(context), question.strip(), model.strip(), history=history)
        return ai.explain(jsonable(context), question.strip(), model.strip())

    # ------------------------------------------------------------ overview
    def overview(self) -> dict:
        cf, tc, en, ug = (self.value[k] for k in ("counterfactual", "temp_capacity", "engineer", "gatherings"))
        pk = self.metrics["peak_uplink"]["test"]
        kpis = [
            {"id": "p10", "label": "Uplink P10 uplift", "value": cf["p10_uplift_mbps"], "unit": "Mbps", "prefix": "+",
             "sub": f"Worst-served 10% of users: {cf['p10_no_action_mbps']} → {cf['p10_with_action_mbps']} Mbps when the PoC acts"},
            {"id": "drops", "label": "Drop-rate reduction", "value": cf["drop_reduction_pct"], "unit": "%",
             "sub": f"Session drops {cf['drop_no_action_pct']}% → {cf['drop_with_action_pct']}% on actioned cells"},
            {"id": "cong", "label": "Congestion minutes avoided", "value": cf["congestion_min_avoided"], "unit": "min",
             "sub": f"{cf['congestion_avoided_pct']}% fewer cell-minutes, {cf['events_covered']:,} events {cf['period']}"},
            {"id": "cost", "label": "Temporary capacity cost avoidable", "value": tc["avoidable_cost_usd"], "unit": "USD",
             "sub": f"≈{_fmt_money(tc['avoidable_cost_per_year_usd'])}/yr · {tc['unneeded_deployments']:,} rule-driven deployments not needed"},
            {"id": "hours", "label": "Engineer hours saved", "value": en["hours_saved"], "unit": "h",
             "sub": f"{en['manual_min_per_decision']} → {en['poc_min_per_decision']} min per decision · ≈{en['hours_saved_per_year']:,.0f} h/yr"},
            {"id": "acc", "label": "Forecast accuracy", "value": round(100 - pk["mape_pct"], 1), "unit": "%",
             "sub": f"Peak uplink per cell, 2025–26 hold-out · {pk['interval_coverage'] * 100:.0f}% inside 90% band"},
        ]
        tm = dl.traffic_mix()
        ay = tm["app_year"].groupby("year")[["ul_gb", "dl_gb"]].sum()
        n_ev = tm["event"][tm["event"]["ul_gb"] > 0].groupby("year")["event_id"].nunique()
        growth = [{"year": int(y), "ul_share": round(100 * r.ul_gb / (r.ul_gb + r.dl_gb), 2),
                   "dl_ul_ratio": round(r.dl_gb / max(r.ul_gb, 1e-9), 2), "ul_tb": round(r.ul_gb / 1000, 1),
                   "ul_gb_per_event": int(round(r.ul_gb / max(int(n_ev.get(y, 1)), 1)))}
                  for y, r in ay.iterrows()]
        apps = tm["app_year"]
        tot = apps.groupby("year")["ul_gb"].transform("sum")
        apps = apps.assign(share=100 * apps["ul_gb"] / tot)
        order = apps[apps["year"] == apps["year"].max()].sort_values("share", ascending=False)["app_category"].tolist()
        app_mix = {"apps": order, "years": sorted(int(y) for y in apps["year"].unique()),
                   "share": {a: [round(float(v), 2) for v in apps[apps["app_category"] == a].sort_values("year")["share"]]
                             for a in order}}
        board = self.venue_board()
        evs = self.scores["events"]
        high = [e for e in evs if e["risk"] == "high"]
        hottest = max(evs, key=lambda e: (e["peak_prb_max"], e["hot_cells"])) if evs else None
        g0, g1 = growth[0], growth[-1]
        gains = {a: app_mix["share"][a][-1] - app_mix["share"][a][0] for a in app_mix["apps"]}
        top_app = max(gains, key=gains.get)
        top_share, top_share0 = app_mix["share"][top_app][-1], app_mix["share"][top_app][0]
        sc = dl.small("safety_checks")
        blocked = int((~sc["steer_safe"]).sum())
        bh_blocked = int((sc["radio_ok"] & ~sc["backhaul_ok"]).sum())
        headlines = [
            f"Uplink per event grew {g1['ul_gb_per_event'] / max(g0['ul_gb_per_event'], 1e-9):.1f}x since {g0['year']} "
            f"({g0['ul_gb_per_event']:,.0f} → {g1['ul_gb_per_event']:,.0f} GB); {top_app} now drives "
            f"{top_share:.0f}% of venue uplink (from {top_share0:.0f}%).",
            f"{len(high)} of the next {len(evs)} events are forecast high-risk for uplink congestion"
            + (f"; {hottest['event_name']} at {hottest['venue_name']} has {hottest['hot_cells']} cells forecast above 85%."
               if hottest else "."),
            f"Backhaul-aware steering avoided {cf['congestion_min_avoided']:,.0f} congestion minutes in replay and "
            f"blocked {bh_blocked:,} moves that radio checks alone would have allowed onto overloaded transport.",
        ]
        lv = self.live
        return {"kpis": kpis, "growth": growth, "app_mix": app_mix, "venue_board": board, "headlines": headlines,
                "takeaway": f"Predicting uplink demand per cell and steering only where backhaul has room cuts drops by "
                            f"{cf['drop_reduction_pct']:.0f}% and makes {_fmt_money(tc['avoidable_cost_usd'])} of temporary capacity avoidable.",
                "live": {"event_id": lv["event_id"], "decisions": len(lv["decisions"]), "alerts": len(lv["alerts"])},
                "blocked_total": blocked}

    def venue_board(self) -> list:
        ven = dl.small("venues")
        evs = self.scores["events"]
        rank = {"low": 0, "medium": 1, "high": 2}
        out = []
        for v in ven.itertuples():
            ve = [e for e in evs if e["venue_id"] == v.venue_id]
            nxt = min(ve, key=lambda e: e["start"]) if ve else None
            worst = max(ve, key=lambda e: (rank[e["risk"]], e["peak_prb_max"])) if ve else None
            out.append({"venue_id": v.venue_id, "venue_name": v.venue_name, "city": v.city, "state": v.state,
                        "is_target": bool(v.is_target_venue), "seats": int(v.seating_capacity), "cells": int(v.num_cells),
                        "events_30d": len(ve), "high_risk_events": sum(e["risk"] == "high" for e in ve),
                        "next_event": nxt["event_name"] if nxt else None, "next_start": nxt["start"] if nxt else None,
                        "max_peak_prb": max((e["peak_prb_max"] for e in ve), default=None),
                        "max_hot_cells": max((e["hot_cells"] for e in ve), default=0),
                        "risk": worst["risk"] if worst else "none",
                        "timeline": [{"event_id": e["event_id"], "start": e["start"], "risk": e["risk"], "name": e["event_name"]} for e in
                                     sorted(ve, key=lambda e: e["start"])]})
        out.sort(key=lambda r: (-{"none": -1, **rank}[r["risk"]], -r["max_hot_cells"], -r["high_risk_events"],
                                -(r["max_peak_prb"] or 0)))
        return out

    # ------------------------------------------------------------ events
    def events_list(self) -> dict:
        keys = ["event_id", "venue_id", "venue_name", "city", "event_name", "category", "subtype", "home_away", "start",
                "status", "capacity", "expected_attendance", "attendance_pred", "attendance_low", "attendance_high",
                "peak_prb_max", "p_over_85", "risk", "hot_cells", "watch_cells", "provided_forecast_peak"]
        evs = [{k: e[k] for k in keys} for e in self.scores["events"]]
        att = self.metrics["attendance"]["test"]
        return {"events": evs, "model_version": self.scores["model_version"],
                "attendance_test": att,
                "takeaway": f"{sum(e['risk'] == 'high' for e in evs)} of {len(evs)} upcoming events need an uplink plan; "
                            f"attendance forecasts are within ±{att['mape_pct']:.1f}% on average (hold-out)."}

    def event_detail(self, event_id: str) -> dict:
        e = next((x for x in self.scores["events"] if x["event_id"] == event_id), None)
        if e is None:
            raise KeyError(event_id)
        return e

    # ------------------------------------------------------------ routing
    def routing(self) -> dict:
        sc = dl.small("safety_checks")
        dlog = dl.small("decisions")[["decision_id", "operator_approved", "verify_result"]]
        m = sc.merge(dlog, on="decision_id", how="left")
        st = m[m["original_recommendation"] == "steer_to_neighbor"]
        executed = st[st["steer_safe"] & st["operator_approved"].fillna(False).astype(bool)]
        funnel = [
            {"stage": "Steer recommended", "n": int(len(st))},
            {"stage": "Radio check passed", "n": int(st["radio_ok"].sum())},
            {"stage": "Backhaul check passed", "n": int(st["steer_safe"].sum())},
            {"stage": "Executed", "n": int(len(executed))},
            {"stage": "Verified", "n": int((executed["verify_result"] == "pass").sum())},
            {"stage": "Rolled back", "n": int((executed["verify_result"] == "rollback").sum())},
        ]
        reasons = sc.loc[~sc["steer_safe"], "check_reason"].value_counts()
        tiers = sc["approval_tier"].value_counts()
        tier0 = int((dl.small("effort")["approval_tier"] == "tier 0 - monitor only").sum())
        b = dl.backhaul()["event_link"]
        lvl = b.groupby(["year", "link_level"])["peak_util"].agg(
            p50="median", p90=lambda s: s.quantile(0.9), over90=lambda s: (s >= 90).mean() * 100).reset_index()
        bh_years = {}
        for r in lvl.itertuples():
            bh_years.setdefault(int(r.year), {})[r.link_level] = {"p50": round(r.p50, 1), "p90": round(r.p90, 1),
                                                                 "over90": round(r.over90, 1)}
        links = dl.small("bh_links")
        v01 = links[links["venue_id"] == config.TARGET_VENUE]
        lv = self.live
        idx = downsample(len(lv["times"]), 240)
        live_bh = {"t": [lv["times"][i] for i in idx],
                   "links": {k: [v[i] for i in idx] for k, v in lv["backhaul"]["util"].items()}}
        act = dl.small("actions")
        recent = act.sort_values("applied_at", ascending=False).head(60)
        acts = [{k: (None if pd.isna(v) else v) for k, v in r.items()} for r in recent.drop(columns=["data_source"]).to_dict("records")]
        by_param = act.groupby("parameter").agg(n=("action_id", "size"),
                                                rollback=("revert_reason", lambda s: (s != "scheduled auto-revert").mean() * 100)).reset_index()
        cnt_live = {}
        for d in lv["decisions"]:
            cnt_live[d["tier"]] = cnt_live.get(d["tier"], 0) + 1
        bh_block = int((sc["radio_ok"] & ~sc["backhaul_ok"]).sum())
        return {
            "funnel": funnel,
            "blocked_reasons": [{"reason": k, "n": int(v)} for k, v in reasons.items()],
            "tiers": [{"tier": k, "n": int(v)} for k, v in tiers.items()] + [{"tier": "tier 0 - monitor only", "n": tier0}],
            "backhaul_by_year": [{"year": y, **{f"{lv_}_{k}": v for lv_, d in vals.items() for k, v in d.items()}}
                                 for y, vals in sorted(bh_years.items())],
            "v01_links": [{k: (None if pd.isna(v) else v) for k, v in r.items()} for r in v01.drop(columns=["data_source"]).to_dict("records")],
            "live_backhaul": live_bh, "live_capacity": lv["backhaul"]["capacity_gbps"],
            "actions": acts,
            "actions_by_param": [{"parameter": r.parameter, "n": int(r.n), "rollback_pct": round(r.rollback, 1)} for r in by_param.itertuples()],
            "live_tiers": cnt_live,
            "takeaway": f"{bh_block:,} steering moves passed the radio check but would have overloaded backhaul - "
                        f"the transport-aware check stopped them and fell back safely.",
        }

    # ------------------------------------------------------------ simulator
    def simulate(self, q: dict) -> dict:
        def num(k, d, lo, hi):
            try:
                return float(np.clip(float(q.get(k, d)), lo, hi))
            except (TypeError, ValueError):
                return d
        ha = q.get("home_away") or None
        ha = ha if ha in ("home", "away", "neutral") else None
        res = self.sim.simulate(event_id=q.get("event_id") or config.LIVE_EVENT_ID,
                                attendance_pct=num("attendance_pct", 100.0, 30, 130),
                                home_away=ha,
                                traffic_moved_pct=num("traffic_moved_pct", 0.0, 0, 50),
                                temp_capacity_mbps=num("temp_capacity_mbps", 0.0, 0, 1000))
        b, a = res["kpis"]["before"], res["kpis"]["after"]
        res["takeaway"] = (f"Scenario: {res['attendance']['scenario']:,} fans. Mitigation moves peak uplink from "
                           f"{b['max_prb']:.0f}% to {a['max_prb']:.0f}% and congestion from {b['congestion_min']:,.0f} to "
                           f"{a['congestion_min']:,.0f} cell-minutes; busiest site backhaul {a['site_bh_max']:.0f}%.")
        return res

    def simulate_events(self) -> list:
        return [{"event_id": e["event_id"], "label": f"{e['start'][:10]} · {e['venue_name']} · {e['event_name']}",
                 "home_away": e["home_away"]} for e in self.scores["events"] if e["category"] in ("football", "concert", "rally", "other")]

    # ------------------------------------------------------------ gatherings
    def gatherings(self) -> dict:
        g = self.value["gatherings"]
        raw = dl.small("gatherings")
        tel = dl.gathering_tel()
        have = set(tel["gathering_id"].astype(str).unique())
        v01 = raw[(raw["venue_id"] == config.TARGET_VENUE) & raw["gathering_id"].isin(have)] \
            .sort_values("ul_surge_vs_baseline_pct", ascending=False)
        lst = [{"gathering_id": r.gathering_id, "type": r.gathering_type, "start": r.start_time,
                "peak_crowd": int(r.peak_crowd_est), "zone": r.location_zone, "social": bool(r.social_spike_detected),
                "social_lead_min": None if pd.isna(r.social_lead_minutes) else float(r.social_lead_minutes),
                "network_lead_s": float(r.network_model_lead_s), "surge_pct": float(r.ul_surge_vs_baseline_pct),
                "peak_prb": float(r.peak_ul_prb_pct), "congestion": bool(r.congestion_occurred), "action": r.poc_action}
               for r in v01.itertuples()]
        leads = raw[["social_spike_detected", "social_lead_minutes", "network_model_lead_s"]]
        return {**g, "v01": lst,
                "lead_compare": {"social_min": float(leads["social_lead_minutes"].median()),
                                 "network_s": float(leads["network_model_lead_s"].median()),
                                 "network_only_share": round(100 * float((~leads["social_spike_detected"]).mean()), 1)},
                "takeaway": f"{g['count']} unplanned crowds hit venues; social spikes gave ~{g['social_lead_min_median']:.0f} min warning "
                            f"for {g['social_detected_pct']:.0f}% of them, and network signals alone still warned ~{g['network_lead_s_median']:.0f} s ahead."}

    def gathering_replay(self, gid: str) -> dict:
        tel = dl.gathering_tel()
        t = tel[tel["gathering_id"] == gid]
        if t.empty:
            raise KeyError(gid)
        agg = t.groupby("seconds_from_start", observed=True).agg(
            prb_max=("ul_prb_pct", "max"), prb_mean=("ul_prb_pct", "mean"), p10_min=("ul_tput_p10_mbps", "min"),
            rrc=("rrc_connected", "sum"), cong=("congestion_flag", "max")).reset_index()
        idx = downsample(len(agg), config.settings()["ui"]["max_points_per_series"])
        agg = agg.iloc[idx]
        r = dl.small("gatherings").set_index("gathering_id").loc[gid]
        first_cong = t.loc[t["congestion_flag"], "seconds_from_start"].min()
        first_cong = None if pd.isna(first_cong) else int(first_cong)
        base = first_cong if first_cong is not None else 0
        return {"gathering_id": gid, "type": r["gathering_type"], "start": r["start_time"],
                "t": agg["seconds_from_start"].astype(int).tolist(), "prb_max": agg["prb_max"].round(1).tolist(),
                "prb_mean": agg["prb_mean"].round(1).tolist(), "p10_min": agg["p10_min"].round(2).tolist(),
                "rrc": agg["rrc"].round(0).tolist(), "cong": agg["cong"].astype(int).tolist(),
                "first_congestion_s": first_cong,
                "network_warning_s": base - float(r["network_model_lead_s"]),
                "social_warning_s": None if pd.isna(r["social_lead_minutes"]) else -60.0 * float(r["social_lead_minutes"]),
                "peak_crowd": int(r["peak_crowd_est"]), "action": r["poc_action"]}

    # ------------------------------------------------------------ model health
    def model_health(self) -> dict:
        m = self.metrics
        hist = mon.history_monitoring()
        live = mon.live_drift()
        alerts = []
        for d in m["monitoring"]["drift"]:
            if d["status"] in ("watch", "alert"):
                alerts.append({"severity": "critical" if d["status"] == "alert" else "warning",
                               "text": f"PSI {d['psi']:.2f} on {d['feature']} (train ≤{config.TRAIN_END_YEAR} vs 2025–26)"})
        pk = m["peak_uplink"]["test"]
        if pk["interval_coverage"] < pk["nominal_coverage"] - 0.05:
            alerts.append({"severity": "warning", "text": f"Interval coverage {pk['interval_coverage']:.0%} below nominal {pk['nominal_coverage']:.0%}"})
        for ep in hist["episodes"]:
            alerts.append({"severity": "info", "text": f"Historical drift {ep['start']} → {ep['end']}: {ep['note']} (PSI {ep['max_psi_prb']})"})
        if live["alert_count"]:
            alerts.append({"severity": "warning", "text": f"{live['alert_count']} live drift alerts on {config.LIVE_EVENT_ID}"})
        else:
            alerts.append({"severity": "ok", "text": f"No live drift alerts on {config.LIVE_EVENT_ID} (forecast MAE {self.live['forecast_mae']} PRB pts)"})
        return {"metrics": m, "history": hist, "live_drift": live, "provided": mon.provided_forecast_accuracy(),
                "alerts": alerts,
                "takeaway": f"Peak-uplink model: {pk['mape_pct']:.1f}% MAPE and {pk['interval_coverage']:.0%} band coverage on unseen "
                            f"2025–26 events; 60-s congestion warning AUC {m['short_horizon']['test']['60s']['auc']:.3f}."}

    # ------------------------------------------------------------ catalog
    def catalog(self) -> dict:
        prov = dl.small("provenance")
        dic = dl.small("dictionary")
        rows = []
        for f in sorted(dl.FILES.values()):
            if f == dl.FILES["service_quality"]:
                frame = pd.read_csv(dl.path_of("service_quality"))
                rows.append({"file": f, "contents": "Synthetic RTT, packet loss and TDD / RF scenarios",
                             "grain": "cell x 15-second sample", "coverage": "Recorded current synthetic event",
                             "rows": len(frame), "columns": len(frame.columns), "source": "synthetic scenario augmentation",
                             "generated_on": None, "n_dictionary_columns": len(sq.SCHEMA)})
                continue
            p = prov[prov["file"] == f]
            cols = dic[dic["file"] == f]
            if len(p):
                r = p.iloc[0]
                rows.append({"file": f, "contents": r["contents"], "grain": r["granularity"], "coverage": r["time_coverage"],
                             "rows": int(r["row_count"]), "columns": int(r["column_count"]), "source": r["source_type"],
                             "generated_on": r["generated_on"]})
            else:
                df = dl.small("dictionary" if f.startswith("00_data_d") else "provenance")
                rows.append({"file": f, "contents": "Data dictionary: every column with type and description"
                             if "dictionary" in f else "Provenance: contents, grain, coverage and row counts per file",
                             "grain": "per column" if "dictionary" in f else "per file", "coverage": "—",
                             "rows": int(len(df)), "columns": int(df.shape[1]), "source": "metadata", "generated_on": None})
            rows[-1]["n_dictionary_columns"] = int(len(cols))
        return {"files": rows, "columns": [{"file": r.file, "column": r.column, "type": r.data_type,
                                            "description": r.description} for r in dic.itertuples()]
                + [{"file": dl.FILES["service_quality"], "column": name, "type": dtype, "description": description}
                   for name, (dtype, description) in sq.SCHEMA.items()],
                "data_dir": str(config.data_dir()),
                "takeaway": f"{len(rows)} synthetic files - venues, events, network KPIs, backhaul, decisions and outcomes - "
                            f"with {len(dic):,} documented columns."}

    def health(self) -> dict:
        return {"status": "ok", "version": config.VERSION, "model_version": config.MODEL_VERSION,
                "startup_s": self.startup_s, "data_dir": str(config.data_dir()),
                "artifacts": str(config.artifacts_dir()), "synthetic": True, "as_of": config.TODAY,
                "live_event_id": config.LIVE_EVENT_ID, "target_venue": config.TARGET_VENUE}
