"""Peak uplink model: per cell x event peak UL PRB % with a prediction interval.

Ridge regression on log(peak PRB) (clipped to 0-100 %), drivers:
predicted attendance x zone share x uplink growth / cell UL capacity, cell
band/technology, non-event baseline, category and home/away.  Interval =
empirical residual quantiles, by predicted-load tercile.  Hourly profiles
by category turn the event peak into a cell x hour curve.
"""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

from .. import config
from .. import features as F
from .core import Scaler, mape, ridge_fit, ridge_predict

def logit(p):
    """Forward transform (log of PRB %); name kept for API stability."""
    return np.log(np.clip(np.asarray(p, float), 1.0, 100.0))


def inv_logit(z):
    return np.clip(np.exp(np.asarray(z, float)), 1.0, 100.0)


class PeakUplinkModel:
    name = "peak_uplink"

    def __init__(self):
        self.cols: list[str] = []
        self.scaler = Scaler()
        self.beta = None
        self.cell_factor: dict[str, float] = {}
        self.bins: list[float] = []
        self.resid_q: list[list[float]] = []
        self.sigma: float = 0.3
        self.profile: dict[str, dict[str, float]] = {}
        self.q = config.model_cfg()["interval_quantiles"]

    def _X(self, pairs, att, ev, baseline):
        f = F.peak_frame(pairs, att, ev, self.cell_factor, baseline)
        if not self.cols:
            self.cols = list(f.columns)
        return f[self.cols].to_numpy(float)

    def fit(self, rows: pd.DataFrame, att: np.ndarray, ev: pd.DataFrame, baseline: pd.DataFrame,
            shape_raw: pd.DataFrame | None = None):
        """rows: cell_event rows (event_id, cell_id, peak_ul_prb) for training events."""
        rows = rows.reset_index(drop=True)
        vm = rows.groupby("event_id")["peak_ul_prb"].transform("mean").clip(lower=1)
        self.cell_factor = (rows["peak_ul_prb"] / vm).groupby(rows["cell_id"]).mean().round(4).to_dict()
        X = self.scaler.fit(self._X(rows, att, ev, baseline)).transform(self._X(rows, att, ev, baseline))
        y = logit(rows["peak_ul_prb"].to_numpy())
        self.beta = ridge_fit(X, y, alpha=config.model_cfg()["peak_ridge_alpha"])
        z = ridge_predict(X, self.beta)
        res = y - z
        self.sigma = float(np.std(res))
        self.bins = [float(b) for b in np.quantile(z, [1 / 3, 2 / 3])]
        b = np.digitize(z, self.bins)
        self.resid_q = [[float(np.quantile(res[b == i], self.q[0])), float(np.quantile(res[b == i], self.q[1]))]
                        for i in range(3)]
        if shape_raw is not None:
            self.profile = self.hourly_profile(shape_raw, ev)
        return self

    @staticmethod
    def hourly_profile(shape_raw, ev):
        s = shape_raw.merge(ev[["event_id", "event_category"]], on="event_id")
        s = s[(s["hour_rel"] >= -6) & (s["hour_rel"] <= 6)]
        prof = s.groupby(["event_category", "hour_rel"])["shape"].mean().clip(0.05, 1.0)
        out = {}
        for (cat, h), v in prof.items():
            out.setdefault(cat, {})[str(int(h))] = round(float(v), 4)
        return out

    def predict(self, pairs, att, ev, baseline) -> pd.DataFrame:
        pairs = pairs.reset_index(drop=True)
        X = self.scaler.transform(self._X(pairs, att, ev, baseline))
        z = ridge_predict(X, self.beta)
        b = np.digitize(z, self.bins)
        lo = np.array([self.resid_q[i][0] for i in b]); hi = np.array([self.resid_q[i][1] for i in b])
        out = pairs[["event_id", "cell_id"]].copy()
        out["peak_pred"] = np.round(inv_logit(z), 2)
        out["peak_low"] = np.round(inv_logit(z + lo), 2)
        out["peak_high"] = np.round(inv_logit(z + hi), 2)
        out["z"] = z
        out["p_over_85"] = np.round(1 - _norm_cdf((logit(85) - z) / max(self.sigma, 1e-3)), 4)
        return out

    def hourly(self, category: str, z: float, hours=range(-6, 7)):
        prof = self.profile.get(category) or next(iter(self.profile.values()))
        peak = float(inv_logit(z))
        rows = []
        for h in hours:
            s = prof.get(str(h), 0.2)
            v = peak * s
            zz = float(logit(v))
            risk = 1 - _norm_cdf((float(logit(85)) - zz) / max(self.sigma, 1e-3))
            rows.append({"hour_rel": h, "prb": round(v, 1), "risk": round(float(risk), 4)})
        return rows

    def evaluate(self, rows, att, ev, baseline) -> dict:
        p = self.predict(rows, att, ev, baseline)
        y = rows["peak_ul_prb"].to_numpy(float)
        inside = (y >= p["peak_low"].to_numpy()) & (y <= p["peak_high"].to_numpy())
        hi = y >= 85
        flag = p["peak_pred"].to_numpy() >= 85
        return {"n": int(len(y)), "mape_pct": round(mape(y, p["peak_pred"]), 2),
                "mae_prb_pts": round(float(np.mean(np.abs(y - p["peak_pred"]))), 2),
                "bias_prb_pts": round(float(np.mean(p["peak_pred"].to_numpy() - y)), 2),
                "interval_coverage": round(float(inside.mean()), 3),
                "nominal_coverage": round(self.q[1] - self.q[0], 2),
                "hot_cell_recall": round(float((flag & hi).sum() / max(hi.sum(), 1)), 3),
                "hot_cell_precision": round(float((flag & hi).sum() / max(flag.sum(), 1)), 3)}

    def save(self, d):
        meta = {"cols": self.cols, "scaler": self.scaler.to_dict(), "beta": self.beta.tolist(),
                "cell_factor": self.cell_factor, "bins": self.bins, "resid_q": self.resid_q,
                "sigma": self.sigma, "profile": self.profile, "q": self.q}
        (d / "peak_uplink.json").write_text(json.dumps(meta, indent=1))

    @classmethod
    def load(cls, d):
        m = cls()
        meta = json.loads((d / "peak_uplink.json").read_text())
        m.cols = meta["cols"]; m.scaler = Scaler(**meta["scaler"]); m.beta = np.array(meta["beta"])
        m.cell_factor = meta["cell_factor"]; m.bins = meta["bins"]; m.resid_q = meta["resid_q"]
        m.sigma = meta["sigma"]; m.profile = meta["profile"]; m.q = meta["q"]
        return m


def _norm_cdf(x):
    x = np.asarray(x, float)
    return 0.5 * (1 + np.vectorize(math.erf)(x / math.sqrt(2)))
