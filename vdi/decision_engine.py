"""Backhaul-aware decision engine with an explainable trace.

threshold check -> neighbour selection -> radio check -> backhaul check
-> approval tier -> action spec -> (later) verify -> rollback / fallback.

Tiers (mirrors 26_steering_safety_checks): tier 0 monitor only,
tier 1 automatic (medium risk, steer safe), tier 2 engineer approves
(high risk, steer safe), tier 3 fallback (no safe neighbour).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from . import config

TIER0, TIER1, TIER2, TIER3 = ("tier 0 - monitor only", "tier 1 - automatic",
                              "tier 2 - engineer approves", "tier 3 - fallback")


@dataclass
class CellState:
    cell_id: str
    site_id: str
    ul_prb: float
    ul_p10: float
    risk_60: float = 0.0
    risk_180: float = 0.0
    rule_run: int = 0            # consecutive 15-s intervals meeting the rule
    traffic_gbps: float = 0.1    # source cell traffic carried on its site backhaul


@dataclass
class Neighbor:
    cell_id: str
    site_id: str
    overlap: float
    eligibility: str
    ul_prb: float                # current (or forecast) UL PRB on the neighbour

    @property
    def headroom(self) -> float:
        return round(100.0 - self.ul_prb, 1)


@dataclass
class Backhaul:
    site_util: dict              # site_id -> utilisation % (peak)
    site_capacity_gbps: dict     # site_id -> capacity
    agg_util: float


@dataclass
class Decision:
    cell_id: str
    risk_level: str
    recommendation: str
    tier: str
    target_cell_id: str | None
    trace: list = field(default_factory=list)
    actions: list = field(default_factory=list)
    projected: dict = field(default_factory=dict)
    checks: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


def risk_level(s: CellState, th=None) -> str:
    th = th or config.thresholds()
    a = th["alert_probability"]
    if s.rule_run >= th["consecutive_15s_intervals"] or s.risk_60 >= a or s.ul_prb >= th["risk_high_prb_pct"]:
        return "high"
    if s.risk_180 >= a or s.ul_prb >= th["risk_medium_prb_pct"]:
        return "medium"
    return "low"


def steer_actions(s: CellState, target: str, st=None) -> list[dict]:
    st = st or config.steering()
    cio = st["cell_individual_offset_db"] - (2 if s.ul_prb >= 95 else 0)
    return [
        {"parameter": "cell_individual_offset_db", "cell": s.cell_id, "target": target,
         "old": 0, "new": cio, "unit": "dB"},
        {"parameter": "a3_offset_db", "cell": s.cell_id, "target": target,
         "old": st["a3_offset_db"][0], "new": st["a3_offset_db"][1], "unit": "dB"},
        {"parameter": "ul_load_balance_threshold_pct", "cell": s.cell_id, "target": target,
         "old": st["ul_load_balance_threshold_pct"][0], "new": st["ul_load_balance_threshold_pct"][1],
         "unit": "%"},
    ]


def fallback_actions(s: CellState) -> list[dict]:
    return [
        {"parameter": "ul_rate_shaping_non_critical_kbps", "cell": s.cell_id, "old": 0, "new": 1,
         "unit": "flag", "note": "shape non-critical uplink (cloud sync, bulk upload)"},
        {"parameter": "priority_boost_first_responders", "cell": s.cell_id, "old": 0, "new": 1,
         "unit": "flag", "note": "protect first-responder / priority users"},
        {"parameter": "activate_standby_carrier", "cell": s.cell_id, "old": 0, "new": 1,
         "unit": "flag", "note": "bring standby carrier on air if licensed"},
    ]


def decide(s: CellState, neighbors: list[Neighbor], bh: Backhaul, th=None, st=None) -> Decision:
    th = th or config.thresholds()
    st = st or config.steering()
    trace = []
    lvl = risk_level(s, th)
    trace.append({"step": "Threshold check", "status": "fail" if lvl != "low" else "pass",
                  "detail": f"UL PRB {s.ul_prb:.1f}% · P10 {s.ul_p10:.2f} Mbps · rule run {s.rule_run}/"
                            f"{th['consecutive_15s_intervals']} · risk 60s {s.risk_60:.0%} / 180s {s.risk_180:.0%}"
                            f" → {lvl} risk"})
    if lvl == "low":
        trace.append({"step": "Approval tier", "status": "info", "detail": "No action - keep monitoring"})
        return Decision(s.cell_id, lvl, "no_routing", TIER0, None, trace)

    move = st["move_fraction"]
    traffic = round(s.traffic_gbps * move, 3)
    cands = sorted(neighbors, key=lambda n: (n.eligibility != "eligible", -(n.overlap * n.headroom)))
    trace.append({"step": "Neighbour selection", "status": "info",
                  "detail": ", ".join(f"{n.cell_id} (overlap {n.overlap:.2f}, headroom {n.headroom:.0f}%, {n.eligibility})"
                                      for n in cands) or "no neighbours configured"})
    chosen = None
    checks = []
    for n in cands:
        c = {"target": n.cell_id, "target_site": n.site_id, "radio_headroom_pct": n.headroom,
             "traffic_to_move_gbps": traffic}
        if n.eligibility != "eligible":
            c.update(radio_ok=False, backhaul_ok=None, reason="steering restricted on this relation")
            checks.append(c)
            continue
        c["radio_ok"] = n.headroom >= th["radio_headroom_min_pct"]
        before = float(bh.site_util.get(n.site_id, 0.0))
        cap = float(bh.site_capacity_gbps.get(n.site_id, 1.0)) or 1.0
        same = n.site_id == s.site_id
        after = before if same else before + 100.0 * traffic / cap
        c.update(same_site_backhaul=same, site_util_before_pct=round(before, 1),
                 site_util_after_pct=round(after, 1), aggregation_util_pct=round(bh.agg_util, 1))
        site_ok = after <= th["site_backhaul_max_after_move_pct"]
        agg_ok = bh.agg_util <= th["aggregation_max_pct"]
        c["backhaul_ok"] = bool(site_ok and agg_ok)
        if not c["radio_ok"]:
            c["reason"] = f"neighbour radio headroom {n.headroom:.0f}% < {th['radio_headroom_min_pct']}%"
        elif not agg_ok:
            c["reason"] = f"venue aggregation link {bh.agg_util:.0f}% > {th['aggregation_max_pct']}%"
        elif not site_ok:
            c["reason"] = f"target site backhaul would reach {after:.0f}% > {th['site_backhaul_max_after_move_pct']}%"
        else:
            c["reason"] = "radio and backhaul headroom confirmed"
        checks.append(c)
        if c["radio_ok"] and c["backhaul_ok"]:
            chosen = (n, c)
            break
    for c in checks:
        trace.append({"step": f"Radio check → {c['target']}", "status": "pass" if c.get("radio_ok") else "fail",
                      "detail": f"headroom {c['radio_headroom_pct']:.0f}% (min {th['radio_headroom_min_pct']}%)"
                      if c.get("reason") != "steering restricted on this relation" else c["reason"]})
        if c.get("backhaul_ok") is not None and c.get("radio_ok"):
            trace.append({"step": f"Backhaul check → {c['target_site']}",
                          "status": "pass" if c["backhaul_ok"] else "fail",
                          "detail": f"site {c['site_util_before_pct']:.0f}% → {c['site_util_after_pct']:.0f}% "
                                    f"(≤{th['site_backhaul_max_after_move_pct']}%), aggregation "
                                    f"{c['aggregation_util_pct']:.0f}% (≤{th['aggregation_max_pct']}%) · {c['reason']}"})
    if chosen is None:
        d = Decision(s.cell_id, lvl, "fallback", TIER3, None, trace, fallback_actions(s),
                     checks={"candidates": checks})
        d.projected = {"source_prb_after": round(s.ul_prb * 0.93, 1), "note": "fallback limits non-critical uplink"}
        trace.append({"step": "Approval tier", "status": "warn",
                      "detail": f"{TIER3}: no safe neighbour - NOC applies fallback and escalates"})
        return d
    n, c = chosen
    tier = TIER2 if lvl == "high" else TIER1
    moved_pts = s.ul_prb * move
    d = Decision(s.cell_id, lvl, "steer_to_neighbor", tier, n.cell_id, trace, steer_actions(s, n.cell_id, st),
                 checks={"chosen": c, "candidates": checks})
    d.projected = {"source_prb_after": round(s.ul_prb - moved_pts, 1),
                   "target_prb_after": round(n.ul_prb + moved_pts * 0.8, 1),
                   "moved_fraction": move, "auto_revert_min": st["auto_revert_minutes"]}
    trace.append({"step": "Approval tier", "status": "pass",
                  "detail": f"{tier}: " + ("engineer approves before execution" if tier == TIER2
                                           else "executes automatically (pre-emptive, medium risk)")})
    trace.append({"step": "Action spec", "status": "info",
                  "detail": "; ".join(f"{a['parameter']} {a['old']}→{a['new']} {a['unit']}" for a in d.actions)
                            + f" · auto-revert {st['auto_revert_minutes']} min"})
    return d


def verify(d: Decision, source_prb_obs: float, target_prb_obs: float, th=None) -> dict:
    """Verify a steer after the verify window using observed loads.

    `source_prb_obs` / `target_prb_obs` are the loads observed without action;
    the steering effect (move fraction) is applied on top (digital-twin view).
    """
    th = th or config.thresholds()
    if d.recommendation != "steer_to_neighbor":
        return {"result": "n/a", "state": "fallback_active" if d.recommendation == "fallback" else "monitor"}
    move = d.projected.get("moved_fraction", config.steering()["move_fraction"])
    src = source_prb_obs * (1 - move)
    tgt = target_prb_obs + source_prb_obs * move * 0.8
    ok = src < th["ul_prb_pct_gt"] and tgt < th["ul_prb_pct_gt"]
    res = {"source_prb_after": round(src, 1), "target_prb_after": round(tgt, 1),
           "result": "pass" if ok else "rollback",
           "state": "verified" if ok else "rolled_back_fallback"}
    if not ok:
        res["reason"] = ("source still above threshold" if src >= th["ul_prb_pct_gt"]
                         else "target neighbour overloaded")
        res["fallback_actions"] = [a["parameter"] for a in fallback_actions(
            CellState(d.cell_id, "", source_prb_obs, 0))]
    return res
