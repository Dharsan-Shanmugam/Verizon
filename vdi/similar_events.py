"""Similar past events: kNN in the attendance-model context space + file 11."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import data_loader as dl
from . import features as F


def _venue_sim() -> dict:
    m = dl.small("similar_venues")
    out = {(r.venue_id, r.similar_venue_id): float(r.overall_similarity) for r in m.itertuples()}
    for v in dl.small("venues")["venue_id"]:
        out[(v, v)] = 1.0
    return out


def find_similar(event_id: str, att_model, k: int = 8) -> list[dict]:
    ev = dl.events()
    row = ev[ev["event_id"] == event_id]
    if row.empty:
        return []
    row = row.iloc[0]
    pool = ev[(ev["event_status"] == "completed") & ev["actual_attendance"].notna()
              & (ev["event_start"] < row["event_start"]) & (ev["event_id"] != event_id)]
    pool = pool[pool["event_category"] == row["event_category"]]
    if pool.empty:
        return []
    fq = att_model._X(ev[ev["event_id"] == event_id])
    fp = att_model._X(pool)
    zq = att_model.knn_scaler.transform(fq[F.KNN_COLS].to_numpy()) * F.KNN_WEIGHTS
    zp = att_model.knn_scaler.transform(fp[F.KNN_COLS].to_numpy()) * F.KNN_WEIGHTS
    d = np.sqrt(((zp - zq[0]) ** 2).sum(1))
    vs = _venue_sim()
    vsim = np.array([vs.get((row["venue_id"], v), 0.5) for v in pool["venue_id"]])
    score = np.exp(-d / 2.0) * (0.6 + 0.4 * vsim)
    top = np.argsort(-score)[:k]
    sel = pool.iloc[top]
    ce = dl.hourly()["cell_event"]
    agg = ce[ce["event_id"].isin(sel["event_id"])].groupby("event_id").agg(
        peak_ul_prb=("peak_ul_prb", "max"), congestion_min=("congestion_min", "sum"),
        hot_cells=("peak_ul_prb", lambda s: int((s >= 85).sum())))
    out = []
    for i, (_, r) in zip(top, sel.iterrows()):
        a = agg.loc[r["event_id"]] if r["event_id"] in agg.index else None
        out.append({
            "event_id": r["event_id"], "venue_id": r["venue_id"], "venue_name": r["venue_name"],
            "event_name": r["event_name"], "date": r["event_start"].strftime("%Y-%m-%d"),
            "home_away": r["anchor_team_home_away"], "actual_attendance": int(r["actual_attendance"]),
            "similarity": round(float(score[i]), 3),
            "peak_ul_prb": None if a is None else round(float(a["peak_ul_prb"]), 1),
            "congestion_min": None if a is None else round(float(a["congestion_min"]), 1),
            "hot_cells": None if a is None else int(a["hot_cells"]),
        })
    return out


def file_similar(event_id: str) -> list[dict]:
    s = dl.small("similar_events")
    s = s[s["event_id"] == event_id].sort_values("similarity_rank")
    return [{"rank": int(r.similarity_rank), "event_id": r.similar_event_id,
             "venue_id": r.similar_venue_id, "date": str(r.similar_event_start)[:10],
             "home_away": r.similar_home_away, "actual_attendance": int(r.similar_actual_attendance),
             "similarity": float(r.similarity_score)} for r in s.itertuples()]
