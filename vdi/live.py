"""Live command center: replay of the in-progress event with model risk,
backhaul state and a decision timeline produced by the decision engine."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config
from . import data_loader as dl
from . import decision_engine as de
from . import features as F


def forecast_curves(times, observed, hours, profile) -> dict:
    """Historical event shape plus a causal, trailing-2-minute residual correction."""
    times = np.asarray(times, float)
    observed = np.asarray(observed, float)
    historical = np.interp(times, np.asarray(hours, float) * 3600, profile)
    residual = pd.Series(observed - historical).rolling(8, min_periods=1).mean().to_numpy()
    ahead = np.clip(np.interp(times + 60, np.asarray(hours, float) * 3600, profile) + residual, 0, 100)
    issued = np.searchsorted(times, times - 60, side="right") - 1
    aligned = [None if j < 0 or times[i] - times[j] != 60 else round(float(ahead[j]), 2)
               for i, j in enumerate(issued)]
    return {"history_fc": np.round(historical, 2).tolist(),
            "adaptive60": np.round(ahead, 2).tolist(), "adaptive60_aligned": aligned}


def forecast_error(observed, predicted) -> dict:
    actual = np.asarray(observed, float)
    forecast = np.asarray(predicted, float)
    valid = np.isfinite(actual) & np.isfinite(forecast)
    errors = actual[valid] - forecast[valid]
    return {"n": int(valid.sum()),
            "mae": round(float(np.abs(errors).mean()), 2) if len(errors) else None,
            "bias": round(float(errors.mean()), 2) if len(errors) else None,
            "current": round(float(actual[-1] - forecast[-1]), 2)
            if len(actual) and valid[-1] else None}


class LiveReplay:
    """Cursor-bound observations and recommendations, never real network execution."""

    def __init__(self, live: dict, event: dict):
        self.live = live
        self.times = np.asarray(live["times"])
        self.cells = F.cell_static().set_index("cell_id")
        self.neighbors = dl.small("neighbors")
        for row in event["heatmap"]["rows"]:
            cell = row["cell_id"]
            live["series"][cell].update(forecast_curves(
                self.times, live["series"][cell]["prb"], event["heatmap"]["hour_rel"], row["values"]))
        bh = dl.backhaul()["live"].copy()
        bh["sfs"] = (pd.to_datetime(bh["timestamp"]) - pd.Timestamp(live["event_start"])).dt.total_seconds()
        self.traffic = {}
        self.site_links = {}
        for link in dl.small("bh_links").itertuples():
            if link.link_level != "site" or link.link_id not in live["backhaul"]["links"]:
                continue
            self.site_links[link.site_id] = link.link_id
            rows = bh[bh["link_id"] == link.link_id].set_index("sfs")
            values = (rows["venue_ul_gbps"] + rows["venue_dl_gbps"]).reindex(self.times).ffill()
            if values.isna().any():
                raise ValueError(f"Missing live backhaul traffic for {link.link_id}.")
            self.traffic[link.site_id] = values.to_numpy(float)
        live["source"] = {"mode": "synthetic_replay", "interval_s": 15,
                          "forecast_method": "Historical event profile + trailing 2-minute residual correction",
                          "execution": "Simulation only; no real telemetry feed or RAN controller connected"}

    def snapshot(self, seconds, cell_id=None) -> dict:
        try:
            seconds = float(seconds)
        except (ValueError, TypeError):
            raise ValueError("Replay position must be a finite number.") from None
        if not np.isfinite(seconds) or not self.times[0] <= seconds <= self.times[-1]:
            raise ValueError("Replay position is outside the recorded stream.")
        if cell_id is not None and cell_id not in self.live["cells"]:
            raise ValueError("Select a cell in this replay.")
        index = int(np.searchsorted(self.times, seconds, side="right") - 1)
        cell_id = cell_id or self.live["cells"][0]
        th, st = config.thresholds(), config.steering()
        bh_data = self.live["backhaul"]
        agg = next(link for link in bh_data["links"] if link.endswith("-AGG"))
        bh = de.Backhaul(
            {site: bh_data["util"][link][index] for site, link in self.site_links.items()},
            {site: bh_data["capacity_gbps"][link] for site, link in self.site_links.items()},
            bh_data["util"][agg][index])
        decisions = []
        for cell in self.live["cells"]:
            series = self.live["series"][cell]
            site = self.cells.loc[cell, "site_id"]
            run = 0
            for j in range(index, -1, -1):
                if series["prb"][j] <= th["ul_prb_pct_gt"] or series["p10"][j] >= th["ul_p10_mbps_lt"]:
                    break
                run += 1
            site_load = sum(self.live["series"][c]["prb"][index] for c in self.live["cells"]
                            if self.cells.loc[c, "site_id"] == site)
            traffic = self.traffic[site][index] * series["prb"][index] / max(site_load, 1e-6)
            state = de.CellState(cell, site, series["prb"][index], series["p10"][index],
                                 series["r60"][index], series["r180"][index], run, float(traffic))
            neighbors = [
                de.Neighbor(row.neighbor_cell_id, self.cells.loc[row.neighbor_cell_id, "site_id"],
                            float(row.overlap_score), row.steering_eligibility,
                            self.live["series"][row.neighbor_cell_id]["prb"][index])
                for row in self.neighbors[self.neighbors["cell_id"] == cell].itertuples()
                if row.neighbor_cell_id in self.live["series"]]
            decision = de.decide(state, neighbors, bh, th, st).to_dict()
            decision["observed_prb"] = series["prb"][index]
            decision["observed_p10"] = series["p10"][index]
            decision["routing_required"] = decision["recommendation"] != "no_routing"
            decision["automatic_eligible"] = decision["tier"] == de.TIER1
            decision["human_approval_required"] = decision["tier"] == de.TIER2
            projection = decision["projected"]
            decision["projected_relief_sufficient"] = (
                projection.get("source_prb_after", 100) < th["ul_prb_pct_gt"]
                and projection.get("target_prb_after", 100) < th["ul_prb_pct_gt"]
            ) if decision["recommendation"] == "steer_to_neighbor" else None
            decisions.append(decision)
        series = self.live["series"][cell_id]
        selected = next(row for row in decisions if row["cell_id"] == cell_id)
        return {
            "seconds": int(self.times[index]), "index": index, "cell_id": cell_id,
            "source": self.live["source"], "observed_prb": series["prb"][index],
            "historical_forecast": series["history_fc"][index],
            "forecast_next_60s": series["adaptive60"][index],
            "forecast_target_s": int(self.times[index]) + 60,
            "errors": {
                name: forecast_error(series["prb"][:index + 1], series[key][:index + 1])
                for name, key in (("historical", "history_fc"), ("feed", "fc"),
                                  ("adaptive60", "adaptive60_aligned"))
            },
            "rolling_5min": forecast_error(series["prb"][max(0, index - 19):index + 1],
                                          series["adaptive60_aligned"][max(0, index - 19):index + 1]),
            "routing": decisions, "selected": selected,
            "comparison": {"kind": "hypothetical single-decision projection, not executed",
                           "source_before": selected["observed_prb"],
                           "source_after": selected["projected"].get("source_prb_after", selected["observed_prb"]),
                           "target_cell_id": selected["target_cell_id"],
                           "target_before": self.live["series"][selected["target_cell_id"]]["prb"][index]
                           if selected["target_cell_id"] else None,
                           "target_after": selected["projected"].get("target_prb_after")},
        }


def build_live(models: dict, peak_scores: pd.DataFrame | None = None) -> dict:
    sh = models["short_horizon"]
    th = config.thresholds(); st = config.steering()
    s = dl.live_stream().sort_values(["event_id", "cell_id", "seconds_from_start"]).reset_index(drop=True)
    P = sh.predict_proba(s)
    for h in (60, 120, 180):
        s[f"risk{h}"] = P[h]
    times = np.sort(s["seconds_from_start"].unique())
    tindex = {t: i for i, t in enumerate(times)}
    cells = sorted(s["cell_id"].unique())
    cs = F.cell_static().set_index("cell_id")
    ev_start = pd.Timestamp(dl.events().set_index("event_id").loc[config.LIVE_EVENT_ID, "event_start"])

    def arr(col, cell, nd=2):
        v = s.loc[s["cell_id"] == cell].set_index("seconds_from_start")[col].reindex(times)
        return np.round(v.to_numpy(float), nd)

    data = {}
    for c in cells:
        data[c] = {"prb": arr("ul_prb_pct", c, 1), "p10": arr("ul_tput_p10_mbps", c, 2),
                   "fc": arr("forecast_ul_prb_pct", c, 1), "r60": arr("risk60", c, 3),
                   "r120": arr("risk120", c, 3), "r180": arr("risk180", c, 3),
                   "file_r60": arr("pred_congestion_risk_60s", c, 3),
                   "cong": arr("congestion_flag", c, 0), "rrc": arr("rrc_connected", c, 0)}
    phases = s[s["cell_id"] == cells[0]].set_index("seconds_from_start")["event_phase"].reindex(times).tolist()

    # backhaul (15 s, live rows of file 25)
    bl = dl.backhaul()["live"].copy()
    bl["sfs"] = ((pd.to_datetime(bl["timestamp"]) - ev_start).dt.total_seconds()).astype(int)
    links = sorted(bl["link_id"].unique())
    bh_util = {lk: np.round(bl[bl["link_id"] == lk].set_index("sfs")["utilization_pct"].reindex(times)
                            .ffill().bfill().to_numpy(float), 1) for lk in links}
    bh_tot = {lk: bl[bl["link_id"] == lk].set_index("sfs")["venue_ul_gbps"].add(
        bl[bl["link_id"] == lk].set_index("sfs")["venue_dl_gbps"]).reindex(times).ffill().bfill().to_numpy(float)
              for lk in links}
    caps = bl.groupby("link_id")["capacity_gbps"].max().to_dict()
    site_link = {r.site_id: r.link_id for r in dl.small("bh_links").itertuples() if r.link_level == "site"}
    agg_link = next(lk for lk in links if lk.endswith("-AGG"))
    nb = dl.small("neighbors")

    # ---------------- decision timeline
    decisions, alerts = [], []
    busy_until = {c: -10**9 for c in cells}
    run = {c: 0 for c in cells}
    prev_r = {c: 0.0 for c in cells}
    prev_cong = {c: 0 for c in cells}
    n = 0
    vw = st["verify_window_s"]
    for i, t in enumerate(times):
        for c in cells:
            d = data[c]
            prb, p10 = float(d["prb"][i]), float(d["p10"][i])
            run[c] = run[c] + 1 if (prb > th["ul_prb_pct_gt"] and p10 < th["ul_p10_mbps_lt"]) else 0
            if d["cong"][i] and not prev_cong[c]:
                alerts.append({"t": int(t), "cell": c, "severity": "critical",
                               "text": f"{c}: congestion rule met (UL PRB {prb:.0f}%, P10 {p10:.1f} Mbps)"})
            if d["r60"][i] >= th["alert_probability"] > prev_r[c]:
                alerts.append({"t": int(t), "cell": c, "severity": "warning",
                               "text": f"{c}: 60-s congestion risk {d['r60'][i]:.0%} - early warning"})
            prev_cong[c] = int(d["cong"][i]); prev_r[c] = float(d["r60"][i])
            if t < busy_until[c]:
                continue
            site = cs.loc[c, "site_id"]
            lk = site_link.get(site)
            site_cells = [x for x in cells if cs.loc[x, "site_id"] == site]
            share = prb / max(sum(float(data[x]["prb"][i]) for x in site_cells), 1e-6)
            state = de.CellState(c, site, prb, p10, float(d["r60"][i]), float(d["r180"][i]), run[c],
                                 traffic_gbps=float(bh_tot[lk][i]) * share if lk else 0.1)
            lvl = de.risk_level(state, th)
            if lvl == "low":
                continue
            nbs = [de.Neighbor(r.neighbor_cell_id, cs.loc[r.neighbor_cell_id, "site_id"], float(r.overlap_score),
                               r.steering_eligibility, float(data[r.neighbor_cell_id]["prb"][i]))
                   for r in nb[nb["cell_id"] == c].itertuples() if r.neighbor_cell_id in data]
            bh = de.Backhaul({sid: float(bh_util[l][i]) for sid, l in site_link.items() if l in bh_util},
                             {sid: float(caps[l]) for sid, l in site_link.items() if l in caps},
                             float(bh_util[agg_link][i]))
            dec = de.decide(state, nbs, bh, th, st)
            n += 1
            j = min(i + vw // 15, len(times) - 1)
            ver = de.verify(dec, float(d["prb"][j]),
                            float(data[dec.target_cell_id]["prb"][j]) if dec.target_cell_id else 0.0, th)
            rec = dec.to_dict()
            rec.update({"decision_id": f"LIVE-{n:04d}", "t": int(t), "verify_t": int(times[j]),
                        "time": (ev_start + pd.Timedelta(seconds=int(t))).strftime("%H:%M:%S"),
                        "verify": ver, "phase": phases[i]})
            decisions.append(rec)
            alerts.append({"t": int(t), "cell": c, "severity": "info" if dec.tier != de.TIER3 else "critical",
                           "text": f"{c}: {dec.recommendation.replace('_', ' ')} → {dec.tier}"
                                   + (f" (target {dec.target_cell_id})" if dec.target_cell_id else ""),
                           "decision_id": rec["decision_id"]})
            busy_until[c] = t + st["cooldown_s"]
    peak = {}
    if peak_scores is not None:
        for r in peak_scores.itertuples():
            peak[r.cell_id] = {"pred": r.peak_pred, "low": r.peak_low, "high": r.peak_high}
    # whole-stream accuracy of the provided forecast vs actual
    err = s["ul_prb_pct"] - s["forecast_ul_prb_pct"]
    return {
        "event_id": config.LIVE_EVENT_ID, "event_start": ev_start.strftime("%Y-%m-%d %H:%M"),
        "times": times.tolist(), "phases": phases, "cells": cells,
        "cell_meta": {c: {"site_id": cs.loc[c, "site_id"], "band": cs.loc[c, "band"],
                          "technology": cs.loc[c, "technology"], "zone_id": cs.loc[c, "zone_id"]} for c in cells},
        "series": {c: {k: v.tolist() for k, v in data[c].items()} for c in cells},
        "backhaul": {"links": links, "util": {k: v.tolist() for k, v in bh_util.items()},
                     "capacity_gbps": {k: float(v) for k, v in caps.items()}},
        "decisions": decisions, "alerts": sorted(alerts, key=lambda a: a["t"]),
        "peak_forecast": peak,
        "thresholds": {k: th[k] for k in ("ul_prb_pct_gt", "ul_p10_mbps_lt", "alert_probability")},
        "forecast_mae": round(float(err.abs().mean()), 2), "forecast_bias": round(float(err.mean()), 2),
    }
