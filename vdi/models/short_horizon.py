"""Short-horizon congestion risk (60 / 120 / 180 s ahead) on 15-s telemetry.

One logistic regression per horizon (numpy, Adam gradient descent) on causal
lag / slope / rolling features.  Time-based split by event: the earliest
~75 % of target-venue events train, the latest events test.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .. import config
from .. import features as F
from .core import Scaler, auc, best_f1_threshold, logistic_fit, precision_recall, sigmoid

HORIZONS = (60, 120, 180)


class ShortHorizonModel:
    name = "short_horizon"

    def __init__(self):
        self.cols: list[str] = []
        self.scaler = Scaler()
        self.coef: dict[int, list[float]] = {}
        self.intercept: dict[int, float] = {}
        self.threshold: dict[int, float] = {}

    def features(self, t: pd.DataFrame) -> np.ndarray:
        th = config.thresholds()
        f = F.short_horizon_frame(t, th["ul_prb_pct_gt"], th["ul_p10_mbps_lt"])
        if not self.cols:
            self.cols = list(f.columns)
        return f[self.cols].to_numpy(np.float32)

    def fit(self, t: pd.DataFrame, X: np.ndarray | None = None):
        X = self.features(t) if X is None else X
        Z = self.scaler.fit(X).transform(X).astype(np.float32)
        mc = config.model_cfg()
        for h in HORIZONS:
            y = t[f"label_congestion_next_{h}s"].to_numpy().astype(float)
            w, b = logistic_fit(Z, y, iters=mc["logistic_iters"], lr=mc["logistic_lr"],
                                l2=mc["logistic_l2"], pos_weight=3.0)
            self.coef[h] = w.tolist(); self.intercept[h] = float(b)
            self.threshold[h] = best_f1_threshold(y, sigmoid(Z @ w + b))
        return self

    def predict_proba(self, t: pd.DataFrame | None = None, X: np.ndarray | None = None) -> dict:
        X = self.features(t) if X is None else X
        Z = self.scaler.transform(X)
        return {h: sigmoid(Z @ np.array(self.coef[h]) + self.intercept[h]) for h in HORIZONS}

    def evaluate(self, t: pd.DataFrame, X: np.ndarray | None = None) -> dict:
        P = self.predict_proba(t, X)
        out = {}
        for h in HORIZONS:
            y = t[f"label_congestion_next_{h}s"].to_numpy().astype(bool)
            pr, rc, f1 = precision_recall(y, P[h], self.threshold[h])
            # onset rows: not congested now but congested within horizon
            onset = ~t["congestion_flag"].to_numpy().astype(bool)
            out[f"{h}s"] = {"auc": round(auc(y, P[h]), 4), "precision": round(pr, 3),
                            "recall": round(rc, 3), "f1": round(f1, 3),
                            "threshold": self.threshold[h], "positive_rate": round(float(y.mean()), 4),
                            "auc_not_yet_congested": round(auc(y[onset], P[h][onset]), 4)}
        return out

    def top_drivers(self, h=60, n=8):
        w = np.array(self.coef[h])
        order = np.argsort(-np.abs(w))[:n]
        return [{"feature": self.cols[i], "weight": round(float(w[i]), 3)} for i in order]

    def save(self, d):
        meta = {"cols": self.cols, "scaler": self.scaler.to_dict(),
                "coef": {str(k): v for k, v in self.coef.items()},
                "intercept": {str(k): v for k, v in self.intercept.items()},
                "threshold": {str(k): v for k, v in self.threshold.items()}}
        (d / "short_horizon.json").write_text(json.dumps(meta, indent=1))

    @classmethod
    def load(cls, d):
        m = cls()
        meta = json.loads((d / "short_horizon.json").read_text())
        m.cols = meta["cols"]; m.scaler = Scaler(**meta["scaler"])
        m.coef = {int(k): v for k, v in meta["coef"].items()}
        m.intercept = {int(k): v for k, v in meta["intercept"].items()}
        m.threshold = {int(k): v for k, v in meta["threshold"].items()}
        return m


def split_events(t: pd.DataFrame, train_frac=0.75):
    first = t.groupby("event_id", observed=True)["timestamp"].min().sort_values()
    n_tr = int(round(len(first) * train_frac))
    tr_ids = set(first.index[:n_tr]); te_ids = set(first.index[n_tr:])
    ev = t["event_id"].astype(str)
    return ev.isin(tr_ids).to_numpy(), ev.isin(te_ids).to_numpy(), sorted(tr_ids), sorted(te_ids)
