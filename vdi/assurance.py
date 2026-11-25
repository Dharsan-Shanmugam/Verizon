"""Vendor-neutral diagnostic evidence, comparable outcomes and policy assurance."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config, data_loader as dl


class OutcomeEvidence:
    def __init__(self):
        outcomes = dl.small("counterfactual").copy()
        events = dl.events()[["event_id", "event_start", "event_category"]]
        self.category = str(events.loc[events["event_id"] == config.LIVE_EVENT_ID, "event_category"].iloc[0])
        outcomes = outcomes.merge(events, on="event_id", how="left")
        self.rows = outcomes[outcomes["event_start"] < pd.Timestamp(config.TODAY)].copy()
        capacity, _ = dl.cell_capacity_at(self.rows["cell_id"], self.rows["event_start"])
        self.rows["dated_capacity_mbps"] = capacity
        self.cache = {}

    def lookup(self, action, venue, prb, sessions):
        if action == "bounded_steering":
            band = round(prb / 5) * 5
            key = (venue, band)
            if key in self.cache:
                return dict(self.cache[key])
            rows = self.rows[(self.rows["recommendation"] == "steer_to_neighbor")
                             & ((self.rows["no_action_peak_ul_prb_pct"] - band).abs() <= 15)]
            same_venue = rows[rows["venue_id"] == venue]
            if len(same_venue) >= 3:
                rows = same_venue
            same_category = rows[rows["event_category"] == self.category]
            category_matched = len(same_category) >= 3
            if category_matched:
                rows = same_category
            successes = ((rows["with_action_peak_ul_prb_pct"] < 85)
                         & (rows["with_action_ul_p10_mbps"] >= rows["no_action_ul_p10_mbps"])
                         & (rows["with_action_drop_rate_pct"] <= rows["no_action_drop_rate_pct"])
                         & (rows["with_action_congestion_min"] <= rows["no_action_congestion_min"]))
            total, passed = len(rows), int(successes.sum())
            source = "File 35 comparable synthetic steering outcomes"
        else:
            actions = [a for session in sessions.values() for a in session["actions"]
                       if a.get("remedy", {}).get("id") == action
                       and a["status"] in ("verified", "rolled_back", "validation_blocked")]
            total, passed = len(actions), sum(a["status"] == "verified" for a in actions)
            source = "Prior local simulation outcomes"
        result = {"matches": total, "successes": passed,
                "success_rate_pct": round(100 * passed / total, 1) if total else None,
                "source": source, "confidence": "Medium" if total >= 3 and passed / total >= .6 else "Low",
                "limitations": "Comparable action family; exact vendor/software/profile and target-side outcome context are incomplete"}
        if action == "bounded_steering":
            result["context"] = {"event_category": self.category, "category_matched": category_matched,
                                 "load_band_prb": band, "load_tolerance_pts": 15,
                                 "dated_capacity_records": int(rows["dated_capacity_mbps"].notna().sum())}
            self.cache[key] = dict(result)
        return result


def advisory_options(rule):
    if not rule:
        return []
    radio = rule in ("RADIO_PRESSURE", "RADIO_ERRORS", "COVERAGE", "TDD_UPLINK")
    options = []
    if radio:
        options += [
            ("activate_commissioned_layer", "Activate a commissioned capacity layer", 2, "No inactive commissioned layer and approved activation adapter recorded"),
            ("scheduler_policy", "Apply approved event scheduler profile", 2, "Scheduler/QoS policy adapter and protected-service commitments not recorded"),
            ("coverage_policy", "Approved beam / cell-shaping optimization", 2, "Requires supported AAS capability and an approved vendor rApp"),
            ("temporary_capacity", "Pre-stage physical event capacity", 3, "Radio, spectrum, transport, power and field integration required")]
    if rule in ("RADIO_ERRORS", "COVERAGE", "TDD_UPLINK"):
        options.append(("interference_policy", "Coordinated interference / power-control review", 2, "Requires engineer-approved coordination scope; no autonomous low-level edits"))
    if rule in ("TRANSPORT_CAPACITY", "TRANSPORT_QUALITY", "SERVICE_DEGRADATION"):
        options += [("prepared_transport_path", "Activate prepared transport path / QoS policy", 2, "No pre-provisioned alternate path or approved transport adapter recorded"),
                    ("physical_transport", "Transport capacity engineering", 3, "Physical bandwidth changes require transport teams")]
    return [{"id": key, "choice_id": key, "title": title, "tier": tier,
             "complexity": "Intermediate" if tier == 2 else "Complex", "eligible": False,
             "executable": False, "parameters": [], "reason": reason}
            for key, title, tier, reason in options]


def diagnostic_groups(issue, raw, neighbors):
    q = issue["quality"]
    incomplete = issue["rule"] == "DATA_QUALITY"
    poor_radio = q["pusch_sinr_db"] < 5 or q["ul_bler_pct"] > 10 or q["harq_retrans_pct"] > 15
    safe_neighbors = [n for n in neighbors if n["eligible"] and n["available"]
                      and n["headroom"] >= 15 and n["sinr_db"] >= 5 and n["handover_success_pct"] >= 95]
    return [
        {"id": "demand", "title": "Demand & radio resources",
         "definition": "High load plus growing active uplink users and queue delay supports demand pressure; PRB alone is not a diagnosis.",
         "assessment": "Insufficient evidence" if incomplete else "Pressure" if issue["observed"] >= 70 and not poor_radio else "Inspect with radio quality",
         "metrics": {"UL PRB %": issue["observed"], "Aggregate UL Mbps": q["aggregate_ul_mbps"],
                     "Connected users": int(raw["rrc_connected"]), "Active users": int(raw["active_ues"]),
                     "Active user change (up to 2 min) %": float(raw["active_users_change_pct"]),
                     "Scheduling queue ms": q["radio_queue_ms"]}},
        {"id": "radio", "title": "Radio quality & interference",
         "definition": "Low PUSCH SINR with BLER and HARQ overhead supports interference or coverage degradation, even at moderate load.",
         "assessment": "Insufficient evidence" if incomplete else "Degraded" if poor_radio else "Stable in current synthetic samples",
         "metrics": {"PUSCH SINR dB": q["pusch_sinr_db"], "UL BLER %": q["ul_bler_pct"],
                     "HARQ retransmission %": q["harq_retrans_pct"], "Receiver interference dBm": q["rx_interference_dbm"]}},
        {"id": "mobility", "title": "Neighbors & mobility",
         "definition": "A target needs spare capacity, usable radio quality, availability and healthy handovers; otherwise steering can move or worsen the problem.",
         "assessment": "Insufficient evidence" if incomplete else f"{len(safe_neighbors)} potential safe neighbors",
         "metrics": {"Configured live neighbors": len(neighbors), "Eligible safe neighbors": len(safe_neighbors),
                     "Serving HO success %": q["handover_success_pct"]},
         "neighbors": neighbors},
        {"id": "transport", "title": "Transport, availability & compute",
         "definition": "Transport queues/loss, equipment availability and compute pressure are separate from radio capacity. Missing fronthaul/midhaul and vendor alarms require integration.",
         "assessment": "Insufficient evidence" if incomplete else "Attention" if issue["site_util"] > 85 or issue["agg_util"] > 90 or q["compute_util_pct"] >= 90 else "Within policy envelope",
         "metrics": {"Site utilization %": issue["site_util"], "Shared utilization %": issue["agg_util"],
                     "Transport RTT ms": q["transport_latency_ms"], "Transport loss %": q["transport_packet_loss_pct"],
                     "Transport jitter proxy ms": q["transport_jitter_ms"],
                     "Compute utilization %": q["compute_util_pct"], "Cell available": q["cell_available"],
                     "Alarm": q["alarm"]}}
    ]


def intent(remedy, cell, seconds):
    steering = remedy["id"] == "bounded_steering"
    return {
        "target": {"level": "cell", "cell_id": cell, "neighbor_cell_id": remedy["target"]},
        "action": "apply_preapproved_temporary_steering_profile" if steering else "review_coordinated_tdd_profile",
        "profile_id": ("EVENT_UL_STEERING_LIMITED" if remedy["fraction"] <= .1 else "EVENT_UL_STEERING_STANDARD")
        if steering else "ENGINEER_TDD_SCENARIO",
        "start_seconds": seconds, "duration_seconds": 600,
        "approval": "automatic_within_policy" if remedy["tier"] == 1 else "engineer_required",
        "scope": [value for value in (cell, remedy["target"]) if value],
        "guardrails": {"radio_headroom_min_pct": 15, "site_after_max_pct": 85, "aggregation_max_pct": 90,
                       "target_sinr_min_db": 5, "handover_success_min_pct": 95,
                       "latency_budget_ms": 50, "packet_loss_budget_pct": 1, "max_affected_cells": 2,
                       "verify_every_seconds": 15, "verify_until_seconds": 120,
                       "rollback_on_degradation": True, "conflicting_actions_blocked": True},
        "adapter": "Simulation adapter only; no vendor CLI/API translation connected",
        "coordination": "Configured eligible neighbor relation" if steering else "Production TDD synchronization scope requires engineering review"
    }


def operational_scorecard(session):
    actions = session["actions"] if session else []
    executed = [a for a in actions if a.get("remedy")]
    validated = [a for a in executed if a["status"] in ("verified", "rolled_back", "validation_blocked")]
    passed = [a for a in validated if a["status"] == "verified"]
    approved = sum(not a.get("automatic", False) for a in executed)
    rejected = sum(a["status"] == "rejected" for a in actions)
    return {"executed": len(executed), "verified": len(passed),
            "success_rate_pct": round(100 * len(passed) / len(validated), 1) if validated else None,
            "rollback_rate_pct": round(100 * sum(a["status"] == "rolled_back" for a in validated) / len(validated), 1) if validated else None,
            "engineer_acceptance_pct": round(100 * approved / (approved + rejected), 1) if approved + rejected else None,
            "mean_validation_seconds": round(float(np.mean([a["validation"]["at"] - a["seconds"] for a in passed])), 1) if passed else None,
            "unavailable": ["Measured user-minutes avoided", "Real post-action P05 gain", "Real network rollback success",
                            "Measured diagnosis/mitigation time", "Per-service protected-QoS-flow validation"],
            "source": "Current-run synthetic counterfactual outcomes; not measured network benefits"}
