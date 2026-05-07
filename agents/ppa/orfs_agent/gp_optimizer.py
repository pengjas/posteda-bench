"""GP-surrogate-driven candidate generation.

Replaces ORFS-Agent's `modelfuncs.py` / `agglomfuncs.py` with a much smaller
sklearn-based equivalent. Latin Hypercube + Matern-GP + UCB-style scoring +
greedy diverse top-k selection.
"""

import math
import random
from typing import Any, Dict, List

import numpy as np

try:
    from sklearn.gaussian_process import GaussianProcessRegressor
    from sklearn.gaussian_process.kernels import Matern, ConstantKernel, WhiteKernel
    from sklearn.preprocessing import StandardScaler
    _HAVE_SK = True
except Exception:
    _HAVE_SK = False


def _categorical_to_index(p: Dict[str, Any], v: Any) -> float:
    cats = p["range"]
    try:
        return float(cats.index(v))
    except ValueError:
        return 0.0


def _index_to_categorical(p: Dict[str, Any], idx: float) -> Any:
    cats = p["range"]
    i = int(round(idx))
    i = max(0, min(len(cats) - 1, i))
    return cats[i]


def encode(params: List[Dict[str, Any]], vec: List[Any]) -> List[float]:
    out = []
    for p, v in zip(params, vec):
        if p["type"] == "categorical":
            out.append(_categorical_to_index(p, v))
        else:
            out.append(float(v))
    return out


def decode(params: List[Dict[str, Any]], xs: List[float]) -> List[Any]:
    out = []
    for p, x in zip(params, xs):
        if p["type"] == "categorical":
            out.append(_index_to_categorical(p, x))
        elif p["type"] == "int":
            lo, hi = p["range"]
            xc = max(float(lo), min(float(hi), float(x)))
            out.append(int(round(xc)))
        else:
            lo, hi = p["range"]
            xc = max(float(lo), min(float(hi), float(x)))
            out.append(float(xc))
    return out


def _bounds(params: List[Dict[str, Any]]):
    lo, hi = [], []
    for p in params:
        if p["type"] == "categorical":
            lo.append(0.0)
            hi.append(float(len(p["range"]) - 1))
        else:
            lo.append(float(p["range"][0]))
            hi.append(float(p["range"][1]))
    return np.array(lo), np.array(hi)


def latin_hypercube(n: int, d: int, rng: np.random.Generator) -> np.ndarray:
    """Standard LHS in [0,1]^d."""
    cut = np.linspace(0.0, 1.0, n + 1)
    u = rng.random((n, d))
    a = cut[:n]
    b = cut[1:]
    rd = u * (b - a)[:, None] + a[:, None]
    out = np.empty_like(rd)
    for j in range(d):
        out[:, j] = rng.permutation(rd[:, j])
    return out


def initial_candidates(params: List[Dict[str, Any]], n: int,
                       seed: int = 0) -> List[List[Any]]:
    """First-iteration LHS sampling, anchored toward defaults.

    We mix one default-near point (small jitter) with (n-1) LHS-spread points
    to keep at least one candidate in the trusted region while exploring.
    """
    rng = np.random.default_rng(seed)
    lo, hi = _bounds(params)
    d = len(params)
    out: List[List[Any]] = []

    # 1 anchor: jittered defaults
    if n > 0:
        anchor = []
        for p in params:
            default = p.get("default")
            if default is None or p["type"] == "categorical":
                anchor.append(rng.uniform(lo[len(anchor)], hi[len(anchor)]))
            else:
                lo_i = float(p["range"][0])
                hi_i = float(p["range"][1])
                d0 = float(default)
                width = (hi_i - lo_i) * 0.05
                v = d0 + rng.uniform(-width, width)
                v = max(lo_i, min(hi_i, v))
                anchor.append(v)
        out.append(decode(params, anchor))

    remaining = max(0, n - 1)
    if remaining > 0:
        u = latin_hypercube(remaining, d, rng)
        for row in u:
            x = lo + row * (hi - lo)
            out.append(decode(params, x.tolist()))

    return out


def _fit_gp(X: np.ndarray, y: np.ndarray):
    if not _HAVE_SK or len(X) < 2:
        return None, None
    try:
        scaler = StandardScaler().fit(X)
        Xs = scaler.transform(X)
        kernel = (ConstantKernel(1.0, (1e-2, 1e2))
                  * Matern(length_scale=1.0, nu=2.5,
                           length_scale_bounds=(1e-2, 1e2))
                  + WhiteKernel(noise_level=1e-3,
                                noise_level_bounds=(1e-6, 1e0)))
        gp = GaussianProcessRegressor(
            kernel=kernel, normalize_y=True, n_restarts_optimizer=2, alpha=1e-6
        )
        gp.fit(Xs, y)
        return gp, scaler
    except Exception as e:
        print(f"GP fit failed: {e}", flush=True)
        return None, None


def gp_propose(params: List[Dict[str, Any]],
               history_X: List[List[Any]],
               history_y: List[float],
               n: int,
               seed: int = 0,
               n_lhs: int = 60) -> List[List[Any]]:
    """Fit GP on history; sample LHS pool; pick top-n by UCB w/ diversity."""
    if len(history_X) < 2 or not _HAVE_SK:
        return initial_candidates(params, n, seed=seed)

    rng = np.random.default_rng(seed)
    d = len(params)
    lo, hi = _bounds(params)

    Xenc = np.array([encode(params, x) for x in history_X], dtype=float)
    y = np.array(history_y, dtype=float)

    # mask penalty values (failed flows) when fitting — let them only act as
    # negative-region hints via separate clipping
    mask = y < 1e5
    if mask.sum() < 2:
        return initial_candidates(params, n, seed=seed)

    gp, scaler = _fit_gp(Xenc[mask], y[mask])
    if gp is None:
        return initial_candidates(params, n, seed=seed)

    # candidate pool
    u = latin_hypercube(n_lhs, d, rng)
    pool = lo + u * (hi - lo)

    pool_s = scaler.transform(pool)
    mu, sigma = gp.predict(pool_s, return_std=True)

    # UCB-style for minimization: score = -mu + kappa * sigma (higher is better)
    kappa = 1.5
    score = -mu + kappa * sigma

    # greedy diverse top-n
    selected_idx: List[int] = []
    while len(selected_idx) < n and len(selected_idx) < len(pool):
        if not selected_idx:
            i = int(np.argmax(score))
        else:
            sel_pts = pool[selected_idx]
            # min L2 distance to any selected point, normalized by bound widths
            widths = np.maximum(hi - lo, 1e-9)
            dists = np.min(
                np.linalg.norm((pool[:, None, :] - sel_pts[None, :, :]) / widths,
                               axis=2), axis=1)
            # combine score with diversity (encourage spread)
            combined = score + 0.3 * dists
            for k in selected_idx:
                combined[k] = -1e18
            i = int(np.argmax(combined))
        selected_idx.append(i)

    out: List[List[Any]] = []
    for i in selected_idx:
        out.append(decode(params, pool[i].tolist()))
    return out
