"""Data layer: typed CSV loads + derived tables cached as pickle.

Large files (12, 13, 14, 24, 25, 27, 31, 32) are read once with `usecols`,
reduced to the tables the models/UI need, and pickled into cache/.  The
cache is invalidated when CACHE_VERSION changes or a source file is newer.
"""
from __future__ import annotations

import logging
import os
import pickle
import time
from functools import lru_cache
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from . import config

log = logging.getLogger("vdi.data")
CACHE_VERSION = 4

FILES = {
    "dictionary": "00_data_dictionary.csv",
    "provenance": "00_data_provenance.csv",
    "venues": "01_venues.csv",
    "similar_venues": "02_similar_venue_map.csv",
    "zones": "03_venue_zones.csv",
    "cells": "04_cell_inventory.csv",
    "cell_capacity": "05_cell_capacity_history.csv",
    "neighbors": "06_cell_neighbors.csv",
    "weather": "07_context_weather_daily.csv",
    "events_hist": "08_events_historical.csv",
    "events_upcoming": "09_events_upcoming_30d.csv",
    "signals": "10_context_event_signals.csv",
    "similar_events": "11_similar_events_top12.csv",
    "kpi_hourly": "12_network_kpi_event_hourly.csv",
    "kpi_daily": "13_network_kpi_cell_daily.csv",
    "telemetry": "14_network_telemetry_15s_target_venue_12m.csv",
    "live": "15_live_signals_15s_current_event.csv",
    "live_monitoring": "16_live_model_monitoring_1min.csv",
    "fva": "17_forecasts_vs_actuals_history.csv",
    "monitoring_daily": "18_model_monitoring_daily.csv",
    "forecasts_upcoming": "19_forecasts_upcoming_hourly.csv",
    "decisions": "20_decisions_log.csv",
    "thresholds": "21_thresholds_policy.csv",
    "bh_links": "22_backhaul_links.csv",
    "bh_capacity": "23_backhaul_capacity_history.csv",
    "bh_hourly": "24_backhaul_utilization_event_hourly.csv",
    "bh_15s": "25_backhaul_utilization_15s_target_venue.csv",
    "safety_checks": "26_steering_safety_checks.csv",
    "drops_hourly": "27_network_accessibility_drops_event_hourly.csv",
    "temp_capacity": "28_temporary_capacity_deployments.csv",
    "effort": "29_engineer_effort_log.csv",
    "gatherings": "30_unplanned_gatherings.csv",
    "gathering_tel": "31_unplanned_gathering_telemetry_15s_V01.csv",
    "traffic_mix": "32_traffic_mix_by_app_event_hourly.csv",
    "device_mix": "33_device_mix_event.csv",
    "actions": "34_steering_actions_detail.csv",
    "counterfactual": "35_counterfactual_simulation.csv",
    "service_quality": "36_service_quality_15s_current_event.csv",
}

# Never allowed into a model feature matrix (simulator ground truth).
FORBIDDEN_FEATURES = {"true_occupancy_sim_only"}


class DataNotFound(RuntimeError):
    pass


def path_of(key: str) -> Path:
    return config.data_dir() / FILES[key]


def check_data() -> list[str]:
    """Return the list of missing CSV files (empty = all present)."""
    return [f for key, f in FILES.items() if key != "service_quality" and not (config.data_dir() / f).exists()]


def read(key: str, usecols=None, dtype=None) -> pd.DataFrame:
    p = path_of(key)
    if not p.exists():
        raise DataNotFound(
            f"Missing {p}. Unzip 'Verizon data.zip' into ./data/ or set VDI_DATA_DIR.")
    return pd.read_csv(p, usecols=usecols, dtype=dtype, low_memory=False)


@lru_cache(maxsize=None)
def small(key: str) -> pd.DataFrame:
    """Small / medium files kept in memory (read-only use: callers must copy)."""
    return read(key)


