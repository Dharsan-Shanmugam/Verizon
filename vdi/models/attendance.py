"""Attendance model: ridge on pre-event context blended with similar-event kNN.

Target = actual attendance / reference capacity.  Interval = empirical
residual quantiles per category x home/away segment.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .. import config
from .. import features as F
from .core import Scaler, mape, ridge_fit, ridge_predict

Q_LO, Q_HI = 0.05, 0.95


class AttendanceModel:
    name = "attendance"

    def __init__(self):
        self.cols: list[str] = []
        self.scaler = Scaler()
        self.beta = None
        self.knn_scaler = Scaler()
        self.knn_Z = None
        self.knn_y = None
        self.knn_t = None
        self.knn_ids = None
        self.w_ridge = 0.5
        self.k = config.model_cfg()["attendance_knn_k"]
        self.resid_q: dict[str, list[float]] = {}
        self.fill: dict[str, float] = {}

    # ----------------------------------------------------------- helpers
    def _X(self, ev):
        f = F.attendance_frame(ev)
        if not self.cols:
            self.cols = list(f.columns)
            self.fill = {c: float(np.nanmedian(f[c])) if f[c].notna().any() else 0.0 for c in self.cols}
        f = f[self.cols].fillna(self.fill)
        return f

    def _knn_raw(self, f):
        return f[F.KNN_COLS].to_numpy(float)

    def _knn_predict(self, Zq, tq, exclude_self_ids=None):
        out = np.empty(len(Zq))
        for s in range(0, len(Zq), 512):
            zq = Zq[s:s + 512]
            d = ((zq[:, None, :] - self.knn_Z[None, :, :]) ** 2).sum(-1)
            # only events that started before the query event
            d = np.where(self.knn_t[None, :] < tq[s:s + 512, None], d, np.inf)
            if exclude_self_ids is not None:
                same = exclude_self_ids[s:s + 512, None] == self.knn_ids[None, :]
                d = np.where(same, np.inf, d)
            k = min(self.k, d.shape[1])
            idx = np.argpartition(d, k - 1, axis=1)[:, :k]
            dd = np.take_along_axis(d, idx, 1)
            w = np.where(np.isfinite(dd), 1.0 / (np.sqrt(dd) + 0.05), 0.0)
            yy = self.knn_y[idx]
            ws = w.sum(1)
            out[s:s + 512] = np.where(ws > 0, (w * yy).sum(1) / np.maximum(ws, 1e-9), np.nan)
        return out

    @staticmethod
    def _segment(ev):
        return (ev["event_category"].astype(str) + "|" + ev["anchor_team_home_away"].astype(str)).to_numpy()

    # ----------------------------------------------------------- fit / predict
    def fit(self, ev: pd.DataFrame):
        ev = ev.reset_index(drop=True)
        f = self._X(ev)
        cr = F.cap_ref(ev)
        y = ev["actual_attendance"].to_numpy(float) / cr
        X = self.scaler.fit(f.to_numpy()).transform(f.to_numpy())
        self.beta = ridge_fit(X, y, alpha=config.model_cfg()["attendance_ridge_alpha"])
        r = ridge_predict(X, self.beta)
        kr = self._knn_raw(f)
        self.knn_scaler.fit(kr)
        self.knn_Z = self.knn_scaler.transform(kr) * F.KNN_WEIGHTS
        self.knn_y = y
        self.knn_t = ev["event_start"].astype("int64").to_numpy() // 10**9
        self.knn_ids = ev["event_id"].to_numpy()
        kn = self._knn_predict(self.knn_Z, self.knn_t + 1, exclude_self_ids=self.knn_ids)
        kn = np.where(np.isfinite(kn), kn, r)
        best = (1.0, np.inf)
        for w in np.linspace(0, 1, 21):
            e = np.mean(np.abs(w * r + (1 - w) * kn - y))
            if e < best[1]:
                best = (float(w), e)
        self.w_ridge = best[0]
        blend = self.w_ridge * r + (1 - self.w_ridge) * kn
        res = (y - blend)
        seg = self._segment(ev)
        self.resid_q = {"__all__": [float(np.quantile(res, Q_LO)), float(np.quantile(res, Q_HI))]}
        for s_ in np.unique(seg):
            m = seg == s_
            if m.sum() >= 40:
                self.resid_q[s_] = [float(np.quantile(res[m], Q_LO)), float(np.quantile(res[m], Q_HI))]
        return self

    def predict(self, ev: pd.DataFrame) -> pd.DataFrame:
        ev = ev.reset_index(drop=True)
        f = self._X(ev)
        cr = F.cap_ref(ev)
        X = self.scaler.transform(f.to_numpy())
        r = ridge_predict(X, self.beta)
        Zq = self.knn_scaler.transform(self._knn_raw(f)) * F.KNN_WEIGHTS
        tq = ev["event_start"].astype("int64").to_numpy() // 10**9
        kn = self._knn_predict(Zq, tq, exclude_self_ids=ev["event_id"].to_numpy())
        kn = np.where(np.isfinite(kn), kn, r)
        ratio = np.clip(self.w_ridge * r + (1 - self.w_ridge) * kn, 0.01, 1.15)
        seg = self._segment(ev)
        lo_q = np.array([self.resid_q.get(s, self.resid_q["__all__"])[0] for s in seg])
        hi_q = np.array([self.resid_q.get(s, self.resid_q["__all__"])[1] for s in seg])
        cap_lim = ev["capacity_limit_pct"].fillna(100).to_numpy() / 100.0
        upper_cap = np.maximum(cr * 1.12 * cap_lim, 1)
        pred = np.minimum(ratio * cr, upper_cap)
        return pd.DataFrame({
            "event_id": ev["event_id"],
            "attendance_pred": np.round(pred),
            "attendance_low": np.round(np.clip((ratio + lo_q) * cr, 0, None)),
            "attendance_high": np.round(np.minimum((ratio + hi_q) * cr, upper_cap)),
            "ridge_ratio": r, "knn_ratio": kn, "capacity_ref": cr,
        })

    def evaluate(self, ev: pd.DataFrame) -> dict:
        p = self.predict(ev)
        y = ev["actual_attendance"].to_numpy(float)
        inside = (y >= p["attendance_low"].to_numpy()) & (y <= p["attendance_high"].to_numpy())
        base = ev["expected_attendance"].to_numpy(float)
        return {"n": int(len(y)), "mape_pct": round(mape(y, p["attendance_pred"], floor=100), 2),
                "mae_people": round(float(np.mean(np.abs(y - p["attendance_pred"]))), 0),
                "bias_people": round(float(np.mean(p["attendance_pred"] - y)), 0),
                "interval_coverage": round(float(inside.mean()), 3),
                "baseline_expected_mape_pct": round(mape(y, base, floor=100), 2)}

    # ----------------------------------------------------------- persistence
    def save(self, d):
        meta = {"cols": self.cols, "scaler": self.scaler.to_dict(), "beta": self.beta.tolist(),
                "knn_scaler": self.knn_scaler.to_dict(), "w_ridge": self.w_ridge, "k": self.k,
                "resid_q": self.resid_q, "fill": self.fill}
        (d / "attendance.json").write_text(json.dumps(meta, indent=1))
        np.savez_compressed(d / "attendance_knn.npz", Z=self.knn_Z.astype(np.float32), y=self.knn_y,
                            t=self.knn_t, ids=self.knn_ids.astype(str))

    @classmethod
    def load(cls, d):
        m = cls()
        meta = json.loads((d / "attendance.json").read_text())
        m.cols = meta["cols"]; m.fill = meta["fill"]
        m.scaler = Scaler(**meta["scaler"]); m.beta = np.array(meta["beta"])
        m.knn_scaler = Scaler(**meta["knn_scaler"]); m.w_ridge = meta["w_ridge"]; m.k = meta["k"]
        m.resid_q = meta["resid_q"]
        z = np.load(d / "attendance_knn.npz", allow_pickle=False)
        m.knn_Z = z["Z"].astype(float); m.knn_y = z["y"]; m.knn_t = z["t"]; m.knn_ids = z["ids"]
        return m
