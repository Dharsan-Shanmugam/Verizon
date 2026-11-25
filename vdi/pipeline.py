"""CLI: python -m vdi.pipeline [cache|train|score|all]

train  - builds the cache, trains attendance / peak-uplink / short-horizon
         models with a time-based split (train <= 2024, test 2025-2026),
         refits on all history and writes artifacts/ + artifacts/metrics.json
score  - scores the upcoming 30-day events + the live event and writes
         artifacts/scores.json and artifacts/live.json
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import config
from . import data_loader as dl
from . import features as F
from . import monitoring as mon
from . import similar_events as se
from .models.attendance import AttendanceModel
from .models.peak_uplink import PeakUplinkModel
from .models.short_horizon import HORIZONS, ShortHorizonModel, split_events
from .simulator import fit_curves

log = logging.getLogger("vdi.pipeline")
DRIFT_FEATURES = {"log_load": "demand-to-capacity load index", "base_prb": "non-event baseline UL PRB",
                  "log_ul_cap": "cell UL capacity (upgrades)"}


def jsonable(o):
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return [jsonable(v) for v in o.tolist()]
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        f = float(o)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    if o is pd.NA or o is pd.NaT:
        return None
    return o


def write_json(path, obj):
    path.write_text(json.dumps(jsonable(obj), indent=1))


def completed_events():
    ev = dl.events()
    return ev[(ev["event_status"] == "completed") & ev["actual_attendance"].notna()].reset_index(drop=True)


# ------------------------------------------------------------------ train
def train() -> dict:
    t0 = time.time()
    timings = {"cache_build_s": dl.build_all()}
    art = config.artifacts_dir()
    c = completed_events()
    split = config.TRAIN_END_YEAR
    tr, te = c[c["year"] <= split], c[c["year"] > split]
    metrics = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "model_version": config.MODEL_VERSION, "split": f"train <= {split}, test {split + 1}-2026"}

    # attendance
    t = time.time()
    am = AttendanceModel().fit(tr)
    metrics["attendance"] = {"train": am.evaluate(tr), "test": am.evaluate(te), "blend_weight_ridge": am.w_ridge,
                             "knn_k": am.k, "n_features": len(am.cols)}
    pa_split = am.predict(c).set_index("event_id")["attendance_pred"]
    am_final = AttendanceModel().fit(c)
    am_final.save(art)
    timings["attendance_s"] = round(time.time() - t, 1)

    # peak uplink
    t = time.time()
    h = dl.hourly()
    base = dl.daily()["baseline"]
    ev = dl.events()
    ce = h["cell_event"].merge(c[["event_id", "year", "event_start"]], on="event_id")
    rtr, rte = ce[ce["year"] <= split].reset_index(drop=True), ce[ce["year"] > split].reset_index(drop=True)
    pm = PeakUplinkModel().fit(rtr, pa_split.loc[rtr["event_id"]].to_numpy(), ev, base, h["shape_raw"])
    metrics["peak_uplink"] = {"train": pm.evaluate(rtr, pa_split.loc[rtr["event_id"]].to_numpy(), ev, base),
                              "test": pm.evaluate(rte, pa_split.loc[rte["event_id"]].to_numpy(), ev, base),
                              "n_features": len(pm.cols)}
    pt = pm.predict(rte, pa_split.loc[rte["event_id"]].to_numpy(), ev, base)
    pt["actual"] = rte["peak_ul_prb"].to_numpy()
    pt["month"] = rte["event_start"].dt.strftime("%Y-%m").to_numpy()
    backtest = mon.backtest_monthly(pt)
    # drift: training distribution vs most recent period
    ftr = F.peak_frame(rtr[["event_id", "cell_id"]], pa_split.loc[rtr["event_id"]].to_numpy(), ev, pm.cell_factor, base)
    fte = F.peak_frame(rte[["event_id", "cell_id"]], pa_split.loc[rte["event_id"]].to_numpy(), ev, pm.cell_factor, base)
    drift = mon.drift_report((tr["actual_attendance"] / F.cap_ref(tr)).to_numpy(),
                             (te["actual_attendance"] / F.cap_ref(te)).to_numpy(),
                             rtr["peak_ul_prb"].to_numpy(), rte["peak_ul_prb"].to_numpy(),
                             {lbl: ftr[k].to_numpy() for k, lbl in DRIFT_FEATURES.items()},
                             {lbl: fte[k].to_numpy() for k, lbl in DRIFT_FEATURES.items()})
    pa_all = am_final.predict(c).set_index("event_id")["attendance_pred"]
    pm_final = PeakUplinkModel().fit(ce, pa_all.loc[ce["event_id"]].to_numpy(), ev, base, h["shape_raw"])
    pm_final.save(art)
    timings["peak_uplink_s"] = round(time.time() - t, 1)

    # short horizon
    t = time.time()
    tel = dl.telemetry()
    sh = ShortHorizonModel()
    X = sh.features(tel)
    trm, tem, tr_ids, te_ids = split_events(tel)
    sh.fit(tel[trm].reset_index(drop=True), X[trm])
    metrics["short_horizon"] = {"train_events": len(tr_ids), "test_events": len(te_ids),
                                "rows_train": int(trm.sum()), "rows_test": int(tem.sum()),
                                "test": sh.evaluate(tel[tem].reset_index(drop=True), X[tem]),
                                "drivers_60s": sh.top_drivers(60), "n_features": len(sh.cols),
                                "excluded_features": sorted(dl.FORBIDDEN_FEATURES)}
    sh_final = ShortHorizonModel()
    sh_final.cols = sh.cols
    sh_final.fit(tel, X)
    sh_final.save(art)
    timings["short_horizon_s"] = round(time.time() - t, 1)

    from .models.outlook import OutlookModel
    t = time.time()
    metrics["outlook"] = OutlookModel.load_or_train().report()
    timings["outlook_s"] = round(time.time() - t, 1)

    # response curves for the simulator
    write_json(art / "curves.json", fit_curves(h["curves"], dl.small("counterfactual")))
    metrics["monitoring"] = {"backtest_monthly": backtest, "drift": drift}
    timings["total_s"] = round(time.time() - t0, 1)
    metrics["timings"] = timings
    metrics["data"] = {"events_completed": int(len(c)), "cell_events": int(len(ce)),
                       "telemetry_rows": int(len(tel)), "data_dir": config.data_dir().name}
    write_json(art / "metrics.json", metrics)
    log.info("training done in %.1fs", timings["total_s"])
    return metrics


# ------------------------------------------------------------------ load
def artifacts_ready() -> bool:
    a = config.artifacts_dir()
    return all((a / f).exists() for f in ["attendance.json", "attendance_knn.npz", "peak_uplink.json",
                                          "short_horizon.json", "curves.json", "metrics.json"])


def load_models() -> dict:
    a = config.artifacts_dir()
    return {"attendance": AttendanceModel.load(a), "peak_uplink": PeakUplinkModel.load(a),
            "short_horizon": ShortHorizonModel.load(a)}


def load_curves() -> dict:
    return json.loads((config.artifacts_dir() / "curves.json").read_text())


# ------------------------------------------------------------------ score
def risk_label(peak, p85):
    th = config.thresholds()
    if peak >= th["risk_high_prb_pct"] or p85 >= 0.5:
        return "high"
    if peak >= th["risk_medium_prb_pct"] or p85 >= 0.2:
        return "medium"
    return "low"


def score_upcoming(models: dict) -> dict:
    ev = dl.events()
    up = ev[ev["is_upcoming"]].reset_index(drop=True)
    am, pm = models["attendance"], models["peak_uplink"]
    att = am.predict(up)
    cells = F.cell_static()
    base = dl.daily()["baseline"]
    pairs = up[["event_id", "venue_id"]].merge(cells[["cell_id", "venue_id"]], on="venue_id")
    attmap = att.set_index("event_id")["attendance_pred"]
    pk = pm.predict(pairs[["event_id", "cell_id"]], attmap.loc[pairs["event_id"]].to_numpy(), ev, base)
    f19 = dl.small("forecasts_upcoming").groupby("event_id")["forecast_peak_ul_prb_pct"].max()
    out = []
    for i, r in up.iterrows():
        a = att.iloc[i]
        p = pk[pk["event_id"] == r["event_id"]]
        cat = r["event_category"]
        heat = []
        for row in p.itertuples():
            hr = pm.hourly(cat, row.z)
            heat.append({"cell_id": row.cell_id, "values": [x["prb"] for x in hr], "risk": [x["risk"] for x in hr]})
        mx = float(p["peak_pred"].max()) if len(p) else 0.0
        p85 = float(p["p_over_85"].max()) if len(p) else 0.0
        start = r["event_start"]
        out.append({
            "event_id": r["event_id"], "venue_id": r["venue_id"], "venue_name": r["venue_name"], "city": r["city"],
            "event_name": r["event_name"], "category": cat, "subtype": r["event_subtype"],
            "home_away": r["anchor_team_home_away"], "start": start.strftime("%Y-%m-%d %H:%M"),
            "status": r["event_status"], "capacity": int(F.cap_ref(up.iloc[[i]])[0]),
            "expected_attendance": int(r["expected_attendance"]),
            "attendance_pred": int(a["attendance_pred"]), "attendance_low": int(a["attendance_low"]),
            "attendance_high": int(a["attendance_high"]),
            "peak_prb_max": round(mx, 1), "p_over_85": round(p85, 3), "risk": risk_label(mx, p85),
            "hot_cells": int((p["peak_pred"] >= 85).sum() + 0), "watch_cells": int((p["peak_pred"] >= 70).sum()),
            "provided_forecast_peak": None if r["event_id"] not in f19.index else float(f19.loc[r["event_id"]]),
            "cells": [{"cell_id": x.cell_id, "pred": x.peak_pred, "low": x.peak_low, "high": x.peak_high,
                       "p_over_85": x.p_over_85} for x in p.itertuples()],
            "heatmap": {"hours": [(start + pd.Timedelta(hours=h)).strftime("%H:%M") for h in range(-6, 7)],
                        "hour_rel": list(range(-6, 7)), "rows": heat},
            "signals": {k: (None if pd.isna(r[k]) else (bool(r[k]) if isinstance(r[k], (bool, np.bool_)) else r[k]))
                        for k in ["home_crowd_multiplier", "anchor_team_win_pct_to_date", "standings_position",
                                  "social_interest_index", "social_mentions_7d", "search_trend_index",
                                  "ticket_resale_price_index", "transit_advisory", "road_closure_nearby",
                                  "competing_events_within_10mi", "local_school_holiday", "signal_confidence",
                                  "ticket_sell_through_pct", "estimate_confidence", "weather_temp_f",
                                  "weather_condition", "rivalry_flag", "primetime_flag"]},
            "similar": se.find_similar(r["event_id"], am, k=6),
            "similar_file": se.file_similar(r["event_id"])[:6],
        })
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "model_version": config.MODEL_VERSION, "events": out}


def score() -> dict:
    t0 = time.time()
    from .live import build_live
    models = load_models()
    sc = score_upcoming(models)
    art = config.artifacts_dir()
    write_json(art / "scores.json", sc)
    live_ev = next((e for e in sc["events"] if e["event_id"] == config.LIVE_EVENT_ID), None)
    peak = pd.DataFrame(live_ev["cells"]).rename(columns={"pred": "peak_pred", "low": "peak_low", "high": "peak_high"}) \
        if live_ev else None
    lv = build_live(models, peak)
    write_json(art / "live.json", lv)
    secs = round(time.time() - t0, 1)
    n_high = sum(e["risk"] == "high" for e in sc["events"])
    log.info("scored %d upcoming events (%d high risk) + live stream with %d decisions in %.1fs",
             len(sc["events"]), n_high, len(lv["decisions"]), secs)
    m = json.loads((art / "metrics.json").read_text()) if (art / "metrics.json").exists() else {}
    m.setdefault("timings", {})["score_s"] = secs
    m["scoring"] = {"upcoming_events": len(sc["events"]), "high_risk_events": n_high,
                    "live_decisions": len(lv["decisions"]), "live_alerts": len(lv["alerts"])}
    write_json(art / "metrics.json", m)
    return {"events": len(sc["events"]), "high_risk": n_high, "live_decisions": len(lv["decisions"]), "seconds": secs}


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="python -m vdi.pipeline")
    ap.add_argument("command", choices=["cache", "train", "score", "all"])
    a = ap.parse_args(argv)
    missing = dl.check_data()
    if missing:
        raise SystemExit(f"Data folder {config.data_dir()} is missing {len(missing)} files "
                         f"(e.g. {missing[0]}). Unzip 'Verizon data.zip' into ./data/ or set VDI_DATA_DIR.")
    if a.command == "cache":
        print(json.dumps(dl.build_all(), indent=1))
    if a.command in ("train", "all"):
        m = train()
        print(json.dumps(jsonable({k: m[k] for k in ["attendance", "peak_uplink", "timings"]}), indent=1))
        print(json.dumps(jsonable(m["short_horizon"]["test"]), indent=1))
    if a.command in ("score", "all"):
        print(json.dumps(score(), indent=1))


if __name__ == "__main__":
    main()
