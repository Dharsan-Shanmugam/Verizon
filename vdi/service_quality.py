"""Reproducible synthetic RTT/loss scenarios, not measured customer experience."""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache

import numpy as np
import pandas as pd

from . import config, data_loader as dl

VERSION = 2
LATENCY_LIMIT_MS = 50
LOSS_LIMIT_PCT = 1
SCHEMA = {
    "timestamp": ("datetime", "15-second sample timestamp"),
    "event_id": ("string", "Recorded synthetic event"),
    "cell_id": ("string", "Radio cell"),
    "latency_ms": ("float", "Modeled synthetic round-trip latency; not a measured SLA"),
    "packet_loss_pct": ("float", "Modeled synthetic end-to-end packet loss percentage"),
    "radio_queue_ms": ("float", "Modeled radio scheduling / queue delay"),
    "transport_latency_ms": ("float", "Maximum site/shared latency from original synthetic file 25"),
    "transport_packet_loss_pct": ("float", "Combined site/shared loss from original synthetic file 25"),
    "transport_jitter_ms": ("float", "Synthetic jitter proxy: trailing absolute transport-latency variation; not measured packet jitter"),
    "tdd_ul_share_pct": ("float", "Synthetic n77 uplink slot allocation; zero for FDD"),
    "tdd_ul_demand_pct": ("float", "Synthetic requested uplink slot share; zero for FDD"),
    "tdd_alignment_ok": ("bool", "Synthetic neighbor slot-pattern safety check"),
    "interference_flag": ("bool", "Explicit synthetic RF impairment scenario"),
    "pusch_sinr_db": ("float", "Synthetic PUSCH SINR scenario; not a measured vendor counter"),
    "ul_bler_pct": ("float", "Synthetic uplink transport-block error percentage"),
    "harq_retrans_pct": ("float", "Synthetic HARQ retransmission percentage"),
    "rx_interference_dbm": ("float", "Synthetic receiver interference/noise scenario"),
    "handover_success_pct": ("float", "Original synthetic mobility KPI adjusted for explicit RF scenarios"),
    "cell_available": ("bool", "Synthetic administrative availability indicator"),
    "alarm": ("string", "Explicit synthetic alarm; NONE means no alarm in this scenario"),
    "compute_util_pct": ("float", "Synthetic baseband/compute resource utilization"),
    "coverage_impairment_flag": ("bool", "Explicit synthetic coverage impairment scenario"),
    "aggregate_ul_mbps": ("float", "Aggregate UL bytes in file 15 converted to Mbps over 15 seconds"),
    "user_ul_p05_mbps": ("float", "Synthetic lognormal P05 inferred from source P50/P10; not measured P05"),
    "data_source": ("string", "synthetic_service_scenario_v2"),
}


def estimate(prb, transport_latency=8., transport_loss=.02, tdd_gap=0., interference=False, processing_delay=0.):
    """Illustrative scenario response, deliberately separate from trained load models."""
    load = np.asarray(prb, float)
    queue = .75 * np.maximum(load - 60, 0) + .65 * np.maximum(tdd_gap, 0)
    latency = 12 + queue + np.asarray(transport_latency) + 35 * np.asarray(interference, float) + processing_delay
    radio_loss = .03 + .055 * np.maximum(load - 70, 0) + .035 * np.maximum(tdd_gap, 0)
    radio_loss += 1.5 * np.asarray(interference, float)
    radio_loss += 1.0 * (np.asarray(processing_delay) > 20)
    loss = 100 * (1 - (1 - np.clip(radio_loss, 0, 100) / 100)
                  * (1 - np.clip(transport_loss, 0, 100) / 100))
    return {"latency_ms": np.round(latency, 2), "packet_loss_pct": np.round(loss, 3),
            "radio_queue_ms": np.round(queue, 2)}

def modeled_p05(p50, p10):
    median = np.maximum(np.asarray(p50, float), .0001)
    lower = np.minimum(median, np.maximum(np.asarray(p10, float), .0001))
    sigma = np.log(median / lower) / 1.28155
    return median * np.exp(-1.64485 * sigma)


