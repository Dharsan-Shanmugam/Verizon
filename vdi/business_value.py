"""Business value roll-up (all derived from the synthetic CSVs)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import data_loader as dl


def _r(x, n=1):
    return None if x is None or not np.isfinite(x) else round(float(x), n)


def counterfactual() -> dict:
    c = dl.small("counterfactual")
    act = c[c["recommendation"] != "no_routing"]
    steer = c[c["recommendation"] == "steer_to_neighbor"]
    years = pd.to_datetime(c["sim_hour"]).dt.year
    by_year = c.assign(year=years, avoided=c["no_action_congestion_min"] - c["with_action_congestion_min"]) \
        .groupby("year").agg(avoided=("avoided", "sum"), sessions=("sessions_protected_est", "sum")).reset_index()
    p10_no = act["no_action_ul_p10_mbps"].mean(); p10_with = act["with_action_ul_p10_mbps"].mean()
    return {
        "decisions": int(len(c)), "actioned": int(len(act)), "steered": int(len(steer)),
        "p10_no_action_mbps": _r(p10_no, 2), "p10_with_action_mbps": _r(p10_with, 2),
        "p10_uplift_mbps": _r(p10_with - p10_no, 2),
        "p10_uplift_x": _r(p10_with / max(p10_no, 0.01), 1),
        "drop_no_action_pct": _r(act["no_action_drop_rate_pct"].mean(), 2),
        "drop_with_action_pct": _r(act["with_action_drop_rate_pct"].mean(), 2),
        "drop_reduction_pct": _r(100 * (1 - act["with_action_drop_rate_pct"].mean()
                                        / act["no_action_drop_rate_pct"].mean()), 1),
        "access_no_action_pct": _r(act["no_action_accessibility_pct"].mean(), 2),
        "access_with_action_pct": _r(act["with_action_accessibility_pct"].mean(), 2),
        "congestion_min_no_action": _r(c["no_action_congestion_min"].sum(), 0),
        "congestion_min_with_action": _r(c["with_action_congestion_min"].sum(), 0),
        "congestion_min_avoided": _r((c["no_action_congestion_min"] - c["with_action_congestion_min"]).sum(), 0),
        "congestion_avoided_pct": _r(100 * (1 - c["with_action_congestion_min"].sum()
                                            / max(c["no_action_congestion_min"].sum(), 1)), 1),
        "sessions_protected": int(c["sessions_protected_est"].sum()),
        "events_covered": int(c["event_id"].nunique()),
        "by_year": [{"year": int(r.year), "avoided": _r(r.avoided, 0), "sessions": int(r.sessions)}
                    for r in by_year.itertuples()],
        "period": f"{years.min()}–{years.max()}",
    }


def temp_capacity() -> dict:
    t = dl.small("temp_capacity").copy()
    t["year"] = pd.to_datetime(t["event_start"]).dt.year
    dep = t[t["deployed_by_current_rule"]]
    poc = t[t["poc_forecast_available"]]
    by_year = t.groupby("year").agg(cost=("deployment_cost_usd", "sum"),
                                    avoidable=("poc_avoidable_cost_usd", "sum")).reset_index()
    outcomes = t["historical_outcome"].value_counts().to_dict()
    missed = int(((t["needed_per_actuals"]) & (~t["deployed_by_current_rule"])).sum())
    poc_catch = int((poc["needed_per_actuals"] & (poc["poc_would_deploy"] == 1)).sum())
    poc_need = int(poc["needed_per_actuals"].sum())
    return {
        "deployments": int(len(dep)), "deployment_cost_usd": _r(dep["deployment_cost_usd"].sum(), 0),
        "unneeded_deployments": int((dep["needed_per_actuals"] == False).sum()),  # noqa: E712
        "avoidable_cost_usd": _r(t["poc_avoidable_cost_usd"].sum(), 0),
        "avoidable_cost_per_year_usd": _r(t["poc_avoidable_cost_usd"].sum()
                                          / max(poc["year"].nunique(), 1), 0),
        "crew_hours": _r(dep["setup_crew_hours"].sum(), 0),
        "missed_needs_current_rule": missed,
        "poc_recall_on_needed": _r(poc_catch / max(poc_need, 1) * 100, 1),
        "poc_years": f"{poc['year'].min()}–{poc['year'].max()}" if len(poc) else None,
        "outcomes": {k: int(v) for k, v in outcomes.items()},
        "by_year": [{"year": int(r.year), "cost": _r(r.cost, 0), "avoidable": _r(r.avoidable, 0)}
                    for r in by_year.itertuples()],
    }


def engineer_effort() -> dict:
    e = dl.small("effort").copy()
    e["year"] = e["decision_id"].map(dl.small("decisions").set_index("decision_id")["decision_time"].str[:4]).astype(int)
    by_tier = e.groupby("approval_tier").agg(n=("decision_id", "size"), manual=("manual_total_min", "mean"),
                                             poc=("poc_engineer_review_min", "mean")).reset_index()
    return {
        "decisions": int(len(e)),
        "hours_saved": _r(e["engineer_minutes_saved"].sum() / 60, 0),
        "hours_saved_per_year": _r(e["engineer_minutes_saved"].sum() / 60 / max(e["year"].nunique(), 1), 0),
        "manual_hours": _r(e["manual_total_min"].sum() / 60, 0),
        "poc_hours": _r(e["poc_engineer_review_min"].sum() / 60, 0),
        "manual_min_per_decision": _r(e["manual_total_min"].mean(), 1),
        "poc_min_per_decision": _r(e["poc_engineer_review_min"].mean(), 1),
        "manual_detection_lag_s": _r(e["manual_detection_lag_s"].median(), 0),
        "poc_detection_lead_s": _r(e["poc_detection_lead_s"].median(), 0),
        "by_tier": [{"tier": r.approval_tier, "n": int(r.n), "manual_min": _r(r.manual, 1), "poc_min": _r(r.poc, 1)}
                    for r in by_tier.itertuples()],
    }


def gatherings() -> dict:
    g = dl.small("gatherings").copy()
    by_type = g.groupby("gathering_type").agg(
        n=("gathering_id", "size"), social_share=("social_spike_detected", "mean"),
        social_lead_min=("social_lead_minutes", "median"), network_lead_s=("network_model_lead_s", "median"),
        congestion_share=("congestion_occurred", "mean"), peak_crowd=("peak_crowd_est", "median"),
        surge=("ul_surge_vs_baseline_pct", "median")).reset_index().sort_values("n", ascending=False)
    return {
        "count": int(len(g)), "venues": int(g["venue_id"].nunique()),
        "social_detected_pct": _r(100 * g["social_spike_detected"].mean(), 1),
        "social_lead_min_median": _r(g["social_lead_minutes"].median(), 0),
        "network_lead_s_median": _r(g["network_model_lead_s"].median(), 0),
        "congestion_pct": _r(100 * g["congestion_occurred"].mean(), 1),
        "actions": {k: int(v) for k, v in g["poc_action"].value_counts().items()},
        "by_type": [{"type": r.gathering_type, "n": int(r.n), "social_pct": _r(100 * r.social_share, 0),
                     "social_lead_min": _r(r.social_lead_min, 0), "network_lead_s": _r(r.network_lead_s, 0),
                     "congestion_pct": _r(100 * r.congestion_share, 0), "peak_crowd": int(r.peak_crowd),
                     "surge_pct": _r(r.surge, 0)} for r in by_type.itertuples()],
    }


def summary() -> dict:
    return {"counterfactual": counterfactual(), "temp_capacity": temp_capacity(),
            "engineer": engineer_effort(), "gatherings": gatherings()}