# ----------------------------------------------------------------- caching
def _cached(name: str, sources: list[str], builder: Callable[[], object]):
    cdir = config.cache_dir()
    fp = cdir / f"{name}.pkl"
    src_mtime = max(os.path.getmtime(path_of(s)) for s in sources)
    if fp.exists():
        try:
            with open(fp, "rb") as fh:
                meta, obj = pickle.load(fh)
            if meta.get("v") == CACHE_VERSION and meta.get("mtime", 0) >= src_mtime:
                return obj
        except Exception:  # corrupt cache -> rebuild
            pass
    t = time.time()
    obj = builder()
    tmp = fp.with_suffix(".tmp")
    with open(tmp, "wb") as fh:
        pickle.dump(({"v": CACHE_VERSION, "mtime": src_mtime}, obj), fh,
                    protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, fp)
    log.info("built cache %s in %.1fs", name, time.time() - t)
    return obj


_MEM: dict[str, object] = {}


def _memo(name: str, sources: list[str], builder):
    if name not in _MEM:
        _MEM[name] = _cached(name, sources, builder)
    return _MEM[name]


def clear_memory():
    _MEM.clear()
    small.cache_clear()


# ----------------------------------------------------------------- builders
def _build_events() -> pd.DataFrame:
    hist = read("events_hist")
    up = read("events_upcoming")
    hist["is_upcoming"] = False
    up["is_upcoming"] = True
    ev = pd.concat([hist, up], ignore_index=True)
    ev["event_start"] = pd.to_datetime(ev["event_start"])
    ev["year"] = ev["event_start"].dt.year
    sig = read("signals").drop(columns=["venue_id", "anchor_team_home_away", "data_source"])
    ev = ev.merge(sig, on="event_id", how="left")
    ven = read("venues")[["venue_id", "venue_name", "city", "seating_capacity",
                          "concert_capacity", "roof_type", "transit_lines_nearby", "num_cells"]]
    ev = ev.merge(ven, on="venue_id", how="left")
    dev = read("device_mix", usecols=["event_id", "share_5g_sa_pct", "share_5g_nsa_pct",
                                       "ul_mimo_capable_pct", "connected_devices_est",
                                       "avg_ul_mb_per_device", "avg_dl_mb_per_device"])
    ev = ev.merge(dev, on="event_id", how="left")
    for c in ["rivalry_flag", "holiday_flag", "primetime_flag", "transit_advisory",
              "road_closure_nearby", "local_school_holiday"]:
        ev[c] = ev[c].astype("boolean").fillna(False).astype(bool)
    return ev.sort_values("event_start").reset_index(drop=True)


def _build_hourly() -> dict:
    """Per cell-event peaks + hourly shape profiles + KPI response curves."""
    k = read("kpi_hourly", usecols=["event_id", "cell_id", "hour_start", "hours_from_start",
                                    "event_phase", "peak_ul_prb_pct", "avg_rrc_connected",
                                    "ul_volume_gb", "dl_volume_gb", "ul_tput_p10_mbps",
                                    "congestion_minutes"])
    d = read("drops_hourly", usecols=["event_id", "cell_id", "hour_start", "accessibility_pct",
                                      "session_drop_rate_pct", "sessions_established",
                                      "abnormal_session_releases", "ul_packet_loss_pct"])
    k = k.merge(d, on=["event_id", "cell_id", "hour_start"], how="left")
    k["hour_rel"] = np.floor(k["hours_from_start"]).astype(int)
    g = k.groupby(["event_id", "cell_id"], sort=False)
    ce = g.agg(peak_ul_prb=("peak_ul_prb_pct", "max"),
               min_ul_p10=("ul_tput_p10_mbps", "min"),
               congestion_min=("congestion_minutes", "sum"),
               peak_rrc=("avg_rrc_connected", "max"),
               ul_gb=("ul_volume_gb", "sum"),
               dl_gb=("dl_volume_gb", "sum"),
               sessions=("sessions_established", "sum"),
               drops=("abnormal_session_releases", "sum"),
               min_access=("accessibility_pct", "min")).reset_index()
    ce["drop_rate_pct"] = np.where(ce["sessions"] > 0, 100 * ce["drops"] / ce["sessions"].clip(lower=1), 0.0)
    # hourly shape: hourly peak / cell-event peak, by relative hour
    k = k.merge(ce[["event_id", "cell_id", "peak_ul_prb"]], on=["event_id", "cell_id"])
    k["shape"] = k["peak_ul_prb_pct"] / k["peak_ul_prb"].clip(lower=1)
    rng = np.random.default_rng(7)
    idx = rng.choice(len(k), size=min(150_000, len(k)), replace=False)
    curves = k.iloc[idx][["peak_ul_prb_pct", "ul_tput_p10_mbps", "congestion_minutes",
                          "session_drop_rate_pct", "accessibility_pct", "ul_packet_loss_pct"]]
    curves = curves.dropna().astype("float32").reset_index(drop=True)
    shape_raw = k[["event_id", "hour_rel", "shape", "event_phase"]]
    return {"cell_event": ce, "shape_raw": shape_raw.groupby(["event_id", "hour_rel"], sort=False)
            .agg(shape=("shape", "mean"), phase=("event_phase", "first")).reset_index(),
            "curves": curves}


