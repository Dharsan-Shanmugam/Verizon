"""Feature engineering for the three models (all pre-event / causal features).

`true_occupancy_sim_only` is simulator ground truth and is never used.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import data_loader as dl

CATEGORIES = ["football", "concert", "rally", "other"]
HOME_AWAY = ["home", "away", "neutral"]
SUBTYPE_FLAGS = ["playoff", "preseason", "festival", "tour", "college football"]
BANDS = ["B13", "B2", "B66", "n77", "n261"]
HOME_MULT_DEFAULT = {"home": 1.0, "away": 0.33, "neutral": 0.85}


def cap_ref(ev: pd.DataFrame) -> np.ndarray:
    """Reference capacity: concert capacity for concerts, else seats."""
    return np.where(ev["event_category"].eq("concert"), ev["concert_capacity"],
                    ev["seating_capacity"]).astype(float)


# ------------------------------------------------------------- attendance
ATT_FEATURES = None  # filled lazily


def attendance_frame(ev: pd.DataFrame) -> pd.DataFrame:
    cr = cap_ref(ev)
    f = pd.DataFrame(index=ev.index)
    f["exp_ratio"] = ev["expected_attendance"] / cr
    f["low_ratio"] = ev["attendance_estimate_low"] / cr
    f["high_ratio"] = ev["attendance_estimate_high"] / cr
    f["est_conf"] = ev["estimate_confidence"]
    f["sell_through"] = ev["ticket_sell_through_pct"] / 100.0
    f["log_price"] = np.log1p(ev["avg_ticket_price_usd"].fillna(0))
    f["cap_limit"] = ev["capacity_limit_pct"].fillna(100) / 100.0
    # dictionary: home 1.0, away 0.33, neutral 0.85 (used when the signal row is missing)
    f["home_mult"] = ev["home_crowd_multiplier"].fillna(ev["anchor_team_home_away"].map(HOME_MULT_DEFAULT))
    f["log_capacity"] = np.log(cr)
    for c in CATEGORIES:
        f[f"cat_{c}"] = ev["event_category"].eq(c).astype(float)
    for h in HOME_AWAY:
        f[f"ha_{h}"] = ev["anchor_team_home_away"].eq(h).astype(float)
    for s in SUBTYPE_FLAGS:
        f[f"sub_{s.replace(' ', '_')}"] = ev["event_subtype"].eq(s).astype(float)
    for s in ["ticketing_system", "organizer_feed", "public_listing"]:
        f[f"src_{s}"] = ev["estimate_source"].eq(s).astype(float)
    f["social"] = ev["social_interest_index"] / 100.0
    f["search"] = ev["search_trend_index"] / 100.0
    f["resale"] = ev["ticket_resale_price_index"]
    f["win_pct"] = ev["anchor_team_win_pct_to_date"]
    f["standing"] = ev["standings_position"]
    f["transit_adv"] = ev["transit_advisory"].astype(float)
    f["road_closure"] = ev["road_closure_nearby"].astype(float)
    f["competing"] = ev["competing_events_within_10mi"]
    f["school_hol"] = ev["local_school_holiday"].astype(float)
    f["rivalry"] = ev["rivalry_flag"].astype(float)
    f["holiday"] = ev["holiday_flag"].astype(float)
    f["primetime"] = ev["primetime_flag"].astype(float)
    f["temp"] = ev["weather_temp_f"]
    f["precip_open"] = ev["weather_precip_in"].fillna(0) * ev["roof_type"].eq("open")
    f["transit_lines"] = ev["transit_lines_nearby"]
    f["year"] = ev["year"] - 2016
    f["covid"] = ev["year"].isin([2020, 2021]).astype(float)
    f["exp_x_home"] = f["exp_ratio"] * f["home_mult"]
    f["exp_x_conf"] = f["exp_ratio"] * f["est_conf"]
    return f.astype(float)


KNN_COLS = ["exp_ratio", "home_mult", "sell_through", "social", "cap_limit",
            "cat_football", "cat_concert", "cat_rally", "cat_other",
            "ha_home", "ha_away", "ha_neutral", "log_capacity", "year"]
KNN_WEIGHTS = np.array([3.0, 2.0, 1.0, 0.7, 1.5, 2, 2, 2, 2, 2, 2, 2, 0.8, 0.3])


# ------------------------------------------------------------- peak uplink
def cell_static() -> pd.DataFrame:
    cells = dl.small("cells").copy()
    zones = dl.small("zones")[["zone_id", "zone_capacity_people"]]
    cells = cells.merge(zones, on="zone_id", how="left")
    vz = dl.small("zones").groupby("venue_id")["zone_capacity_people"].sum().rename("venue_zone_people")
    cells = cells.merge(vz, on="venue_id", how="left")
    # zones can host several cells: split the zone's people across its cells
    per_zone = cells.groupby("zone_id")["cell_id"].transform("count")
    cells["zone_share"] = cells["zone_capacity_people"] / cells["venue_zone_people"] / per_zone
    cells["band_key"] = cells["band"].str.split(" ").str[0]
    cells["is_5g"] = cells["technology"].str.contains("5G").astype(float)
    return cells


def _history_table() -> pd.DataFrame:
    """Running stats of each cell's last 4 completed events in the same segment."""
    ce = dl.hourly()["cell_event"][["event_id", "cell_id", "peak_ul_prb"]]
    ev = dl.events()[["event_id", "event_start", "event_category", "anchor_team_home_away",
                      "actual_attendance", "event_status"]]
    h = ce.merge(ev, on="event_id")
    h = h[(h["event_status"] == "completed") & h["actual_attendance"].notna()]
    h["seg"] = h["event_category"] + "|" + h["anchor_team_home_away"]
    h = h.sort_values(["cell_id", "seg", "event_start"])
    g = h.groupby(["cell_id", "seg"], sort=False)
    h["h_log_peak"] = g["peak_ul_prb"].transform(lambda s: np.log(s).rolling(4, min_periods=1).mean())
    h["h_log_att"] = g["actual_attendance"].transform(lambda s: np.log(s.clip(lower=1)).rolling(4, min_periods=1).mean())
    h["h_year"] = h["event_start"].dt.year
    return h[["cell_id", "seg", "event_start", "h_log_peak", "h_log_att", "h_year"]]


