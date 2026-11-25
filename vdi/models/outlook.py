"""Five-minute-step radio load and congestion-bin risk through 30 minutes."""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd

from .. import config, data_loader as dl, features as F
from .core import Scaler, ridge_fit, ridge_predict, logistic_fit, sigmoid, auc, precision_recall, best_f1_threshold

VERSION = 2
HORIZONS = (300, 600, 900, 1200, 1500, 1800)


def ordered(frame):
    return frame.sort_values(["event_id", "cell_id", "timestamp"]).reset_index(drop=True)


def design(frame):
    features = F.short_horizon_frame(frame)
    hours = frame["seconds_from_start"].to_numpy(float) / 3600
    for knot in (-2, -1.5, -1, -.5, 0, .5, 1, 1.5, 2, 2.5):
        features[f"time_hinge_{knot}"] = np.maximum(hours - knot, 0)
    for power in (1, 2, 3):
        features[f"time_power_{power}"] = hours ** power
        features[f"load_time_{power}"] = hours ** power * features["prb"]
    return features


def targets(frame, horizon):
    group = frame.groupby(["event_id", "cell_id"], observed=True, sort=False)
    steps = horizon // 15
    future = group["ul_prb_pct"].shift(-steps)
    future_time = pd.to_datetime(group["timestamp"].shift(-steps))
    elapsed = (future_time - pd.to_datetime(frame["timestamp"])).dt.total_seconds()
    return future.where(elapsed == horizon).to_numpy(float)

def risk_targets(frame, horizon):
    keys = [frame["event_id"], frame["cell_id"]]
    rolling = frame["congestion_flag"].astype(float).groupby(keys, observed=True).rolling(20, min_periods=20).max()
    rolling = rolling.reset_index(level=[0, 1], drop=True).sort_index()
    future = rolling.groupby(keys, observed=True).shift(-horizon // 15).to_numpy(float)
    return np.where(np.isfinite(targets(frame, horizon)), future, np.nan)


class OutlookModel:
    def __init__(self, metadata):
        self.meta = metadata
        self.scaler = Scaler(**metadata["scaler"])

    def predict(self, frame):
        x = design(frame)[self.meta["columns"]].to_numpy(float)
        z = self.scaler.transform(x)
        current = frame["ul_prb_pct"].to_numpy(float)
        return {int(h): np.clip(current + ridge_predict(z, np.array(record["beta"])), 0, 100)
                for h, record in self.meta["horizons"].items()}

    def predict_risk(self, frame):
        z = self.scaler.transform(design(frame)[self.meta["columns"]].to_numpy(float))
        return {int(h): sigmoid(z @ np.array(record["risk_coef"]) + record["risk_intercept"])
                for h, record in self.meta["horizons"].items()}

    @classmethod
    def train(cls, frame):
        frame = ordered(frame)
        first = frame.groupby("event_id", observed=True)["timestamp"].min().sort_values()
        ids = first.index.astype(str).tolist()
        if len(ids) < 10:
            raise ValueError("At least ten historical events are required for outlook evaluation.")
        ntrain, ncal = int(len(ids) * .6), int(len(ids) * .2)
        groups = [ids[:ntrain], ids[ntrain:ntrain + ncal], ids[ntrain + ncal:]]
        event_ids = frame["event_id"].astype(str)
        masks = [event_ids.isin(group).to_numpy() for group in groups]
        x = design(frame)
        scaler = Scaler().fit(x.to_numpy(float)[masks[0]])
        z = scaler.transform(x.to_numpy(float))
        current = frame["ul_prb_pct"].to_numpy(float)
        metadata = {"version": VERSION, "columns": list(x.columns), "scaler": scaler.to_dict(),
                    "split": dict(zip(("train", "calibration", "test"), groups)),
                    "method": "Direct ridge load forecast; causal telemetry and event-relative time",
                    "interval": "90% residual range from separate calibration events",
                    "horizons": {}}
        for horizon in HORIZONS:
            y = targets(frame, horizon)
            train, calibration, test = [mask & np.isfinite(y) for mask in masks]
            if min(train.sum(), calibration.sum(), test.sum()) == 0:
                raise ValueError(f"No valid samples for the {horizon}-second outlook.")
            beta = ridge_fit(z[train], y[train] - current[train], alpha=100)
            predictions = np.clip(current + ridge_predict(z, beta), 0, 100)
            lower, upper = np.quantile(y[calibration] - predictions[calibration], [.05, .95])
            error = predictions[test] - y[test]
            baseline = float(np.abs(current[test] - y[test]).mean())
            mae = float(np.abs(error).mean())
            coverage = np.mean((y[test] >= np.clip(predictions[test] + lower, 0, 100))
                               & (y[test] <= np.clip(predictions[test] + upper, 0, 100)))
            yr = risk_targets(frame, horizon)
            rtrain, rcal, rtest = [mask & np.isfinite(yr) for mask in masks]
            weight, intercept = logistic_fit(z[rtrain], yr[rtrain], iters=100, lr=.05, l2=.001, pos_weight=1.)
            probability = sigmoid(z @ weight + intercept)
            threshold = best_f1_threshold(yr[rcal], probability[rcal])
            precision, recall, f1 = precision_recall(yr[rtest], probability[rtest], threshold)
            metadata["horizons"][str(horizon)] = {
                "beta": beta.tolist(), "lower_residual": float(lower), "upper_residual": float(upper),
                "risk_coef": weight.tolist(), "risk_intercept": float(intercept),
                "risk_threshold": threshold,
                "test": {"n": int(test.sum()), "mae": round(mae, 2),
                         "bias": round(float(error.mean()), 2), "persistence_mae": round(baseline, 2),
                         "skill_pct": round(100 * (1 - mae / baseline), 1) if baseline else None,
                         "coverage_pct": round(100 * float(coverage), 1),
                         "congestion_risk": {"n": int(rtest.sum()), "auc": round(auc(yr[rtest], probability[rtest]), 4),
                                             "precision": round(precision, 3), "recall": round(recall, 3),
                                             "f1": round(f1, 3), "brier": round(float(np.mean((probability[rtest] - yr[rtest]) ** 2)), 4),
                                             "threshold": threshold}}}
        return cls(metadata)

    @classmethod
    def load_or_train(cls):
        source = dl.path_of("telemetry")
        stat = source.stat()
        fingerprint = hashlib.sha256(f"{VERSION}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()
        path = config.artifacts_dir() / "outlook.json"
        if path.exists():
            metadata = json.loads(path.read_text())
            if metadata.get("fingerprint") == fingerprint:
                return cls(metadata)
        model = cls.train(dl.telemetry())
        model.meta["fingerprint"] = fingerprint
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(model.meta, indent=2))
        temp.replace(path)
        return model

    def report(self):
        return {key: value for key, value in self.meta.items() if key in
                ("version", "split", "method", "interval")} | {
                    "horizons": {h: record["test"] for h, record in self.meta["horizons"].items()},
                    "risk_definition": "Probability of the supplied synthetic congestion flag in each future 5-minute bin; thresholds need network calibration",
                    "limitations": ["Risk is not an independently evaluated hardware/interference-outage forecast",
                                    "Future user throughput and traffic figures are scenario estimates, not evaluated model heads"],
                    "source": "Synthetic historical telemetry; no real-world performance claim"}
