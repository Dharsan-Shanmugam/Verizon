"""Learn & govern: accuracy, bias, interval coverage, PSI drift and alerts."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import data_loader as dl
from .models.core import psi

PSI_WATCH, PSI_ALERT = 0.10, 0.25


def psi_status(v: float) -> str:
    if not np.isfinite(v):
        return "n/a"
    return "alert" if v >= PSI_ALERT else ("watch" if v >= PSI_WATCH else "ok")


def backtest_monthly(pred: pd.DataFrame) -> list[dict]:
    """pred: event_id, cell_id, month, actual, peak_pred, peak_low, peak_high."""
    g = pred.assign(err=pred["peak_pred"] - pred["actual"],
                    ape=(pred["peak_pred"] - pred["actual"]).abs() / pred["actual"].clip(lower=1),
                    inside=(pred["actual"] >= pred["peak_low"]) & (pred["actual"] <= pred["peak_high"]))
    out = g.groupby("month").agg(n=("err", "size"), mae=("err", lambda s: s.abs().mean()),
                                 bias=("err", "mean"), mape=("ape", "mean"),
                                 coverage=("inside", "mean")).reset_index()
    out["mape"] *= 100
    return [{k: (round(float(v), 3) if isinstance(v, (float, np.floating)) else v) for k, v in r.items()}
            for r in out.to_dict("records")]


def drift_report(train_att, recent_att, train_prb, recent_prb, train_feats: dict, recent_feats: dict) -> list[dict]:
    rows = [{"feature": "attendance ratio", "psi": psi(train_att, recent_att)},
            {"feature": "peak UL PRB", "psi": psi(train_prb, recent_prb)}]
    for k in train_feats:
        rows.append({"feature": k, "psi": psi(train_feats[k], recent_feats[k])})
    for r in rows:
        r["psi"] = round(r["psi"], 4) if np.isfinite(r["psi"]) else None
        r["status"] = psi_status(r["psi"] if r["psi"] is not None else np.nan)
    return rows


def history_monitoring(step_days: int = 7) -> dict:
    """File 18 (provided monitoring), downsampled weekly for the browser."""
    m = dl.small("monitoring_daily").copy()
    m["date"] = pd.to_datetime(m["date"])
    wk = m.set_index("date").resample(f"{step_days}D").agg({
        "model_version": "last", "rolling_30d_mape_pct": "mean", "rolling_30d_bias_prb": "mean",
        "rolling_30d_interval_coverage": "mean", "psi_attendance": "max", "psi_ul_prb": "max",
        "drift_flag": "max", "events_scored": "sum"}).reset_index()
    wk = wk.dropna(subset=["rolling_30d_mape_pct"])
    series = [{"date": d.strftime("%Y-%m-%d"), "version": v, "mape": round(float(a), 2), "bias": round(float(b), 2),
               "coverage": round(float(c), 3), "psi_att": round(float(p1), 3), "psi_prb": round(float(p2), 3),
               "drift": bool(f)} for d, v, a, b, c, p1, p2, f in zip(
        wk["date"], wk["model_version"], wk["rolling_30d_mape_pct"], wk["rolling_30d_bias_prb"],
        wk["rolling_30d_interval_coverage"], wk["psi_attendance"], wk["psi_ul_prb"], wk["drift_flag"])]
    m["drift_note"] = m["drift_note"].fillna("")
    episodes = []
    flagged = m[m["drift_flag"]]
    if not flagged.empty:
        grp = (flagged["date"].diff().dt.days.fillna(99) > 3).cumsum()
        for _, g in flagged.groupby(grp):
            episodes.append({"start": g["date"].min().strftime("%Y-%m-%d"), "end": g["date"].max().strftime("%Y-%m-%d"),
                             "days": int(len(g)), "note": g["drift_note"].mode().iat[0] if (g["drift_note"] != "").any() else "drift",
                             "max_psi_prb": round(float(g["psi_ul_prb"].max()), 3)})
    versions = m.groupby("model_version").agg(first=("date", "min"), last=("date", "max"),
                                              mape=("rolling_30d_mape_pct", "mean"),
                                              coverage=("rolling_30d_interval_coverage", "mean"),
                                              events=("events_scored", "sum")).reset_index()
    versions = [{"version": r.model_version, "from": r.first.strftime("%Y-%m-%d"), "to": r.last.strftime("%Y-%m-%d"),
                 "mape": round(float(r.mape), 2), "coverage": round(float(r.coverage), 3), "events": int(r.events)}
                for r in versions.itertuples()]
    return {"series": series, "episodes": episodes, "versions": versions}


def live_drift() -> dict:
    m = dl.small("live_monitoring").copy()
    last = m.sort_values("timestamp").groupby("cell_id").tail(1)
    alerts = m[m["drift_alert"]]
    return {"cells": [{"cell_id": r.cell_id, "mae_5min": r.mae_5min_ul_prb, "bias_5min": r.bias_5min_ul_prb,
                       "drift_score": None if pd.isna(r.drift_score) else round(float(r.drift_score), 2),
                       "alerts": int((alerts["cell_id"] == r.cell_id).sum())} for r in last.itertuples()],
            "alert_count": int(len(alerts)),
            "first_alert": None if alerts.empty else str(alerts["timestamp"].min())}


def provided_forecast_accuracy() -> list[dict]:
    """File 17: backtest of the incumbent forecast by model version."""
    f = dl.small("fva")
    g = f.groupby("model_version").agg(n=("event_id", "size"), mape=("abs_pct_error_peak_prb", "mean"),
                                       att_mape=("abs_pct_error_attendance", "mean"),
                                       coverage=("within_interval", "mean")).reset_index()
    return [{"version": r.model_version, "n": int(r.n), "mape": round(float(r.mape), 2),
             "attendance_mape": round(float(r.att_mape), 2), "coverage": round(float(r.coverage), 3)}
            for r in g.itertuples()]