def _build_traffic_mix() -> dict:
    t = read("traffic_mix", usecols=["event_id", "venue_id", "hour_start", "event_phase",
                                     "app_category", "ul_gb", "dl_gb"])
    t["year"] = t["hour_start"].str.slice(0, 4).astype(int)
    app_year = t.groupby(["year", "app_category"], as_index=False)[["ul_gb", "dl_gb"]].sum()
    ev = t.groupby(["event_id", "venue_id", "year"], as_index=False)[["ul_gb", "dl_gb"]].sum()
    return {"app_year": app_year, "event": ev}


def _build_backhaul() -> dict:
    b = read("bh_hourly", usecols=["link_id", "link_level", "venue_id", "site_id", "event_id",
                                   "hour_start", "venue_ul_gbps", "venue_dl_gbps", "background_gbps",
                                   "total_gbps", "capacity_gbps",
                                   "utilization_pct", "peak_15min_utilization_pct",
                                   "congested_flag", "latency_ms", "packet_loss_pct"])
    ev = b.groupby(["event_id", "link_id"], as_index=False, sort=False).agg(
        link_level=("link_level", "first"), venue_id=("venue_id", "first"),
        site_id=("site_id", "first"), peak_util=("peak_15min_utilization_pct", "max"),
        avg_util=("utilization_pct", "mean"), capacity_gbps=("capacity_gbps", "max"),
        peak_venue_ul_gbps=("venue_ul_gbps", "max"), peak_venue_dl_gbps=("venue_dl_gbps", "max"),
        background_gbps=("background_gbps", "mean"), peak_total_gbps=("total_gbps", "max"),
        congested_hours=("congested_flag", "sum"),
        max_latency_ms=("latency_ms", "max"))
    ev["year"] = ev["event_id"].map(
        _memo("events", ["events_hist", "events_upcoming", "signals", "venues", "device_mix"],
              _build_events).set_index("event_id")["year"])
    s = read("bh_15s", usecols=["timestamp", "link_id", "link_level", "site_id", "event_id",
                                "event_phase", "is_live", "venue_ul_gbps", "venue_dl_gbps",
                                "background_gbps", "total_gbps", "capacity_gbps",
                                "utilization_pct", "headroom_gbps", "latency_ms",
                                "packet_loss_pct", "congested_flag"])
    live = s[s["is_live"]].reset_index(drop=True)
    hist15 = s[~s["is_live"]].groupby(["event_id", "link_id"], as_index=False).agg(
        peak_util=("utilization_pct", "max"), mean_util=("utilization_pct", "mean"),
        congested_15s=("congested_flag", "sum"))
    return {"event_link": ev, "live": live, "hist15_event_link": hist15}


def _build_telemetry() -> pd.DataFrame:
    cols = ["timestamp", "event_id", "cell_id", "seconds_from_start", "event_phase",
            "ul_prb_pct", "dl_prb_pct", "rrc_connected", "active_ues", "ul_bytes_mb",
            "ul_tput_p50_mbps", "ul_tput_p10_mbps", "cqi", "sinr_db", "power_headroom_db",
            "handover_attempts", "rach_attempts", "rach_failures", "congestion_flag",
            "label_congestion_next_60s", "label_congestion_next_120s",
            "label_congestion_next_180s"]
    t = read("telemetry", usecols=cols)
    assert not FORBIDDEN_FEATURES & set(t.columns)
    for c in t.columns:
        if t[c].dtype == "float64" or t[c].dtype == "int64":
            if c != "seconds_from_start":
                t[c] = t[c].astype("float32")
    for c in ["event_id", "cell_id", "event_phase"]:
        t[c] = t[c].astype("category")
    return t.sort_values(["event_id", "cell_id", "seconds_from_start"]).reset_index(drop=True)


