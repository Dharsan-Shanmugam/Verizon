"""Digital-twin what-if simulator.

Scenario levers: attendance %, home vs away crowd, % of hot-cell traffic
steered to neighbours, temporary UL capacity added (COW / small cells).
Outputs per-cell peak UL PRB, P10 uplink speed, drop rate, congestion
minutes and backhaul utilisation, before vs after mitigation.

Response curves (PRB -> P10 / drops / congestion minutes / accessibility)
are monotone isotonic fits on 12 + 27 hourly KPIs, so the simulator is
monotone by construction.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config
from . import data_loader as dl
from . import features as F
from .models.core import interp, isotonic

HOME_MULT = {"home": 1.0, "away": 0.33, "neutral": 0.85}


def fit_curves(curves: pd.DataFrame, cf: pd.DataFrame | None = None) -> dict:
    """Monotone response curves on a uniform PRB grid.

    Sources: hourly KPIs (12 + 27) and the digital-twin pairs in file 35
    (no-action and with-action rows), which densify the 60-100 % range.
    P10 uplift after relief is learned from file 35 with-action outcomes.
    """
    x = curves["peak_ul_prb_pct"].to_numpy(float)
    cols = {"p10": "ul_tput_p10_mbps", "congestion_min": "congestion_minutes",
            "drop_rate": "session_drop_rate_pct", "accessibility": "accessibility_pct"}
    data = {k: curves[v].to_numpy(float) for k, v in cols.items()}
    out = {}
    x_p10 = x
    if cf is not None and len(cf):
        x_p10 = np.concatenate([x, cf["no_action_peak_ul_prb_pct"].to_numpy(float)])
        data["p10"] = np.concatenate([data["p10"], cf["no_action_ul_p10_mbps"].to_numpy(float)])
        for side in ("no_action", "with_action"):
            x = np.concatenate([x, cf[f"{side}_peak_ul_prb_pct"].to_numpy(float)])
            data["congestion_min"] = np.concatenate([data["congestion_min"], cf[f"{side}_congestion_min"].to_numpy(float)])
            data["drop_rate"] = np.concatenate([data["drop_rate"], cf[f"{side}_drop_rate_pct"].to_numpy(float)])
            data["accessibility"] = np.concatenate([data["accessibility"], cf[f"{side}_accessibility_pct"].to_numpy(float)])
        act = cf[(cf["recommendation"] != "no_routing") & (cf["with_action_peak_ul_prb_pct"] < 95)]
        relief = (act["no_action_peak_ul_prb_pct"] - act["with_action_peak_ul_prb_pct"]).to_numpy(float)
        out["p10_after_relief"] = isotonic(relief, act["with_action_ul_p10_mbps"], increasing=True,
                                           edges=np.arange(0, 42, 2.0))
    edges = np.arange(0, 102.5, 2.5)
    out.update({
        "p10": isotonic(x_p10, data["p10"], increasing=False, edges=edges),
        "congestion_min": isotonic(x, data["congestion_min"], increasing=True, edges=edges),
        "drop_rate": isotonic(x, data["drop_rate"], increasing=True, edges=edges),
        "accessibility": isotonic(x, data["accessibility"], increasing=False, edges=edges),
    })
    return out


def _bh_baseline(venue_id: str, category: str) -> pd.DataFrame:
    b = dl.backhaul()["event_link"]
    ev = dl.events()[["event_id", "event_category", "event_start"]]
    b = b[b["venue_id"] == venue_id].merge(ev, on="event_id")
    recent = b[(b["event_category"] == category) & (b["year"] >= b["year"].max() - 1)]
    if recent.empty:
        recent = b[b["year"] >= b["year"].max() - 1]
    g = recent.groupby(["link_id", "link_level", "site_id"], dropna=False).agg(
        peak_util=("peak_util", "median"), capacity=("capacity_gbps", "last"),
        bg=("background_gbps", "median"), total=("peak_total_gbps", "median")).reset_index()
    g["venue_gbps"] = (g["total"] - g["bg"]).clip(lower=0)
    g["peak_factor"] = (g["peak_util"] / (100 * g["total"] / g["capacity"]).clip(lower=1e-3)).clip(1.0, 1.6)
    return g


class Simulator:
    def __init__(self, models: dict, curves: dict):
        self.att = models["attendance"]
        self.peak = models["peak_uplink"]
        self.curves = curves
        self.cells = F.cell_static().set_index("cell_id")
        self.nb = dl.small("neighbors")

    def _peaks(self, ev_row: pd.DataFrame, attendance: float) -> pd.DataFrame:
        cells = self.cells[self.cells["venue_id"] == ev_row["venue_id"].iloc[0]].index.tolist()
        pairs = pd.DataFrame({"event_id": ev_row["event_id"].iloc[0], "cell_id": cells})
        base = dl.daily()["baseline"]
        return self.peak.predict(pairs, np.full(len(cells), attendance), dl.events(), base)

    def _p10(self, prb: np.ndarray, relief: np.ndarray | None = None) -> np.ndarray:
        p10 = interp(self.curves["p10"], prb)
        if relief is not None and "p10_after_relief" in self.curves:
            lift = interp(self.curves["p10_after_relief"], relief)
            p10 = np.where((relief > 0.5) & (prb < 95), np.maximum(p10, lift), p10)
        return p10

    def _kpis(self, prb: np.ndarray, z_hours: np.ndarray, relief: np.ndarray | None = None) -> dict:
        p10 = self._p10(prb, relief)
        drop = interp(self.curves["drop_rate"], prb)
        acc = interp(self.curves["accessibility"], prb)
        cong = float(interp(self.curves["congestion_min"], z_hours).sum())
        return {"max_prb": round(float(prb.max()), 1), "avg_prb": round(float(prb.mean()), 1),
                "hot_cells": int((prb >= config.thresholds()["ul_prb_pct_gt"]).sum()),
                "p10_min": round(float(p10.min()), 2), "p10_avg": round(float(p10.mean()), 2),
                "drop_rate": round(float(drop.mean()), 3), "accessibility": round(float(acc.mean()), 2),
                "congestion_min": round(cong, 1)}

    def simulate(self, event_id: str | None = None, attendance_pct: float = 100.0,
                 home_away: str | None = None, traffic_moved_pct: float = 0.0,
                 temp_capacity_mbps: float = 0.0) -> dict:
        event_id = event_id or config.LIVE_EVENT_ID
        ev = dl.events()
        row = ev[ev["event_id"] == event_id]
        if row.empty:
            raise KeyError(event_id)
        r0 = row.iloc[0]
        base_att = float(self.att.predict(row)["attendance_pred"].iloc[0])
        sched_ha = r0["anchor_team_home_away"]
        ha = home_away if home_away in HOME_MULT else sched_ha
        ha_factor = HOME_MULT[ha] / HOME_MULT.get(sched_ha, 1.0)
        cap = float(F.cap_ref(row)[0])
        scen_att = float(np.clip(base_att * attendance_pct / 100.0 * ha_factor, 0, cap * 1.1))
        before = self._peaks(row, scen_att).set_index("cell_id")
        baseline = self._peaks(row, base_att).set_index("cell_id")
        cells = before.index.tolist()
        prb_b = before["peak_pred"].to_numpy(float)
        prb_a = prb_b.copy()
        ul_cap, _ = dl.cell_capacity_at(cells, [r0["event_start"]] * len(cells))
        th = config.thresholds()
        hot_thr = th["risk_medium_prb_pct"]
        # 1) temporary capacity: shared across hot cells in proportion to excess load
        if temp_capacity_mbps > 0:
            excess = np.clip(prb_a - (hot_thr - 10), 0, None)
            if excess.sum() <= 0:
                excess = np.zeros_like(prb_a); excess[np.argsort(-prb_a)[:2]] = 1.0
            add = temp_capacity_mbps * excess / excess.sum()
            prb_a = prb_a * ul_cap / (ul_cap + 0.9 * add)
        # 2) steering: move a share of hot-cell load to eligible neighbours with headroom
        moves = []
        if traffic_moved_pct > 0:
            idx = {c: i for i, c in enumerate(cells)}
            sources = {cells[i] for i in range(len(cells)) if prb_a[i] >= hot_thr}
            for i in np.argsort(-prb_a):
                c = cells[i]
                if c not in sources:
                    continue
                want = prb_a[i] * traffic_moved_pct / 100.0
                nbs = self.nb[(self.nb["cell_id"] == c) & (self.nb["steering_eligibility"] == "eligible")]
                nbs = nbs[nbs["neighbor_cell_id"].isin(idx) & ~nbs["neighbor_cell_id"].isin(sources)] \
                    .sort_values("overlap_score", ascending=False)
                for n in nbs.itertuples():
                    j = idx[n.neighbor_cell_id]
                    ratio = ul_cap[i] / ul_cap[j]
                    room = max(0.0, (hot_thr + 5) - prb_a[j]) / ratio
                    take = min(want, room)
                    if take <= 0.05:
                        continue
                    prb_a[i] -= take; prb_a[j] += take * ratio; want -= take
                    moves.append({"from": c, "to": n.neighbor_cell_id, "prb_pts": round(take, 1)})
                    if want <= 0.05:
                        break
        prof = self.peak.profile.get(r0["event_category"]) or next(iter(self.peak.profile.values()))
        hours = sorted(int(h) for h in prof)
        shape = np.array([prof[str(h)] for h in hours])
        hz_b = np.outer(prb_b, shape); hz_a = np.outer(prb_a, shape)
        relief = np.clip(prb_b - prb_a, 0, None)
        kb = self._kpis(prb_b, hz_b); ka = self._kpis(prb_a, hz_a, relief)
        # backhaul
        bh = _bh_baseline(r0["venue_id"], r0["event_category"])
        site_load_b = pd.Series(prb_b, index=cells).groupby(self.cells.loc[cells, "site_id"]).sum()
        site_load_0 = pd.Series(baseline["peak_pred"].to_numpy(float), index=cells).groupby(
            self.cells.loc[cells, "site_id"]).sum()
        site_load_a = pd.Series(prb_a, index=cells).groupby(self.cells.loc[cells, "site_id"]).sum()
        links = []
        agg_b = agg_a = 0.0
        for lk in bh.itertuples():
            if lk.link_level == "site":
                s = lk.site_id
                f_b = site_load_b.get(s, 0) / max(site_load_0.get(s, 1), 1e-6)
                f_a = site_load_a.get(s, 0) / max(site_load_0.get(s, 1), 1e-6)
                vb = lk.venue_gbps * f_b
                va = lk.venue_gbps * f_a  # steered load already shifted between sites via PRB
                ub = 100 * (lk.bg + vb) / lk.capacity * lk.peak_factor
                ua = 100 * (lk.bg + va) / lk.capacity * lk.peak_factor
                links.append({"link_id": lk.link_id, "level": "site", "site_id": s, "capacity_gbps": float(lk.capacity),
                              "before": round(min(ub, 100), 1), "after": round(min(ua, 100), 1)})
            else:
                tot0 = max(site_load_0.sum(), 1e-6)
                ub = 100 * (lk.bg + lk.venue_gbps * site_load_b.sum() / tot0) / lk.capacity * lk.peak_factor
                ua = 100 * (lk.bg + lk.venue_gbps * site_load_a.sum() / tot0) / lk.capacity * lk.peak_factor
                agg_b, agg_a = min(ub, 100), min(ua, 100)
                links.append({"link_id": lk.link_id, "level": "aggregation", "site_id": None,
                              "capacity_gbps": float(lk.capacity), "before": round(agg_b, 1), "after": round(agg_a, 1)})
        site_links = [l for l in links if l["level"] == "site"]
        kb["site_bh_max"] = max((l["before"] for l in site_links), default=0.0)
        ka["site_bh_max"] = max((l["after"] for l in site_links), default=0.0)
        kb["agg_bh"] = round(agg_b, 1); ka["agg_bh"] = round(agg_a, 1)
        p10b = self._p10(prb_b); p10a = self._p10(prb_a, relief)
        return {
            "event": {"event_id": event_id, "event_name": r0["event_name"], "venue_id": r0["venue_id"],
                      "venue_name": r0["venue_name"], "date": r0["event_start"].strftime("%Y-%m-%d %H:%M"),
                      "scheduled_home_away": sched_ha, "category": r0["event_category"]},
            "inputs": {"attendance_pct": attendance_pct, "home_away": ha, "traffic_moved_pct": traffic_moved_pct,
                       "temp_capacity_mbps": temp_capacity_mbps},
            "attendance": {"forecast": round(base_att), "scenario": round(scen_att), "capacity": round(cap)},
            "kpis": {"before": kb, "after": ka},
            "cells": [{"cell_id": c, "site_id": self.cells.loc[c, "site_id"], "band": self.cells.loc[c, "band"],
                       "before": round(float(prb_b[i]), 1), "after": round(float(prb_a[i]), 1),
                       "p10_before": round(float(p10b[i]), 2), "p10_after": round(float(p10a[i]), 2),
                       "ul_capacity_mbps": round(float(ul_cap[i]), 1)} for i, c in enumerate(cells)],
            "hourly": [{"hour_rel": h, "before": round(float(hz_b[:, k].max()), 1),
                        "after": round(float(hz_a[:, k].max()), 1)} for k, h in enumerate(hours)],
            "backhaul": links, "moves": moves,
            "method": (f"Peak model re-scored at the scenario attendance; temporary capacity shared across cells above "
                       f"{hot_thr - 10:.0f}% PRB; steering moves load from cells ≥{hot_thr:.0f}% to eligible neighbours "
                       f"up to {hot_thr + 5:.0f}% PRB; P10, drops and congestion minutes from monotone response curves "
                       f"(hourly KPIs + file 35 digital-twin replays)."),
        }
