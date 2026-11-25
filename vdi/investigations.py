"""Evidence-backed hypotheses and persisted, replay-timestamped case histories."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import uuid
from .pipeline import jsonable


SHORT_NAMES = {"RADIO_PRESSURE": "Radio surge", "TDD_UPLINK": "TDD imbalance",
               "TRANSPORT_CAPACITY": "Backhaul congestion", "TRANSPORT_QUALITY": "Transport impairment",
               "RADIO_ERRORS": "RF interference", "SERVICE_DEGRADATION": "Service degradation",
               "DATA_QUALITY": "Telemetry gap", "COVERAGE": "Coverage impairment",
               "COMPUTE_PRESSURE": "Compute pressure", "AVAILABILITY": "Cell unavailable"}
SHORT_NAMES["FORECAST_WATCH"] = "Forecast watch"


def hypotheses(issue):
    if issue["rule"] == "DATA_QUALITY":
        return [{"cause": "Telemetry quality", "assessment": "Supported", "evidence": issue["reason"]}]
    q = issue["quality"]
    radio_counters = all(key in q for key in ("pusch_sinr_db", "ul_bler_pct", "harq_retrans_pct"))
    poor_quality = radio_counters and (q["pusch_sinr_db"] < 5 or q["ul_bler_pct"] > 10 or q["harq_retrans_pct"] > 15)
    radio = issue["observed"] >= 70 and not poor_quality
    transport = issue["site_util"] > 85 or issue["agg_util"] > 90 or q["transport_latency_ms"] > 30 or q["transport_packet_loss_pct"] > 1
    gap = q["tdd_ul_demand_pct"] - q["tdd_ul_share_pct"]
    return [
        {"cause": "Radio demand surge", "assessment": "Supported" if radio else "Not supported",
         "evidence": f"Radio use {issue['observed']:.1f}%; P10 {issue['p10']:.2f} Mbps"},
        {"cause": "Backhaul queue pressure", "assessment": "Supported" if transport else "Not supported",
         "evidence": f"Site {issue['site_util']:.1f}%; shared {issue['agg_util']:.1f}%; transport RTT {q['transport_latency_ms']:.1f} ms"},
        {"cause": "TDD uplink slot shortage", "assessment": "Supported" if gap >= 10 else "Not supported",
         "evidence": f"Uplink demand {q['tdd_ul_demand_pct']:.0f}% vs allocated {q['tdd_ul_share_pct']:.0f}%"},
        {"cause": "RF interference / coverage", "assessment": "Supported" if poor_quality else "Not supported" if radio_counters else "Needs counters",
         "evidence": f"SINR {q['pusch_sinr_db']:.1f} dB; BLER {q['ul_bler_pct']:.1f}%; HARQ {q['harq_retrans_pct']:.1f}%"
         if radio_counters else "PUSCH SINR, BLER and HARQ counters are unavailable"}]


def parameters(remedy, cell):
    if remedy["id"] == "tdd_ul_profile":
        return [{"parameter": "tdd_uplink_slot_share_pct", "cell": cell,
                 "old": remedy["old_ul_share"], "new": remedy["new_ul_share"], "unit": "%"},
                {"parameter": "simulated_radio_capacity_factor", "cell": cell,
                 "old": 1., "new": round(1 / (1 - remedy["fraction"]), 3), "unit": "x"},
                {"parameter": "simulated_downlink_load_factor", "cell": cell,
                 "old": 1., "new": round(1 + .75 * remedy["fraction"], 3), "unit": "x"}]
    return [{"parameter": "steering_traffic_share_pct", "cell": cell,
             "old": 0, "new": round(remedy["fraction"] * 100, 1), "unit": "%"},
            {"parameter": "steering_target_cell", "cell": cell,
             "old": cell, "new": remedy["target"], "unit": ""}]


def event(case, kind, seconds, summary, **details):
    case["timeline"].append({"kind": kind, "seconds": seconds,
                             "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                             "summary": summary, **details})


def ensure_case(session, issue, seconds):
    cases = session.setdefault("cases", {})
    active = session.setdefault("active_cases", {})
    case = cases.get(active.get(issue["cell_id"]))
    if not issue["tier"] and not issue.get("forecast_watch") and not case:
        return None
    if case and case["title"] == "Forecast watch" and not issue["tier"] and not issue.get("forecast_watch"):
        case["status"] = "watch_cleared"
        case["last_seconds"] = seconds
        event(case, "watch_cleared", seconds, "Forecast watch cleared without a network action")
        active.pop(issue["cell_id"], None)
        return case
    if case and case["status"] == "verified" and issue["tier"] and issue["workflow"]["state"] not in ("verified", "reserved"):
        case = None
    if case is None:
        case_id = uuid.uuid4().hex
        case = {"id": case_id, "cell_id": issue["cell_id"], "title": issue["title"],
                "first_seconds": seconds, "last_seconds": seconds, "status": issue["workflow"]["state"],
                "tier": issue["tier"], "hypotheses": copy.deepcopy(issue["hypotheses"]),
                "detected_evidence": jsonable({"reason": issue["reason"], "radio": issue["observed"],
                                               "quality": issue["quality"]}),
                "action_ids": [], "timeline": []}
        cases[case_id] = case
        active[issue["cell_id"]] = case_id
        event(case, "detected", seconds, issue["title"] + ": " + issue["reason"])
    case["last_seconds"] = seconds
    if case["title"] == "Forecast watch" and issue["tier"]:
        case["title"] = issue["title"]
        event(case, "diagnosed", seconds, issue["title"] + ": " + issue["reason"])
    case["hypotheses"] = copy.deepcopy(issue["hypotheses"])
    signature = (issue["rule"], issue["tier"], issue["remedy"].get("choice_id") if issue["remedy"] else None)
    decision_open = issue["workflow"]["state"] in ("automatic_ready", "engineer_review", "escalation_required", "recording_ending", "forecast_watch")
    if issue["rule"] and decision_open and case.get("ranking_signature") != list(signature):
        case["ranking_signature"] = list(signature)
        case["ranked_remedies"] = jsonable(issue["remedies"])
        event(case, "ranked", seconds, f"Tier {issue['tier']}; {len(issue['candidates'])} safe executable options",
              remedies=jsonable(issue["remedies"]))
    case["tier"] = issue["tier"]
    if issue["tier"] or case["status"] not in ("verified", "escalated", "rejected", "rolled_back", "validation_blocked"):
        case["status"] = issue["workflow"]["state"]
    return case


def attach_action(case, action):
    action["case_id"] = case["id"]
    case["action_ids"].append(action["id"])
    if action.get("remedy"):
        event(case, "agent_authorized" if action["automatic"] else "engineer_approved", action["seconds"],
              f"Rank {action['remedy']['rank']}: {action['remedy']['title']}", action_id=action["id"])
        event(case, "parameters_changed", action["seconds"], "Simulation parameters applied",
              action_id=action["id"], parameters=copy.deepcopy(action["parameters"]))
    else:
        event(case, action["status"], action["seconds"], "Human response recorded", action_id=action["id"])
    case["status"] = action["status"]


def finish_action(session, action):
    case = session.get("cases", {}).get(action.get("case_id"))
    if not case:
        return
    case["status"] = action["status"]
    checked = action["validation"]
    event(case, action["status"], checked["at"], checked["result"], action_id=action["id"],
          validation=copy.deepcopy(checked))
