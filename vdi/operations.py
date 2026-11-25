"""Persisted, deterministic demo preparation and incident workflows. No RAN calls."""
from __future__ import annotations

from datetime import datetime, timezone
import copy
import hashlib
import json
import threading
import uuid

import numpy as np

from . import config, data_loader as dl
from .models.outlook import OutlookModel, ordered
from . import service_quality as sq
from . import investigations as iv
from . import assurance


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def observed_true(value):
    return isinstance(value, (bool, np.bool_)) and bool(value)


def workflow_status(issue, session, seconds, recording_end):
    """Presentation state follows persisted actions, not just the current alarm."""
    actions = session["actions"] if session else []
    own = [a for a in actions if a["cell_id"] == issue["cell_id"] and a["seconds"] <= seconds]
    latest = own[-1] if own else None
    status, label, tone, needs_review = "monitoring", "Monitoring", "", False
    remaining, progress = 0, 0
    held = next((a for a in reversed(own) if a["status"] in
                 ("rejected", "escalated", "rolled_back", "validation_blocked")), None)
    if held:
        latest = held
        status = held["status"]
        label = {"rejected": "On hold", "escalated": "Escalated", "rolled_back": "Rolled back",
                 "validation_blocked": "Validation blocked"}[status]
        tone = "danger" if status != "rejected" else "amber"
        needs_review = status in ("rolled_back", "validation_blocked")
    elif latest and latest["status"] == "verifying":
        status, label, tone = "verifying", "Validating simulation", "amber"
        remaining = max(0, latest["verify_at"] - seconds)
        progress = min(100, max(0, round(100 * (seconds - latest["seconds"]) / 120)))
    elif latest and latest["status"] == "verified" and seconds < latest["seconds"] + 600 and issue["tier"] < 3:
        status, label, tone, progress = "verified", "Verified simulation", "good", 100
    else:
        reservation = next((a for a in reversed(actions)
                            if 0 <= seconds - a["seconds"] < 600
                            and issue["cell_id"] in (a["cell_id"], a["target"])), None)
        if reservation and issue["tier"] != 3:
            status, label, tone = "reserved", "Cell reserved", "amber"
        elif issue["tier"]:
            if issue["tier"] == 3:
                status, label, tone, needs_review = "escalation_required", "Escalation required", "danger", True
            elif seconds + 120 > recording_end:
                status, label, tone = "recording_ending", "Validation window unavailable", "amber"
            elif issue["tier"] == 2:
                status, label, tone, needs_review = "engineer_review", "Awaiting engineer", "amber", True
            else:
                status, label, tone = "automatic_ready", "Automatic policy ready", "good"
        elif issue.get("forecast_watch"):
            status, label, tone = "forecast_watch", "Forecast watch / read-only RCA", "amber"
    return {"state": status, "label": label, "tone": tone, "needs_review": needs_review,
            "can_approve": status == "engineer_review", "can_respond": status in (
                "engineer_review", "automatic_ready", "escalation_required", "rolled_back", "validation_blocked"),
            "action_id": latest["id"] if latest and status in (
                "verifying", "verified", "rejected", "escalated", "rolled_back", "validation_blocked") else None,
            "remaining_s": remaining, "progress_pct": progress}


def review_token(issue, session, seconds):
    evidence = {"session": session["id"] if session else None, "seconds": seconds,
                "quality": issue["quality"], "rule": issue["rule"], "tier": issue["tier"],
                "observed": issue["observed"], "p10": issue["p10"],
                "site": issue["site_util"], "aggregation": issue["agg_util"],
                "candidates": issue["candidates"], "workflow": issue["workflow"]}
    return hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()