_HIST = None


def cell_history(pairs: pd.DataFrame, e: pd.DataFrame) -> pd.DataFrame:
    """Causal similar-event feature: stats of the cell's previous events (strictly earlier)."""
    global _HIST
    if _HIST is None:
        _HIST = _history_table().sort_values("event_start")
    q = pd.DataFrame({"cell_id": pairs["cell_id"].to_numpy(),
                      "seg": (e["event_category"] + "|" + e["anchor_team_home_away"]).to_numpy(),
                      "event_start": e["event_start"].to_numpy(), "_i": np.arange(len(pairs))})
    q = q.sort_values("event_start")
    m = pd.merge_asof(q, _HIST, on="event_start", by=["cell_id", "seg"], direction="backward",
                      allow_exact_matches=False)
    return m.sort_values("_i").reset_index(drop=True)


def peak_frame(pairs: pd.DataFrame, attendance: np.ndarray, ev: pd.DataFrame,
               cell_factor: dict, baseline: pd.DataFrame) -> pd.DataFrame:
    """pairs: DataFrame(event_id, cell_id) aligned with `attendance` (people)."""
    cs = cell_static().set_index("cell_id")
    e = ev.set_index("event_id").loc[pairs["event_id"].to_numpy()]
    c = cs.loc[pairs["cell_id"].to_numpy()]
    ul_cap, prb = dl.cell_capacity_at(pairs["cell_id"].to_numpy(), e["event_start"].to_numpy())
    ym = e["event_start"].dt.strftime("%Y-%m").to_numpy()
    bl = baseline.set_index(["cell_id", "ym"])["base_peak_prb"]
    key = pd.MultiIndex.from_arrays([pairs["cell_id"].to_numpy(), ym])
    base = bl.reindex(key).to_numpy(dtype=float)
    base = np.where(np.isfinite(base), base, np.nanmedian(bl.to_numpy()))
    att = np.asarray(attendance, float)
    people = att * c["zone_share"].to_numpy()
    year = e["year"].to_numpy() - 2016
    f = pd.DataFrame(index=pairs.index)
    load = people * np.exp(0.12 * year) / np.maximum(ul_cap, 1)
    f["log_load"] = np.log1p(load)
    f["load"] = load / 100.0
    f["log_people"] = np.log1p(people)
    f["att_ratio"] = att / cap_ref(e.reset_index())
    f["log_ul_cap"] = np.log(np.maximum(ul_cap, 1))
    f["prb_count"] = prb / 100.0
    f["is_5g"] = c["is_5g"].to_numpy()
    for b in BANDS:
        f[f"band_{b}"] = (c["band_key"].to_numpy() == b).astype(float)
    f["cell_factor"] = pairs["cell_id"].map(cell_factor).fillna(1.0).to_numpy()
    f["base_prb"] = base / 100.0
    for cat in CATEGORIES:
        f[f"cat_{cat}"] = (e["event_category"].to_numpy() == cat).astype(float)
    for h in HOME_AWAY:
        f[f"ha_{h}"] = (e["anchor_team_home_away"].to_numpy() == h).astype(float)
    f["duration"] = e["duration_hours"].to_numpy()
    f["year"] = year
    f["load_x_factor"] = f["log_load"] * f["cell_factor"]
    f["load_sq"] = f["log_load"] ** 2
    hist = cell_history(pairs, e)
    has = hist["h_log_peak"].notna().to_numpy()
    f["has_hist"] = has.astype(float)
    f["hist_log_peak"] = np.where(has, hist["h_log_peak"].to_numpy(float), 0.0)
    f["hist_att_delta"] = np.where(has, np.log(np.maximum(att, 1)) - hist["h_log_att"].to_numpy(float), 0.0)
    f["hist_age"] = np.where(has, e["year"].to_numpy() - hist["h_year"].to_numpy(float), 0.0)
    f["load_cu"] = f["log_load"] ** 3
    f["load_x_factor2"] = f["log_load"] * f["cell_factor"] ** 2
    cid = pairs["cell_id"].to_numpy()
    onehot = pd.DataFrame({f"cell_{cell}": (cid == cell).astype(float) for cell in cs.index},
                          index=f.index)
    return pd.concat([f, onehot], axis=1).astype(float)


