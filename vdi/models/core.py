"""Numpy-only ML primitives: scaling, ridge, logistic regression, metrics."""
from __future__ import annotations

import numpy as np


class Scaler:
    def __init__(self, mean=None, std=None):
        self.mean = None if mean is None else np.asarray(mean, float)
        self.std = None if std is None else np.asarray(std, float)

    def fit(self, X):
        self.mean = np.nanmean(X, axis=0)
        sd = np.nanstd(X, axis=0)
        self.std = np.where(sd < 1e-9, 1.0, sd)
        return self

    def transform(self, X):
        Z = (np.asarray(X, float) - self.mean) / self.std
        return np.nan_to_num(Z, nan=0.0, posinf=0.0, neginf=0.0)

    def to_dict(self):
        return {"mean": self.mean.tolist(), "std": self.std.tolist()}


def ridge_fit(X, y, alpha=1.0, w=None):
    """Closed-form ridge on standardized X (intercept unpenalised)."""
    n, p = X.shape
    A = np.hstack([np.ones((n, 1)), X])
    if w is None:
        w = np.ones(n)
    Aw = A * w[:, None]
    reg = alpha * np.eye(p + 1)
    reg[0, 0] = 0.0
    beta = np.linalg.solve(A.T @ Aw + reg, Aw.T @ y)
    return beta


def ridge_predict(X, beta):
    return beta[0] + X @ beta[1:]


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))


def logistic_fit(X, y, iters=400, lr=0.05, l2=1e-4, pos_weight=None, seed=0):
    """Full-batch Adam gradient descent on (weighted) log-loss."""
    n, p = X.shape
    rng = np.random.default_rng(seed)
    y32 = np.asarray(y, np.float32)
    w = rng.normal(0, 0.01, p).astype(np.float32)
    b = float(np.log((y32.mean() + 1e-6) / (1 - y32.mean() + 1e-6)))
    sw = np.where(y32 > 0.5, 1.0 if pos_weight is None else pos_weight, 1.0).astype(np.float32)
    sw /= sw.mean()
    m_w = np.zeros(p, np.float32); v_w = np.zeros(p, np.float32); m_b = 0.0; v_b = 0.0
    b1, b2, eps = 0.9, 0.999, 1e-8
    Xf = np.ascontiguousarray(X, dtype=np.float32)
    XT = np.ascontiguousarray(Xf.T)
    for t in range(1, iters + 1):
        z = Xf @ w + np.float32(b)
        np.clip(z, -35, 35, out=z)
        pr = 1.0 / (1.0 + np.exp(-z))
        err = (pr - y32) * sw
        gw = (XT @ err) / n + l2 * w
        gb = float(err.mean())
        m_w = b1 * m_w + (1 - b1) * gw; v_w = b2 * v_w + (1 - b2) * gw * gw
        m_b = b1 * m_b + (1 - b1) * gb; v_b = b2 * v_b + (1 - b2) * gb * gb
        w -= (lr * (m_w / (1 - b1 ** t)) / (np.sqrt(v_w / (1 - b2 ** t)) + eps)).astype(np.float32)
        b -= lr * (m_b / (1 - b1 ** t)) / (np.sqrt(v_b / (1 - b2 ** t)) + eps)
    return w, b


def auc(y, s):
    y = np.asarray(y).astype(bool)
    s = np.asarray(s, float)
    n1 = y.sum(); n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return float("nan")
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s)); ranks[order] = np.arange(1, len(s) + 1)
    # average ranks for ties
    ss = s[order]
    i = 0
    while i < len(ss):
        j = i
        while j + 1 < len(ss) and ss[j + 1] == ss[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + j + 2) / 2.0
        i = j + 1
    return float((ranks[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def precision_recall(y, s, thr):
    y = np.asarray(y).astype(bool)
    p = np.asarray(s) >= thr
    tp = float((p & y).sum()); fp = float((p & ~y).sum()); fn = float((~p & y).sum())
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return prec, rec, f1


def best_f1_threshold(y, s):
    best = (0.5, -1.0)
    for thr in np.linspace(0.05, 0.95, 37):
        f1 = precision_recall(y, s, thr)[2]
        if f1 > best[1]:
            best = (float(thr), f1)
    return best[0]


def mape(y, p, floor=1.0):
    y = np.asarray(y, float); p = np.asarray(p, float)
    return float(np.mean(np.abs(p - y) / np.maximum(np.abs(y), floor)) * 100)


def isotonic(x, y, increasing=True, n_bins=60, edges=None):
    """Binned pool-adjacent-violators fit; returns (knots_x, knots_y)."""
    x = np.asarray(x, float); y = np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if edges is None:
        edges = np.unique(np.quantile(x, np.linspace(0, 1, n_bins + 1)))
    edges = np.asarray(edges, float)
    idx = np.clip(np.searchsorted(edges, x, side="right") - 1, 0, len(edges) - 2)
    bx = np.array([x[idx == i].mean() for i in range(len(edges) - 1) if (idx == i).any()])
    by = np.array([y[idx == i].mean() for i in range(len(edges) - 1) if (idx == i).any()])
    bw = np.array([(idx == i).sum() for i in range(len(edges) - 1) if (idx == i).any()], float)
    sign = 1 if increasing else -1
    vals = list(sign * by); wts = list(bw); cnt = [1] * len(vals)
    i = 0
    while i < len(vals) - 1:
        if vals[i] > vals[i + 1]:
            nv = (vals[i] * wts[i] + vals[i + 1] * wts[i + 1]) / (wts[i] + wts[i + 1])
            vals[i] = nv; wts[i] += wts[i + 1]; cnt[i] += cnt[i + 1]
            del vals[i + 1], wts[i + 1], cnt[i + 1]
            i = max(i - 1, 0)
        else:
            i += 1
    fy = np.repeat(np.array(vals) * sign, cnt)
    return bx.tolist(), fy.tolist()


def interp(knots, x):
    kx, ky = knots
    return np.interp(np.asarray(x, float), kx, ky)


def psi(expected, actual, bins=10):
    e = np.asarray(expected, float); a = np.asarray(actual, float)
    e = e[np.isfinite(e)]; a = a[np.isfinite(a)]
    if len(e) < 10 or len(a) < 10:
        return float("nan")
    edges = np.unique(np.quantile(e, np.linspace(0, 1, bins + 1)))
    edges[0], edges[-1] = -np.inf, np.inf
    pe = np.histogram(e, edges)[0] / len(e)
    pa = np.histogram(a, edges)[0] / len(a)
    pe = np.clip(pe, 1e-4, None); pa = np.clip(pa, 1e-4, None)
    return float(np.sum((pa - pe) * np.log(pa / pe)))