@lru_cache(maxsize=1)
def user_throughput_reference():
    frame = dl.telemetry()
    bins = (frame["ul_prb_pct"] / 5).round() * 5
    return frame.groupby(bins, observed=True)[["ul_tput_p50_mbps", "ul_tput_p10_mbps"]].median().sort_index()


def ensure_live_data(live, replay):
    sources = [dl.path_of(key) for key in ("live", "bh_15s", "cells")]
    signature = hashlib.sha256(json.dumps([VERSION, list(SCHEMA), *[
        (p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in sources]]).encode()).hexdigest()
    path = dl.path_of("service_quality")
    meta = config.artifacts_dir() / "service_quality_source.json"
    if path.exists() and meta.exists() and json.loads(meta.read_text()).get("signature") == signature:
        return pd.read_csv(path)
    transport = dl.backhaul()["live"].copy()
    transport["seconds"] = (pd.to_datetime(transport["timestamp"]) - pd.Timestamp(live["event_start"])).dt.total_seconds()
    agg = next(link for link in live["backhaul"]["links"] if link.endswith("-AGG"))
    times = np.array(live["times"])
    raw = dl.live_stream().sort_values(["cell_id", "timestamp"])
    frames = []
    for cell in live["cells"]:
        site = replay.cells.loc[cell, "site_id"]
        rows = [transport[transport["link_id"] == link].set_index("seconds").reindex(times)
                for link in (replay.site_links[site], agg)]
        lat = np.maximum(rows[0]["latency_ms"].to_numpy(), rows[1]["latency_ms"].to_numpy())
        loss = 100 * (1 - (1 - rows[0]["packet_loss_pct"].to_numpy() / 100)
                      * (1 - rows[1]["packet_loss_pct"].to_numpy() / 100))
        if not np.isfinite(lat).all() or not np.isfinite(loss).all():
            raise ValueError(f"Missing transport quality data for {cell}.")
        n77 = str(replay.cells.loc[cell, "band"]).startswith("n77")
        imbalance = n77 & (times >= -1800) & (times <= -600)
        share = np.where(imbalance, 20., 40.) if n77 else np.zeros(len(times))
        demand = np.where(imbalance, 60., 40.) if n77 else np.zeros(len(times))
        interference = (cell.endswith("C01")) & (times >= -300) & (times <= 300)
        coverage = cell.endswith("C08") & (times >= 1200) & (times <= 1800)
        compute_fault = cell.endswith("C05") & (times >= 600) & (times <= 1200)
        source = raw[raw["cell_id"].astype(str) == cell].reset_index(drop=True)
        if len(source) != len(times):
            raise ValueError(f"Missing raw diagnostic samples for {cell}.")
        sinr = np.clip(source["sinr_db"].to_numpy(float) - 20 * interference - 18 * coverage, -15, 35)
        bler = np.clip(2 + 1.1 * np.maximum(12 - sinr, 0), .2, 60)
        compute = np.clip(20 + .4 * np.array(live["series"][cell]["prb"]) + 65 * compute_fault, 0, 99)
        quality = estimate(live["series"][cell]["prb"], lat, loss, demand - share, interference | coverage,
                           np.where(compute >= 90, 30., 0.))
        frames.append(pd.DataFrame({
            "timestamp": (pd.Timestamp(live["event_start"]) + pd.to_timedelta(times, unit="s")).astype(str),
            "event_id": live["event_id"], "cell_id": cell, **quality,
            "transport_latency_ms": lat, "transport_packet_loss_pct": np.round(loss, 4),
            "transport_jitter_ms": pd.Series(lat).diff().abs().fillna(0).rolling(4, min_periods=1).mean().round(3).to_numpy(),
            "tdd_ul_share_pct": share, "tdd_ul_demand_pct": demand,
            "tdd_alignment_ok": ~((times >= -1050) & (times <= -600) & n77),
            "interference_flag": interference, "coverage_impairment_flag": coverage,
            "pusch_sinr_db": sinr, "ul_bler_pct": np.round(bler, 2), "harq_retrans_pct": np.round(bler * 1.4, 2),
            "rx_interference_dbm": np.where(interference, -82., -108.),
            "handover_success_pct": np.clip(source["handover_success_pct"].to_numpy(float) - 25 * interference - 15 * coverage, 0, 100),
            "cell_available": True, "alarm": np.where(compute >= 90, "BASEBAND_COMPUTE_HIGH",
                                                       np.where(interference, "UL_INTERFERENCE",
                                                                np.where(coverage, "COVERAGE_DEGRADATION", "NONE"))),
            "compute_util_pct": compute,
            "aggregate_ul_mbps": np.round(source["ul_bytes_mb"].to_numpy(float) * 8 / 15, 2),
            "user_ul_p05_mbps": np.round(modeled_p05(source["ul_tput_p50_mbps"], source["ul_tput_p10_mbps"]), 4),
            "data_source": "synthetic_service_scenario_v2"}))
    frame = pd.concat(frames, ignore_index=True)
    if frame[list(SCHEMA)[3:8]].isna().any().any():
        raise ValueError("Synthetic service metrics contain missing values.")
    temp = path.with_suffix(".tmp")
    frame.to_csv(temp, index=False)
    temp.replace(path)
    meta.write_text(json.dumps({"signature": signature, "version": VERSION,
                               "rows": len(frame), "source": "Explicit synthetic service scenarios"}))
    return frame


def readiness_checks(event, cells, neighbors, site_util, shared_util, tdd_cells):
    """Prioritize advisory preparation using this event's forecast, not live authority."""
    if not cells or not np.isfinite([site_util, shared_util, *[c["peak_prb"] for c in cells]]).all():
        raise ValueError("Readiness planning needs finite cell and transport forecasts.")
    by_cell = {c["cell_id"]: c for c in cells}
    peak = max(cells, key=lambda c: c["peak_prb"])
    pressure = [c["cell_id"] for c in cells if c["peak_prb"] >= 70]
    hot = [c["cell_id"] for c in cells if c["peak_prb"] >= 85]
    potential = [(n["cell_id"], n["neighbor_cell_id"]) for n in neighbors
                 if n["steering_eligibility"] == "eligible" and n["cell_id"] in pressure
                 and n["neighbor_cell_id"] in by_cell
                 and n["cell_id"] != n["neighbor_cell_id"]
                 and by_cell[n["neighbor_cell_id"]]["peak_prb"] < 85]
    venue = event["venue_name"]
    radio_status = "priority" if hot else "review" if pressure else "baseline"
    steering_status = "review" if pressure and potential else "blocked" if pressure else "not_triggered"
    transport_status = "priority" if site_util > 85 or shared_util > 90 else (
        "review" if site_util >= 75 or shared_util >= 80 else "baseline")
    unsupported_hot = [cell for cell in hot if not any(source == cell for source, _ in potential)]
    capacity_review = bool(unsupported_hot)
    rows = [
        {"id": "radio_capacity", "title": "Validate cell / sector-carrier capacity", "status": radio_status,
         "evidence": f"{venue}: {peak['cell_id']} peaks at {peak['peak_prb']:.1f}% PRB; {len(hot)} cells reach the 85% planning line.",
         "when": f"Before {event['start']}; prioritize {len(pressure)} cells at or above 70% PRB.",
         "action": "Compare these predicted peaks and uncertainty bands with dated radio-capacity and configuration snapshots."},
        {"id": "steering_policy", "title": "Prepare temporary steering policy", "status": steering_status,
         "evidence": f"{len(pressure)} pressured cells; {len(potential)} eligible configured relationships have a target forecast below 85% PRB. Not a live safety clearance.",
         "when": "Before the forecast pressure window." if pressure else "No radio pressure trigger in this event forecast.",
         "action": ("Review " + ", ".join(f"{source} to {target}" for source, target in potential) + ". "
                    if potential else "No forecast-supported target in this venue's configured relationships. " if pressure else "")
                   + "Verify post-move headroom, coverage, handovers and transport before preparing any approved steering profile."},
        {"id": "tdd_allocation", "title": "Review TDD uplink allocation",
         "status": "review" if tdd_cells else "not_triggered",
         "evidence": f"{len(tdd_cells)} n77 cells forecast at or above 65% PRB"
                     + (": " + ", ".join(tdd_cells) if tdd_cells else "; no busy n77 trigger") + ". Slot shortage is not established by PRB alone.",
         "when": "Before the busy n77 window." if tdd_cells else "Only if new evidence shows uplink slot pressure.",
         "action": "Check actual uplink demand, coordinated neighbor slot patterns and downlink headroom. TDD changes remain engineer-reviewed."},
        {"id": "mobility", "title": "Validate mobility relationships", "status": steering_status,
         "evidence": f"{venue}: {len(potential)} potential source/target relationships for the forecast pressure cells; live quality and handover checks remain required.",
         "when": "Before any proposed traffic redirection." if pressure else "No steering preparation triggered by this forecast.",
         "action": "Verify neighbor eligibility, target availability, radio quality and handover success; do not treat forecast headroom as authorization."},
        {"id": "transport", "title": "Review transport headroom", "status": transport_status,
         "evidence": f"{venue}: modeled site utilization {site_util:.1f}% / shared {shared_util:.1f}%; policy limits 85% / 90%.",
         "when": "Before the event; transport is forecast near or above the planning limits."
                 if transport_status != "baseline" else "Capture the event transport baseline and preserve spare capacity.",
         "action": "Validate backhaul queues, protected traffic and prepared alternate paths. Fronthaul/midhaul counters and executable failover adapters are not supplied."},
        {"id": "physical_capacity", "title": "Pre-stage physical event capacity",
         "status": "review" if capacity_review else "not_triggered",
         "evidence": f"{len(hot)} cells reach 85% PRB; {len(potential)} potential below-limit target relationships."
                     + (" No forecast-supported target for " + ", ".join(unsupported_hot) + "."
                        if capacity_review else " Physical capacity need is not established by this screening."),
         "when": "Review field lead times before the event." if capacity_review else "If validated software options cannot cover the event demand.",
         "action": "Engineering must assess radio, spectrum, power and transport options. Forecast screening does not prove that software remedies will suffice."},
        {"id": "commissioned_resources", "title": "Verify commissioned resource availability",
         "status": "review" if hot else "not_triggered",
         "evidence": f"{venue}: {len(hot)} cells reach the planning load line. No inactive commissioned layer or activation adapter is recorded in this simulation.",
         "when": "Check inventory as part of capacity preparation." if hot else "If the revised forecast needs extra capacity.",
         "action": "Confirm event-specific inventory, activation support and licensing; do not assume a resource exists or can be activated."},
        {"id": "guardrails", "title": "Establish operating guardrails", "status": "baseline",
         "evidence": f"{venue} / {event['start']} / {len(cells)} forecast cells. This checklist is advisory only.",
         "when": "Before any live intervention, regardless of the forecast.",
         "action": "Define approved scope, change window, expiry, validation, rollback, audit and human override. Live policy still decides every action."}
    ]
    order = {"priority": 0, "blocked": 1, "review": 2, "baseline": 3, "not_triggered": 4}
    return sorted(rows, key=lambda row: order[row["status"]])


def recommendations(state, event_id):
    event = state.event_detail(event_id)
    device_rows = dl.events().loc[lambda frame: frame["event_id"] == event_id, "connected_devices_est"]
    if len(device_rows) != 1 or not np.isfinite(device_rows.iloc[0]) or device_rows.iloc[0] < 0:
        raise ValueError("Planning needs a valid connected-device estimate for the selected event.")
    connected_devices = float(device_rows.iloc[0])
    if not connected_devices.is_integer():
        raise ValueError("The connected-device estimate must be a whole-device count.")
    simulation = state.sim.simulate(event_id=event_id)
    before = simulation["kpis"]["before"]
    site, agg = before["site_bh_max"], before["agg_bh"]
    prb = event["peak_prb_max"]
    # These are scenario assumptions, not a second evaluated forecasting model.
    transport_latency = 8 + .7 * max(max(site, agg) - 60, 0)
    transport_loss = .02 + .06 * max(max(site, agg) - 80, 0)
    quality = {key: float(value) for key, value in estimate(prb, transport_latency, transport_loss).items()}
    cards = []
    if prb >= 70:
        cards.append({
            "id": "radio_headroom", "priority": 1 if prb >= 85 else 2, "issue": "Uplink PRB utilization pressure",
            "evidence": f"Predicted peak uplink PRB {prb:.0f}% / {100 - prb:.0f}% radio headroom / {event['hot_cells']} high-load cells",
            "impact": "High uplink PRB utilization can reduce user throughput and increase scheduling delay; overloaded buffers can cause packet loss.",
            "remedy": "Prepare bounded traffic steering to eligible neighboring cells to protect uplink and user throughput. Validate post-move radio and transport headroom; if insufficient, assess temporary uplink capacity.",
            "owner": "Radio planning", "cells": [c["cell_id"] for c in event["cells"] if c["pred"] >= 70]})
    if site >= 75 or agg >= 80:
        cards.append({
            "id": "backhaul_capacity", "priority": 1 if site > 85 or agg > 90 else 2,
            "issue": "Backhaul capacity pressure",
            "evidence": f"Modeled site link {site:.0f}% / shared link {agg:.0f}%",
            "impact": "Backhaul constraints can limit uplink throughput, increase transport delay and cause packet loss.",
            "remedy": "Validate alternate-path capacity and rebalance traffic; otherwise plan a bandwidth upgrade. Review QoS without harming protected traffic.",
            "owner": "Transport engineering", "cells": []})
    tdd_cells = [c["cell_id"] for c in event["cells"]
                 if str(state.replay.cells.loc[c["cell_id"], "band"]).startswith("n77") and c["pred"] >= 65]
    if tdd_cells:
        cards.append({
            "id": "tdd_slots", "priority": 2, "issue": "TDD uplink slot allocation readiness",
            "evidence": f"{len(tdd_cells)} n77 cells forecast at or above 65% uplink PRB utilization; slot shortage is not established by load alone",
            "impact": "Insufficient uplink time slots can constrain uplink and user throughput and increase scheduling delay or packet loss.",
            "remedy": "Compare uplink demand with allocated TDD time slots. Prepare any uplink-heavy profile for engineer review only after neighboring-cell alignment and downlink-headroom checks.",
            "owner": "Radio optimization", "cells": tdd_cells})
    if not cards:
        cards.append({
            "id": "baseline", "priority": 3, "issue": "Uplink service baseline readiness",
            "evidence": f"No major capacity pressure predicted: peak uplink PRB {prb:.0f}% / site {site:.0f}% / shared {agg:.0f}%",
            "impact": "Preserve uplink and user throughput while monitoring latency and packet loss against policy budgets.",
            "remedy": "Capture baseline uplink throughput, user throughput, PRB utilization, RTT and packet loss; verify queue policies and preserve capacity headroom.",
            "owner": "Service assurance", "cells": []})
    cards.sort(key=lambda c: (c["priority"], c["id"]))
    offsets = np.arange(-21600, 21600 + 1, 300)
    reference = user_throughput_reference()
    ids = [c["cell_id"] for c in event["cells"]]
    capacities, _ = dl.cell_capacity_at(ids, [event["start"]] * len(ids))
    if not np.isfinite(capacities).all():
        raise ValueError("Planning needs a dated capacity snapshot for every cell.")
    severe = state.sim.simulate(event_id=event_id, attendance_pct=130)
    severe_peaks = {row["cell_id"]: row["before"] for row in severe["cells"]}
    profiles, cell_outputs = [], []
    for cell, capacity in zip(event["cells"], capacities):
        row = next(r for r in event["heatmap"]["rows"] if r["cell_id"] == cell["cell_id"])
        load = np.interp(offsets / 3600, event["heatmap"]["hour_rel"], row["values"])
        risk = np.interp(offsets / 3600, event["heatmap"]["hour_rel"], row["risk"])
        busy = np.flatnonzero(load >= 85)
        p50 = float(np.interp(cell["pred"], reference.index.to_numpy(float), reference["ul_tput_p50_mbps"]))
        p10 = float(np.interp(cell["pred"], reference.index.to_numpy(float), reference["ul_tput_p10_mbps"]))
        meta = state.replay.cells.loc[cell["cell_id"]]
        p50_curve = np.interp(load, reference.index.to_numpy(float), reference["ul_tput_p50_mbps"])
        p10_curve = np.interp(load, reference.index.to_numpy(float), reference["ul_tput_p10_mbps"])
        profiles.append({"cell_id": cell["cell_id"], "prb": np.round(load, 1).tolist(),
                         "risk": np.round(risk, 4).tolist(),
                         "aggregate_ul_mbps_scenario": np.round(capacity * load / 100 * .65, 1).tolist(),
                         "user_p50_mbps_scenario": np.round(p50_curve, 3).tolist(),
                         "user_p05_mbps_scenario": np.round(modeled_p05(p50_curve, p10_curve), 4).tolist()})
        cell_outputs.append({
            "cell_id": cell["cell_id"], "sector": str(meta["sector"]), "site_id": str(meta["site_id"]), "band": str(meta["band"]),
            "peak_prb": cell["pred"], "peak_low": cell["low"], "peak_high": cell["high"],
            "peak_risk": cell["p_over_85"], "capacity_mbps": round(float(capacity), 1),
            "aggregate_ul_mbps": round(float(capacity * cell["pred"] / 100 * .65), 1),
            "aggregate_ul_upper_mbps": round(float(capacity * cell["high"] / 100 * .65), 1),
            "severe_ul_mbps": round(float(capacity * severe_peaks[cell["cell_id"]] / 100 * .65), 1),
            "user_p50_mbps": round(p50, 3), "user_p05_mbps": round(float(modeled_p05(p50, p10)), 4),
            "risk_start_s": int(offsets[busy[0]]) if len(busy) else None,
            "peak_s": int(offsets[np.argmax(load)]),
            "recovery_s": int(offsets[busy[-1]] + 300) if len(busy) and busy[-1] < len(offsets) - 1 else None})
    return {"event": event, "recommendations": cards, "service_forecast": quality,
            "connected_devices_est": int(connected_devices),
            "connected_devices_method": "Event-level connected-device estimate from the device-mix dataset. Not a live device count or an independently evaluated device forecast; no per-cell or time-varying device estimates are supplied.",
            "transport_forecast": {"site_util": site, "agg_util": agg},
            "policy": {"latency_limit_ms": LATENCY_LIMIT_MS, "packet_loss_limit_pct": LOSS_LIMIT_PCT},
            "mode": "advisory_only", "approval_required": False,
            "readiness_checks": readiness_checks(event, cell_outputs, state.replay.neighbors.to_dict("records"),
                                                 site, agg, tdd_cells),
            "cell_forecasts": cell_outputs,
            "five_minute_profile": {"seconds": offsets.tolist(), "cells": profiles,
                                    "method": "Five-minute display interpolated from trained hourly event shape; not an independently calibrated 5-minute model"},
            "throughput_method": "Aggregate traffic = 65% of nominal UL capacity scaled by load; user rates use historical load-bin medians and a lognormal P05 assumption. Scenario estimates, not independently evaluated throughput forecasts.",
            "method": "Illustrative service response to trained peak-load and simulated transport estimates; not measured or independently backtested RTT/loss."}