class Operations:
    def __init__(self, state):
        self.state = state
        self.lock = threading.RLock()
        self.file = config.audit_dir() / "operations.json"
        self.data = json.loads(self.file.read_text()) if self.file.exists() else {"plans": {}, "sessions": {}}
        for session in self.data["sessions"].values():
            session.setdefault("cases", {})
            session.setdefault("active_cases", {})
        self.model = OutlookModel.load_or_train()
        frame = ordered(dl.live_stream())
        predictions = self.model.predict(frame)
        risks = self.model.predict_risk(frame)
        self.outlooks = {}
        self.risk_outlooks = {}
        self.raw = {}
        for cell, rows in frame.groupby("cell_id", sort=False, observed=True):
            if not np.array_equal(rows["seconds_from_start"].to_numpy(), state.live["times"]):
                raise ValueError(f"Telemetry/outlook timestamp alignment failed for {cell}.")
            indices = rows.index.to_numpy()
            self.outlooks[str(cell)] = {h: values[indices] for h, values in predictions.items()}
            self.risk_outlooks[str(cell)] = {h: values[indices] for h, values in risks.items()}
            self.raw[str(cell)] = rows.reset_index(drop=True)
        quality = sq.ensure_live_data(state.live, state.replay)
        self.quality = {}
        self.recommendation_cache = {}
        self.outcome_evidence = assurance.OutcomeEvidence()
        for cell, rows in quality.groupby("cell_id", sort=False):
            rows = rows.sort_values("timestamp").reset_index(drop=True)
            if len(rows) != len(state.live["times"]) or not np.array_equal(
                    pd_times(rows["timestamp"]) - pd_times([state.live["event_start"]])[0], state.live["times"]):
                raise ValueError(f"Service-quality timestamp alignment failed for {cell}.")
            self.quality[cell] = rows

    def save(self):
        temp = self.file.with_suffix(".tmp")
        temp.write_text(json.dumps(self.data, allow_nan=False), encoding="utf-8")
        temp.replace(self.file)

    def plan(self, event_id):
        if event_id not in self.recommendation_cache:
            self.recommendation_cache[event_id] = sq.recommendations(self.state, event_id)
        return copy.deepcopy(self.recommendation_cache[event_id])

    def plans(self):
        return {"approvals": {}, "mode": "advisory_only"}

    def approve_plan(self, event_id, body):
        raise ValueError("Planning is advisory only. Approvals belong to live Tier 2 remedies.")

    def start(self):
        with self.lock:
            if len(self.data["sessions"]) >= 100:
                raise ValueError("Replay session limit reached. Reuse an existing session.")
            session_id = uuid.uuid4().hex
            starts = []
            for series in self.state.live["series"].values():
                pressure = np.flatnonzero((np.asarray(series["prb"]) >= 70) & (np.asarray(series["p10"]) < 3))
                if len(pressure):
                    starts.append(int(pressure[0]))
            start_index = max(0, min(starts) - 8) if starts else 0
            session = {"id": session_id, "seconds": int(self.state.live["times"][start_index]),
                       "actions": [], "cases": {}, "active_cases": {}, "created_at": utcnow()}
            self.data["sessions"][session_id] = session
            self.save()
            return session

    def session(self, session_id):
        if not isinstance(session_id, str) or session_id not in self.data["sessions"]:
            raise ValueError("Start a valid replay session.")
        return self.data["sessions"][session_id]

    def issue(self, cell, index, snapshot):
        series = self.state.live["series"][cell]
        row = next(item for item in snapshot["routing"] if item["cell_id"] == cell)
        window = slice(max(0, index - 2), index + 1)
        values = np.array(series["prb"][window], float)
        speed = np.array(series["p10"][window], float)
        rule = None
        tier = 0
        reason = "Within operating envelope"
        raw_window = self.raw[cell].iloc[max(0, index - 2):index + 1]
        cadence = pd_times(raw_window["timestamp"])
        if (not np.isfinite(values).all() or not np.isfinite(speed).all()
                or (len(cadence) > 1 and not np.all(np.diff(cadence) == 15))):
            rule, tier, reason = "DATA_QUALITY", 3, "Missing or invalid telemetry; automatic changes blocked"
        elif len(values) == 3 and np.all(values >= 70) and np.all(speed < 3):
            rule = "RADIO_PRESSURE"
            tier = 2 if np.any(values >= 85) else 1
            reason = "3 consecutive samples: radio load >=70% and P10 <3 Mbps"
        bh = self.state.live["backhaul"]
        site = self.state.replay.cells.loc[cell, "site_id"]
        link = self.state.replay.site_links[site]
        agg = next(key for key in bh["links"] if key.endswith("-AGG"))
        site_values = np.array(bh["util"][link][window], float)
        agg_values = np.array(bh["util"][agg][window], float)
        if not np.isfinite(site_values).all() or not np.isfinite(agg_values).all():
            rule, tier, reason = "DATA_QUALITY", 3, "Invalid transport readings; automatic changes blocked"
        elif len(site_values) == 3 and (np.all(site_values > 85) or np.all(agg_values > 90)):
            rule, tier, reason = "TRANSPORT_CAPACITY", 3, "Sustained backhaul limit breach; no alternate path configured"
        quality_window = self.quality[cell].iloc[max(0, index - 2):index + 1]
        quality = self.quality[cell].iloc[index].to_dict()
        metric_columns = ["latency_ms", "packet_loss_pct", "transport_latency_ms", "transport_packet_loss_pct",
                          "pusch_sinr_db", "ul_bler_pct", "harq_retrans_pct", "compute_util_pct", "handover_success_pct"]
        service_bad = len(quality_window) == 3 and bool((
            (quality_window["latency_ms"] > sq.LATENCY_LIMIT_MS)
            | (quality_window["packet_loss_pct"] > sq.LOSS_LIMIT_PCT)).all())
        tdd_gap = quality["tdd_ul_demand_pct"] - quality["tdd_ul_share_pct"]
        if not np.isfinite(quality_window[metric_columns].to_numpy(float)).all():
            rule, tier, reason = "DATA_QUALITY", 3, "Missing service-quality samples; automatic changes blocked"
        elif not isinstance(quality["cell_available"], (bool, np.bool_)):
            rule, tier, reason = "DATA_QUALITY", 3, "Cell availability evidence is missing or invalid"
        elif rule != "DATA_QUALITY" and not observed_true(quality["cell_available"]):
            rule, tier, reason = "AVAILABILITY", 3, "Cell unavailable; hardware/availability diagnosis required"
        elif rule != "DATA_QUALITY" and quality["compute_util_pct"] >= 90 and service_bad:
            rule, tier, reason = "COMPUTE_PRESSURE", 3, "High processing load with service deterioration; radio steering is not a proven compute remedy"
        elif rule != "DATA_QUALITY" and (service_bad or rule == "RADIO_PRESSURE") and (quality["pusch_sinr_db"] < 5 or quality["ul_bler_pct"] > 10 or quality["harq_retrans_pct"] > 15):
            rule = "RADIO_ERRORS" if quality["rx_interference_dbm"] > -95 else "COVERAGE"
            tier, reason = 3, "Poor uplink SINR with BLER/retransmission overhead; distinguish interference from coverage, not capacity alone"
        elif rule != "DATA_QUALITY" and rule != "TRANSPORT_CAPACITY" and service_bad:
            if bool(((quality_window["transport_latency_ms"] > 30)
                     | (quality_window["transport_packet_loss_pct"] > 1)).all()):
                rule, tier, reason = "TRANSPORT_QUALITY", 3, "Sustained transport latency / loss; investigate path capacity and queues"
            elif tdd_gap >= 10:
                rule, tier, reason = "TDD_UPLINK", 2, "Sustained latency / loss with uplink demand exceeding configured TDD slots"
            elif bool(quality["interference_flag"]):
                rule, tier, reason = "RADIO_ERRORS", 3, "Synthetic RF impairment increases retransmission delay and packet loss"
            elif rule is None:
                rule, tier, reason = "SERVICE_DEGRADATION", 3, "Persistent latency / packet loss without a safe matched automatic playbook"
        candidates = []
        if rule == "RADIO_PRESSURE":
            replay, live = self.state.replay, self.state.live
            fraction = config.steering()["move_fraction"]
            moved = row["observed_prb"] * fraction
            total = sum(live["series"][c]["prb"][index] for c in live["cells"]
                        if replay.cells.loc[c, "site_id"] == site)
            traffic = replay.traffic[site][index] * row["observed_prb"] / max(total, 1e-6) * fraction
            for neighbor in replay.neighbors[replay.neighbors["cell_id"] == cell].itertuples():
                target = neighbor.neighbor_cell_id
                if neighbor.steering_eligibility != "eligible" or target not in live["series"]:
                    continue
                target_site = replay.cells.loc[target, "site_id"]
                target_diagnostics = self.quality[target].iloc[index]
                if not observed_true(target_diagnostics["cell_available"]) or not np.isfinite(
                        [target_diagnostics["pusch_sinr_db"], target_diagnostics["handover_success_pct"]]).all() or target_diagnostics["pusch_sinr_db"] < 5 or target_diagnostics["handover_success_pct"] < 95:
                    continue
                target_link = replay.site_links[target_site]
                same_site = target_site == site
                target_after = live["series"][target]["prb"][index] + moved * .8
                capacity = bh["capacity_gbps"][target_link]
                if not np.isfinite([target_after, capacity, bh["util"][target_link][index]]).all() or capacity <= 0:
                    continue
                site_after = bh["util"][target_link][index] + (
                    0 if same_site else 100 * traffic / capacity)
                if not np.isfinite([site_after, traffic, agg_values[-1]]).all() or target_after > 85 or site_after > 85 or agg_values[-1] > 90:
                    continue
                target_quality = self.project_quality(target, index, target_after)
                if target_quality["latency_ms"] > sq.LATENCY_LIMIT_MS or target_quality["packet_loss_pct"] > sq.LOSS_LIMIT_PCT:
                    continue
                candidate_tier = tier if same_site else 2
                candidates.append({
                    "id": "bounded_steering", "title": "Bounded traffic steering",
                    "target": target, "tier": candidate_tier, "fraction": fraction,
                    "source_after": round(row["observed_prb"] - moved, 1),
                    "target_after": round(target_after, 1), "relief": round(moved, 1),
                    "headroom_after": round(100 - target_after, 1),
                    "site_after": round(site_after, 1), "agg_after": float(agg_values[-1]),
                    "same_site": same_site, "overlap": float(neighbor.overlap_score),
                    "disruption": "Limited" if candidate_tier == 1 else "Engineer-reviewed",
                    "reason": "Eligible neighbor; post-move radio and backhaul safety checks passed"})
            candidates.sort(key=lambda c: (c["tier"], -c["headroom_after"], -c["overlap"], c["target"]))
            for rank, candidate in enumerate(candidates, 1):
                candidate["rank"] = rank
            if candidates:
                tier = candidates[0]["tier"]
            if not candidates:
                tier, reason = 3, "No steering candidate passes post-move radio and transport limits"
        if rule == "TDD_UPLINK":
            for fraction, share in ((.1, (quality["tdd_ul_share_pct"] + quality["tdd_ul_demand_pct"]) / 2),
                                    (.2, quality["tdd_ul_demand_pct"])):
                downlink_after = float(self.raw[cell].iloc[index]["dl_prb_pct"]) * (1 + fraction * .75)
                if not (observed_true(quality["tdd_alignment_ok"]) and downlink_after <= 85 and site_values[-1] <= 85 and agg_values[-1] <= 90):
                    continue
                after = round(row["observed_prb"] * (1 - fraction), 1)
                candidates.append({"id": "tdd_ul_profile", "title": "Aligned uplink-heavy TDD profile",
                               "target": None, "tier": 2,
                               "source_after": after, "target_after": None,
                               "relief": round(row["observed_prb"] - after, 1),
                               "headroom_after": round(100 - after, 1),
                               "site_after": float(site_values[-1]), "agg_after": float(agg_values[-1]),
                               "old_ul_share": quality["tdd_ul_share_pct"],
                               "new_ul_share": share, "fraction": fraction,
                               "downlink_after": round(downlink_after, 1), "rank": 1,
                               "disruption": "Engineer-reviewed", "reason": "Neighbor alignment and downlink guard passed"})
            if not candidates:
                tier, reason = 3, "TDD change blocked by neighbor alignment, downlink or transport safety"
        forecast_bins = [{"minutes": h // 60, "probability": round(float(p[index]), 4)}
                         for h, p in self.risk_outlooks[cell].items()
                         if p[index] >= self.model.meta["horizons"][str(h)]["risk_threshold"]]
        if rule is None and forecast_bins:
            rule, tier, reason = "FORECAST_WATCH", 0, "Predicted congestion-rule risk: inspect serving and neighbor cells before any intervention"
        candidates, remedies = self.rank_remedies(cell, index, row, candidates, rule)
        if candidates:
            tier = candidates[0]["tier"]
        elif rule in ("RADIO_PRESSURE", "TDD_UPLINK"):
            tier, reason = 3, "No available profile passes radio, transport and service recovery gates"
        issue = {"cell_id": cell, "rule": rule, "tier": tier, "reason": reason,
                "title": iv.SHORT_NAMES.get(rule, "Healthy"), "forecast_watch": rule == "FORECAST_WATCH",
                "forecast_risk_bins": forecast_bins,
                "observed": row["observed_prb"], "p10": row["observed_p10"],
                "headroom": round(100 - row["observed_prb"], 1), "site_util": float(site_values[-1]),
                "agg_util": float(agg_values[-1]), "candidates": candidates,
                "remedy": candidates[0] if candidates else None, "remedies": remedies,
                "quality": {key: quality[key] for key in ("latency_ms", "packet_loss_pct", "radio_queue_ms",
                            "transport_latency_ms", "transport_packet_loss_pct", "transport_jitter_ms", "tdd_ul_share_pct",
                            "tdd_ul_demand_pct", "tdd_alignment_ok", "interference_flag", "pusch_sinr_db",
                            "ul_bler_pct", "harq_retrans_pct", "rx_interference_dbm", "handover_success_pct",
                            "cell_available", "alarm", "compute_util_pct", "aggregate_ul_mbps",
                            "coverage_impairment_flag", "user_ul_p05_mbps")},
                "recommended_next_step": {
                    "TRANSPORT_CAPACITY": "Validate alternate-path capacity or upgrade backhaul; protect critical queues.",
                    "TRANSPORT_QUALITY": "Inspect transport queues, policers, interface errors and spare-path capacity.",
                    "TDD_UPLINK": "Align neighbor slot patterns and verify downlink headroom before changing the TDD profile.",
                    "RADIO_ERRORS": "Investigate interference, retransmissions and antenna / radio configuration.",
                    "COVERAGE": "Validate serving/neighbor coverage and an approved coverage optimization profile.",
                    "COMPUTE_PRESSURE": "Inspect baseband processing and software alarms; escalate processing capacity or availability faults.",
                    "AVAILABILITY": "Investigate cell, power, synchronization and interface alarms before moving traffic.",
                    "SERVICE_DEGRADATION": "Correlate radio retransmissions, queue drops and path errors before selecting a remedy."
                }.get(rule, reason)}
        issue["hypotheses"] = iv.hypotheses(issue)
        meta = self.state.replay.cells.loc[cell]
        issue["network_object"] = {"level": "cell", "cell_id": cell, "site_id": str(meta["site_id"]),
                                   "sector": str(meta["sector"]), "band": str(meta["band"]), "technology": str(meta["technology"])}
        neighbors = []
        for neighbor in self.state.replay.neighbors[self.state.replay.neighbors["cell_id"] == cell].itertuples():
            target = neighbor.neighbor_cell_id
            if target not in self.quality:
                neighbors.append({"cell_id": target, "eligible": False, "available": None,
                                  "headroom": None, "sinr_db": None, "handover_success_pct": None,
                                  "status": "Live neighbor counters unavailable; steering blocked"})
                continue
            q = self.quality[target].iloc[index]
            neighbors.append({"cell_id": target, "eligible": neighbor.steering_eligibility == "eligible",
                              "available": bool(q["cell_available"]) if isinstance(q["cell_available"], (bool, np.bool_)) else None, "headroom": round(100 - self.state.live["series"][target]["prb"][index], 1),
                              "sinr_db": float(q["pusch_sinr_db"]), "handover_success_pct": float(q["handover_success_pct"])})
        raw_diagnostics = self.raw[cell].iloc[index].copy()
        previous_users = float(self.raw[cell].iloc[max(0, index - 8)]["active_ues"])
        raw_diagnostics["active_users_change_pct"] = round(100 * (float(raw_diagnostics["active_ues"]) - previous_users) / max(previous_users, 1), 1)
        issue["diagnostic_groups"] = assurance.diagnostic_groups(issue, raw_diagnostics, neighbors)
        return issue

    def rank_remedies(self, cell, index, row, candidates, rule):
        options = []
        for candidate in candidates:
            profiles = [copy.deepcopy(candidate)]
            if candidate["id"] == "bounded_steering" and candidate["fraction"] > .1:
                light = copy.deepcopy(candidate)
                fraction, old_fraction = .1, candidate["fraction"]
                target_before = self.state.live["series"][candidate["target"]]["prb"][index]
                light.update(fraction=fraction, title="Limited traffic steering",
                             source_after=round(row["observed_prb"] * (1 - fraction), 1),
                             target_after=round(target_before + row["observed_prb"] * fraction * .8, 1),
                             relief=round(row["observed_prb"] * fraction, 1))
                light["headroom_after"] = round(100 - light["target_after"], 1)
                target_site = self.state.replay.cells.loc[candidate["target"], "site_id"]
                target_link = self.state.replay.site_links[target_site]
                before = self.state.live["backhaul"]["util"][target_link][index]
                light["site_after"] = round(before + (candidate["site_after"] - before) * fraction / old_fraction, 1)
                profiles.append(light)
            for option in profiles:
                share = option.get("new_ul_share")
                projected = self.project_quality(cell, index, option["source_after"], share)
                option["service_after"] = projected
                option["choice_id"] = f"{option['id']}:{option['target'] or cell}:{option['fraction']}"
                option["complexity"] = "Easy" if option["tier"] == 1 else "Intermediate"
                option["parameters"] = iv.parameters(option, cell)
                if option["id"] != "bounded_steering" or option["fraction"] > .18 or not option.get("same_site", True):
                    option["tier"] = 2
                option["complexity"] = "Easy" if option["tier"] == 1 else "Intermediate"
                history = self.outcome_evidence.lookup(option["id"], self.state.replay.cells.loc[cell, "venue_id"],
                                                       row["observed_prb"], self.data["sessions"])
                option["historical_evidence"] = history
                success = .5 if history["success_rate_pct"] is None else history["success_rate_pct"] / 100
                confidence = .7 if history["confidence"] == "Medium" else .4
                simplicity = 1. if option["fraction"] <= .1 else .7
                operational_risk = option["tier"] * (1 + 10 * option["fraction"])
                option["rank_score"] = round(option["relief"] * success * confidence * simplicity / operational_risk, 3)
                option["ranking_factors"] = {"expected_relief": option["relief"], "historical_success_factor": success,
                                             "evidence_confidence": confidence, "simplicity": simplicity, "operational_risk": operational_risk}
                option["intent"] = assurance.intent(option, cell, int(self.state.live["times"][index]))
                option["eligible"] = bool(option["source_after"] < 85 and projected["latency_ms"] <= sq.LATENCY_LIMIT_MS
                                          and projected["packet_loss_pct"] <= sq.LOSS_LIMIT_PCT)
                option["ranking_factors"]["technical_applicability"] = int(option["eligible"])
                option["ranking_factors"]["history_factor_is_prior"] = history["matches"] == 0
                if not option["eligible"]:
                    option["rank_score"] = 0.
                option["executable"] = True
                if not option["eligible"]:
                    option["reason"] = "Predicted source radio or service recovery is insufficient"
                options.append(option)
        if rule == "FORECAST_WATCH":
            options.append({"id": "read_only_rca", "choice_id": "read_only_rca", "title": "Inspect demand, radio quality, neighbors and transport",
                            "tier": 0, "complexity": "Observe", "eligible": True, "executable": False,
                            "reason": "Forecast alone does not establish cause or authorize a change", "parameters": []})
        elif rule:
            options.append({"id": "specialist_review", "choice_id": "specialist_review",
                            "title": "Capacity / RF specialist remediation" if rule != "TDD_UPLINK" else "Neighbor-aligned TDD redesign",
                            "tier": 3, "complexity": "Complex", "eligible": True, "executable": False,
                            "reason": "Requires specialist diagnosis or infrastructure changes; no autonomous actuator",
                            "parameters": []})
        options.extend(assurance.advisory_options(rule))
        options.sort(key=lambda c: (not c["eligible"], c["tier"], -c.get("rank_score", 0), c.get("fraction", 1),
                                    -c.get("headroom_after", 0), c.get("target") or cell))
        for rank, option in enumerate(options, 1):
            option["rank"] = rank
        return [c for c in options if c["eligible"] and c["executable"]], options

    def project_quality(self, cell, index, prb, ul_share=None):
        quality = self.quality[cell].iloc[index]
        gap = quality["tdd_ul_demand_pct"] - (quality["tdd_ul_share_pct"] if ul_share is None else ul_share)
        return {key: float(value) for key, value in sq.estimate(
            prb, quality["transport_latency_ms"], quality["transport_packet_loss_pct"],
            gap, quality["interference_flag"] or quality["coverage_impairment_flag"],
            30. if quality["compute_util_pct"] >= 90 else 0.).items()}

    def validation_projection(self, action, due):
        seconds = int(self.state.live["times"][due])
        source = self.state.live["series"][action["cell_id"]]["prb"][due]
        tdd = action["remedy"]["id"] == "tdd_ul_profile"
        target = self.state.live["series"][action["target"]]["prb"][due] if not tdd else 0
        moved = source * action["remedy"]["fraction"]
        # Counterfactual verification against the recorded no-action stream.
        source_after, target_after = source - moved, target + moved * .8
        site = self.state.replay.cells.loc[action["target"] if not tdd else action["cell_id"], "site_id"]
        link = self.state.replay.site_links[site]
        bh = self.state.live["backhaul"]
        agg = next(key for key in bh["links"] if key.endswith("-AGG"))
        site_util = bh["util"][link][due]
        service_inputs = [self.quality[cell].iloc[due][key]
                          for cell in ([action["cell_id"]] if tdd else [action["cell_id"], action["target"]])
                          for key in ("latency_ms", "packet_loss_pct", "transport_latency_ms",
                                      "transport_packet_loss_pct", "tdd_ul_share_pct", "tdd_ul_demand_pct",
                                      "pusch_sinr_db", "ul_bler_pct", "harq_retrans_pct", "handover_success_pct", "compute_util_pct")]
        if not np.isfinite([source, target, site_util, bh["util"][agg][due], *service_inputs]).all():
            return {"at": seconds, "passed": False, "blocked": True, "result": "insufficient data + escalation",
                                      "source_after": None, "target_after": None, "site_after": None,
                                      "mode": "No recovery claim without valid observations"}
        if not tdd and self.state.replay.cells.loc[action["cell_id"], "site_id"] != site:
            source_site = self.state.replay.cells.loc[action["cell_id"], "site_id"]
            total = sum(self.state.live["series"][c]["prb"][due] for c in self.state.live["cells"]
                        if self.state.replay.cells.loc[c, "site_id"] == source_site)
            moved_gbps = self.state.replay.traffic[source_site][due] * source / max(total, 1e-6) * action["remedy"]["fraction"]
            site_util += 100 * moved_gbps / bh["capacity_gbps"][link]
        after_quality = self.project_quality(action["cell_id"], due, source_after,
                                             action["remedy"]["new_ul_share"] if tdd else None)
        quality_ok = after_quality["latency_ms"] <= sq.LATENCY_LIMIT_MS and after_quality["packet_loss_pct"] <= sq.LOSS_LIMIT_PCT
        if tdd:
            quality_ok = quality_ok and observed_true(self.quality[action["cell_id"]].iloc[due]["tdd_alignment_ok"]) and float(self.raw[action["cell_id"]].iloc[due]["dl_prb_pct"]) * (1 + action["remedy"]["fraction"] * .75) <= 85
        else:
            target_quality = self.project_quality(action["target"], due, target_after)
            quality_ok = quality_ok and target_quality["latency_ms"] <= sq.LATENCY_LIMIT_MS and target_quality["packet_loss_pct"] <= sq.LOSS_LIMIT_PCT
        mobility_ok = observed_true(self.quality[action["cell_id"]].iloc[due]["cell_available"])
        if not tdd:
            target_diagnostics = self.quality[action["target"]].iloc[due]
            mobility_ok = mobility_ok and observed_true(target_diagnostics["cell_available"]) and target_diagnostics["pusch_sinr_db"] >= 5 and target_diagnostics["handover_success_pct"] >= 95
        passed = source_after < 85 and target_after <= 85 and site_util <= 85 and bh["util"][agg][due] <= 90 and quality_ok and mobility_ok
        return {"at": seconds, "passed": bool(passed), "blocked": False, "source_before": source,
                                  "service_before": {key: float(self.quality[action["cell_id"]].iloc[due][key])
                                                     for key in ("latency_ms", "packet_loss_pct")},
                                  "source_after": round(source_after, 1),
                                  "target_after": round(target_after, 1) if not tdd else None, "site_after": round(site_util, 1),
                                  "service_after": after_quality,
                                  "result": "pass" if passed else "rollback + escalation",
                                  "mode": "Counterfactual projection; recorded telemetry unchanged"}

    def validate(self, action, index):
        if action["status"] != "verifying":
            return
        samples = action.setdefault("validation_samples", [])
        start = samples[-1]["at"] + 15 if samples else action["seconds"] + 15
        end = min(int(self.state.live["times"][index]), action["verify_at"])
        for seconds in range(start, end + 1, 15):
            check = self.validation_projection(action, int(np.searchsorted(self.state.live["times"], seconds)))
            samples.append(check)
            if not check["passed"] or seconds == action["verify_at"]:
                action["validation"] = check
                action["status"] = "validation_blocked" if check["blocked"] else "verified" if check["passed"] else "rolled_back"
                break

    def execute(self, session, issue, seconds, automatic):
        remedy = issue["remedy"]
        if not remedy:
            raise ValueError("No safe remedy is available.")
        if seconds + 120 > self.state.live["times"][-1]:
            raise ValueError("Not enough recorded data remains to validate a remedy.")
        involved = {cell for cell in (issue["cell_id"], remedy["target"]) if cell}
        for action in session["actions"]:
            if seconds - action["seconds"] < 600 and involved & {cell for cell in (action["cell_id"], action["target"]) if cell}:
                raise ValueError("Cell reserved by another action or in 10-minute cooldown.")
            if action["cell_id"] == issue["cell_id"] and action["status"] in (
                    "rolled_back", "validation_blocked", "escalated", "rejected"):
                raise ValueError("A failed remedy requires specialist escalation; retries are blocked.")
        action = {"id": uuid.uuid4().hex, "cell_id": issue["cell_id"], "target": remedy["target"],
                  "tier": issue["tier"], "seconds": seconds, "verify_at": seconds + 120,
                  "status": "verifying", "automatic": automatic, "created_at": utcnow(),
                  "remedy": copy.deepcopy(remedy), "parameters": copy.deepcopy(remedy["parameters"]), "intent": copy.deepcopy(remedy["intent"]),
                  "issue_title": issue["title"], "reason": issue["reason"], "hypotheses": copy.deepcopy(issue["hypotheses"]),
                  "model_context": {"outlook_version": self.model.meta["version"], "diagnostic_scenario_version": sq.VERSION,
                                    "vendor_adapter": "simulation_only", "network_software_release": "not_recorded"},
                  "validation_samples": [], "service_before": issue["quality"], "mode": "simulation"}
        case = session.get("cases", {}).get(session.get("active_cases", {}).get(issue["cell_id"]))
        if case is None:
            case = iv.ensure_case(session, issue, seconds)
        iv.attach_action(case, action)
        session["actions"].append(action)
        return action

    def snapshot(self, seconds, cell_id, session_id=None):
        with self.lock:
            result = self._snapshot(seconds, cell_id, session_id)
            if result["session"] and result["seconds"] < result["session"]["seconds"]:
                return self._snapshot(result["session"]["seconds"], cell_id, session_id)
            return result

    def _snapshot(self, seconds, cell_id, session_id=None):
        if isinstance(seconds, bool):
            raise ValueError("Replay position must be numeric, not a boolean.")
        snap = self.state.replay.snapshot(seconds, cell_id)
        session = self.session(session_id) if session_id else None
        index, cell = snap["index"], snap["cell_id"]
        issues = [self.issue(c, index, snap) for c in self.state.live["cells"]]
        x = self.outlooks[cell]
        points = [{"minutes": 0, "prediction": snap["observed_prb"],
                   "low": snap["observed_prb"], "high": snap["observed_prb"]}]
        for horizon, values in sorted(x.items()):
            record = self.model.meta["horizons"][str(horizon)]
            value = float(values[index])
            points.append({"minutes": horizon // 60, "prediction": round(value, 1),
                           "low": round(max(0, value + record["lower_residual"]), 1),
                           "high": round(min(100, value + record["upper_residual"]), 1),
                           "congestion_probability": round(float(self.risk_outlooks[cell][horizon][index]), 4),
                           "risk_threshold": record["risk_threshold"]})
        raw = self.raw[cell].iloc[index]
        if session:
            for issue in issues:
                history = [a for a in session["actions"] if a["cell_id"] == issue["cell_id"]]
                if issue["tier"] and any(a["status"] in (
                        "rolled_back", "validation_blocked", "escalated", "rejected") for a in history):
                    issue.update(tier=3, remedy=None, candidates=[],
                                 reason="Failed validation or human hold. Automatic retries blocked for this run.")
        for issue in issues:
            issue["workflow"] = workflow_status(issue, session, snap["seconds"],
                                                self.state.live["times"][-1])
            issue["review_token"] = review_token(issue, session, snap["seconds"])
        quality = self.quality[cell].iloc[index]
        return {"seconds": snap["seconds"], "index": index, "cell_id": cell,
                "issues": issues, "outlook": points,
                "history": [{"seconds": self.state.live["times"][i],
                             "value": self.state.live["series"][cell]["prb"][i]}
                            for i in range(max(0, index - 80), index + 1, 2)],
                "observed": snap["observed_prb"], "p10": snap["selected"]["observed_p10"],
                "source": "Populated synthetic replay", "cadence_s": 15,
                "unsupported_metrics": [],
                "quality": {"valid": bool(np.isfinite([raw["ul_prb_pct"], raw["ul_tput_p10_mbps"],
                                                       quality["latency_ms"], quality["packet_loss_pct"]]).all()),
                            "packet_loss": float(quality["packet_loss_pct"]), "latency": float(quality["latency_ms"]),
                            "source": "synthetic_service_scenario_v2"},
                "service_history": [{"seconds": self.state.live["times"][i],
                                     "latency_ms": float(self.quality[cell].iloc[i]["latency_ms"]),
                                     "packet_loss_pct": float(self.quality[cell].iloc[i]["packet_loss_pct"])}
                                    for i in range(max(0, index - 80), index + 1, 2)],
                "service_policy": {"latency_limit_ms": sq.LATENCY_LIMIT_MS, "packet_loss_limit_pct": sq.LOSS_LIMIT_PCT},
                "short_risk": self.state.live["series"][cell]["r180"][index],
                "model": self.model.report(), "scorecard": assurance.operational_scorecard(session), "session": copy.deepcopy(session),
                "event_id": self.state.live["event_id"],
                "cells": [{"cell_id": c, "prb": self.state.live["series"][c]["prb"][index],
                           "forecast20": round(float(self.outlooks[c][1200][index]), 1),
                           "forecast30": round(float(self.outlooks[c][1800][index]), 1)}
                          for c in self.state.live["cells"]]}

    def advance(self, body):
        seconds = body.get("seconds")
        with self.lock:
            session = self.session(body.get("session_id"))
            snap = self._snapshot(seconds, body.get("cell_id"), session["id"])
            seconds = snap["seconds"]
            if seconds < session["seconds"]:
                raise ValueError("Replay cannot rewind an active workflow. Start a new session.")
            session["seconds"] = seconds
            for action in session["actions"]:
                previous = action["status"]
                self.validate(action, snap["index"])
                if previous == "verifying" and action["status"] != previous:
                    iv.finish_action(session, action)
            snap = self._snapshot(seconds, body.get("cell_id"), session["id"])
            for issue in snap["issues"]:
                case = iv.ensure_case(session, issue, seconds)
                if issue["tier"] == 1 and issue["remedy"]:
                    try:
                        self.execute(session, issue, seconds, True)
                    except ValueError as error:
                        issue["action_blocked"] = str(error)
                        if case and case.get("last_blocked") != str(error):
                            case["last_blocked"] = str(error)
                            iv.event(case, "safety_hold", seconds, str(error))
            self.save()
            snap["session"] = copy.deepcopy(session)
            for issue in snap["issues"]:
                issue["workflow"] = workflow_status(issue, session, seconds, self.state.live["times"][-1])
                issue["review_token"] = review_token(issue, session, seconds)
            return snap

    def respond(self, body):
        with self.lock:
            session = self.session(body.get("session_id"))
            if body.get("action") not in ("approve", "reject", "escalate"):
                raise ValueError("Select approve, reject or escalate.")
            snap = self.snapshot(session["seconds"], body.get("cell_id"), session["id"])
            issue = next(row for row in snap["issues"] if row["cell_id"] == snap["cell_id"])
            if issue["tier"] == 0:
                raise ValueError("This cell has no active issue.")
            if body["action"] == "approve":
                if issue["tier"] != 2:
                    raise ValueError("Only a current Tier 2 remedy can receive engineer approval.")
                if not issue["workflow"]["can_approve"]:
                    raise ValueError("Cell reserved or in cooldown; review the current investigation.")
                if body.get("review_token") != issue["review_token"]:
                    raise ValueError("Investigation changed. Review the latest ranking before approving.")
                choice = body.get("choice_id")
                selected = None
                if choice:
                    selected = next((c for c in issue["candidates"] if c["choice_id"] == choice and c["tier"] == 2), None)
                    if not selected:
                        raise ValueError("The selected remedy is no longer an eligible engineer option.")
                iv.ensure_case(session, issue, session["seconds"])
                if selected:
                    issue = {**issue, "remedy": selected}
                action = self.execute(session, issue, session["seconds"], False)
            else:
                status = "rejected" if body["action"] == "reject" else "escalated"
                previous = next((a for a in reversed(session["actions"])
                                 if a["cell_id"] == snap["cell_id"]), None)
                if previous and previous["seconds"] == session["seconds"] and previous["status"] == status:
                    return previous
                action = {"id": uuid.uuid4().hex, "cell_id": snap["cell_id"], "target": None,
                          "tier": issue["tier"], "seconds": session["seconds"],
                          "status": status,
                          "issue_title": issue["title"], "reason": issue["reason"],
                          "hypotheses": copy.deepcopy(issue["hypotheses"]), "parameters": [],
                          "created_at": utcnow(), "mode": "simulation"}
                iv.attach_action(iv.ensure_case(session, issue, session["seconds"]), action)
                session["actions"].append(action)
            self.save()
            return action


def pd_times(values):
    """Seconds since epoch, used only to verify observed sample cadence."""
    import pandas as pd
    return pd.to_datetime(values).to_numpy(dtype="datetime64[s]").astype(np.int64)