# ------------------------------------------------------------- short horizon
PHASES = ["pre-event", "ingress", "in-event (1st half)", "halftime/intermission",
          "in-event (2nd half)", "egress", "post-event"]
BASE_COLS = ["ul_prb_pct", "dl_prb_pct", "rrc_connected", "active_ues", "ul_bytes_mb",
             "ul_tput_p50_mbps", "ul_tput_p10_mbps", "cqi", "sinr_db", "power_headroom_db",
             "handover_attempts", "rach_attempts", "rach_failures"]


def short_horizon_frame(t: pd.DataFrame, prb_thr=85.0, p10_thr=3.0) -> pd.DataFrame:
    """Causal lag/slope features on a 15-s stream sorted by (event, cell, time)."""
    key = (t["event_id"].astype(str) + "|" + t["cell_id"].astype(str)).to_numpy()
    grp = pd.Series(key)
    f = pd.DataFrame(index=t.index)
    prb = t["ul_prb_pct"].astype(float).reset_index(drop=True)
    p10 = t["ul_tput_p10_mbps"].astype(float).reset_index(drop=True)
    rrc = t["rrc_connected"].astype(float).reset_index(drop=True)
    g_prb = prb.groupby(grp); g_p10 = p10.groupby(grp); g_rrc = rrc.groupby(grp)
    f["prb"] = prb.to_numpy() / 100
    f["dl_prb"] = t["dl_prb_pct"].to_numpy(float) / 100
    f["log_rrc"] = np.log1p(rrc.to_numpy())
    f["log_active"] = np.log1p(t["active_ues"].to_numpy(float))
    f["log_ul_mb"] = np.log1p(t["ul_bytes_mb"].to_numpy(float))
    f["log_p50"] = np.log1p(t["ul_tput_p50_mbps"].to_numpy(float))
    f["log_p10"] = np.log1p(p10.to_numpy())
    f["cqi"] = t["cqi"].to_numpy(float)
    f["sinr"] = t["sinr_db"].to_numpy(float)
    f["phr"] = t["power_headroom_db"].to_numpy(float)
    f["ho"] = np.log1p(t["handover_attempts"].to_numpy(float))
    ra = t["rach_attempts"].to_numpy(float)
    f["rach_fail"] = t["rach_failures"].to_numpy(float) / np.maximum(ra, 1)
    f["log_rach"] = np.log1p(ra)
    for L in (1, 2, 4, 8):
        f[f"prb_lag{L}"] = (g_prb.shift(L).fillna(prb) / 100).to_numpy()
    for L in (1, 4, 8, 12):
        f[f"prb_slope{L}"] = ((prb - g_prb.shift(L).fillna(prb)) / L).to_numpy()
        f[f"p10_slope{L}"] = ((np.log1p(p10) - np.log1p(g_p10.shift(L).fillna(p10))) / L).to_numpy()
        f[f"rrc_slope{L}"] = ((np.log1p(rrc) - np.log1p(g_rrc.shift(L).fillna(rrc))) / L).to_numpy()
    f["prb_roll8"] = (g_prb.rolling(8, min_periods=1).mean().reset_index(level=0, drop=True)
                      .sort_index() / 100).to_numpy()
    f["prb_max8"] = (g_prb.rolling(8, min_periods=1).max().reset_index(level=0, drop=True)
                     .sort_index() / 100).to_numpy()
    hot = ((prb > prb_thr) & (p10 < p10_thr)).astype(float)
    f["rule_now"] = hot.to_numpy()
    f["rule_run"] = (hot.groupby(grp).rolling(3, min_periods=1).sum()
                     .reset_index(level=0, drop=True).sort_index()).to_numpy()
    f["near_prb"] = (prb > prb_thr - 10).astype(float).to_numpy()
    f["headroom_to_thr"] = ((prb_thr - prb) / 100).to_numpy()
    f["congested_now"] = t["congestion_flag"].astype(float).to_numpy()
    sfs = t["seconds_from_start"].to_numpy(float)
    f["t_hours"] = sfs / 3600
    phase = t["event_phase"].astype(str).to_numpy()
    for p in PHASES:
        f[f"ph_{p}"] = (phase == p).astype(float)
    return f.astype(np.float32)