def _build_gathering_tel() -> pd.DataFrame:
    g = read("gathering_tel", usecols=["gathering_id", "cell_id", "seconds_from_start",
                                       "ul_prb_pct", "ul_tput_p10_mbps", "rrc_connected",
                                       "congestion_flag"])
    g["gathering_id"] = g["gathering_id"].astype("category")
    g["cell_id"] = g["cell_id"].astype("category")
    for c in ["ul_prb_pct", "ul_tput_p10_mbps", "rrc_connected"]:
        g[c] = g[c].astype("float32")
    return g


def _build_daily() -> dict:
    d = read("kpi_daily", usecols=["date", "venue_id", "cell_id", "is_event_day",
                                   "peak_ul_prb_pct", "daily_ul_gb", "daily_dl_gb",
                                   "congestion_minutes"])
    d["ym"] = d["date"].str.slice(0, 7)
    d["year"] = d["date"].str.slice(0, 4).astype(int)
    base = d[~d["is_event_day"]].groupby(["cell_id", "ym"], as_index=False).agg(
        base_peak_prb=("peak_ul_prb_pct", "mean"), base_ul_gb=("daily_ul_gb", "mean"))
    year = d.groupby(["year"], as_index=False).agg(ul_gb=("daily_ul_gb", "sum"),
                                                   dl_gb=("daily_dl_gb", "sum"),
                                                   congestion_min=("congestion_minutes", "sum"))
    return {"baseline": base, "year": year}


# ----------------------------------------------------------------- public
def events() -> pd.DataFrame:
    return _memo("events", ["events_hist", "events_upcoming", "signals", "venues", "device_mix"],
                 _build_events)


def hourly() -> dict:
    return _memo("hourly", ["kpi_hourly", "drops_hourly"], _build_hourly)


def traffic_mix() -> dict:
    return _memo("traffic_mix", ["traffic_mix"], _build_traffic_mix)


def backhaul() -> dict:
    return _memo("backhaul", ["bh_hourly", "bh_15s"], _build_backhaul)


def telemetry() -> pd.DataFrame:
    return _memo("telemetry", ["telemetry"], _build_telemetry)


def gathering_tel() -> pd.DataFrame:
    return _memo("gathering_tel", ["gathering_tel"], _build_gathering_tel)


def daily() -> dict:
    return _memo("daily", ["kpi_daily"], _build_daily)


def live_stream() -> pd.DataFrame:
    def b():
        df = read("live")
        return df.drop(columns=["data_source"])
    return _memo("live", ["live"], b)


def cell_capacity_at(cell_ids, dates) -> np.ndarray:
    """UL capacity (Mbps) in force for each (cell, date) pair."""
    cap = small("cell_capacity").copy()
    cap["effective_from"] = pd.to_datetime(cap["effective_from"])
    cap = cap.sort_values("effective_from")
    q = pd.DataFrame({"cell_id": np.asarray(cell_ids), "d": pd.to_datetime(np.asarray(dates)),
                      "_i": np.arange(len(cell_ids))}).sort_values("d")
    m = pd.merge_asof(q, cap[["cell_id", "effective_from", "ul_capacity_mbps", "prb_count"]],
                      left_on="d", right_on="effective_from", by="cell_id", direction="backward")
    m = m.sort_values("_i")
    return m["ul_capacity_mbps"].to_numpy(dtype=float), m["prb_count"].to_numpy(dtype=float)


def build_all(verbose: bool = True) -> dict:
    """Build every cache table; returns timings (seconds)."""
    out = {}
    for name, fn in [("events", events), ("hourly", hourly), ("traffic_mix", traffic_mix),
                     ("backhaul", backhaul), ("telemetry", telemetry),
                     ("gathering_tel", gathering_tel), ("daily", daily), ("live", live_stream)]:
        t = time.time()
        fn()
        out[name] = round(time.time() - t, 2)
        if verbose:
            log.info("cache %-14s ready (%.1fs)", name, out[name])
    return out
