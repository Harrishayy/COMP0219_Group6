"""Parameter fitting against ground truth.

Methods
- `lsq`         scipy least_squares (TRF, bounded) on est - gt over the chosen free params.
- `robust_lsq`  same with a soft-L1 loss: residuals beyond ~0.3 m/s count linearly, so gust spikes,
                GT glitches and the first samples after a step do not drag the static curve.
- `dynamics`    bounded Nelder-Mead on MSE over the response: EMA weight `ema_alpha` and `lead_tau`
                (the cost is non-smooth: median filter, clamp). Fitting either sets filter_kind = 1 (EMA),
                the smoother the team uses; `lpf_hz` and `filter_kind` are never fitted.
`nelder_mead` is accepted as an alias: `dynamics` if every free param is dynamic, else `robust_lsq`.

All methods run the *full* model (static + filter) so the static fit already accounts for the filter's
delay, and start from a closed-form linear-regression guess (see `initial_guess`) when that is better
than the current values, so a bad starting point cannot strand the optimiser.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import least_squares, minimize

from . import metrics, models
from .data import Run

METHODS = ("robust_lsq", "lsq", "dynamics")
DYNAMIC_PARAMS = ("ema_alpha", "lead_tau")
NEVER_FIT = {"filter_kind", "lpf_hz"}


def normalize_method(method: str | None, free=None) -> str:
    """Map legacy / unknown names onto METHODS (`nelder_mead` -> dynamics or robust_lsq)."""
    if method in METHODS:
        return method
    if method == "nelder_mead":
        return "dynamics" if free and set(free) <= set(DYNAMIC_PARAMS) else "robust_lsq"
    return "robust_lsq"


@dataclass
class FitResult:
    ok: bool
    method: str
    model: str
    params: dict
    before: dict
    after: dict
    free: list
    n: int
    message: str = ""
    elapsed_s: float = 0.0
    cv: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("ok", "method", "model", "params", "before", "after", "free", "n",
                                              "message", "elapsed_s", "cv")}


def prepare(run: Run, window_s: float | None = None, lag_s: float = 0.0):
    """Sensor-base arrays for fitting: (raw dict, gt on sensor timestamps)."""
    r = run.window(window_s)
    t = r.t_s - lag_s
    from .metrics import resample

    gt = resample(r.t_g, r.gt, t)
    raw = {k: np.asarray(v, dtype=float) for k, v in r.sensor.items()}
    return raw, gt


def default_free(model_id: str, method: str) -> list[str]:
    if method == "dynamics":
        return list(DYNAMIC_PARAMS)
    return list(models.MODELS[model_id].fit_params)


def initial_guess(model_id: str, params: dict, raw: dict, gt) -> dict:
    """Closed-form static guess from linear regression on settled samples."""
    gt = np.asarray(gt, dtype=float)
    x = np.asarray(raw[models.feature_channel(model_id, raw)], dtype=float)
    ok = np.isfinite(x) & np.isfinite(gt) & (gt > 1.0)
    if ok.sum() < 20:
        return {}
    if model_id == "rotor":
        ok &= x >= params.get("rotor_min_hz", 0.0)
        if ok.sum() < 20:
            return {}
        slope, off = np.polyfit(x[ok], gt[ok], 1)
        return {"rotor_slope": float(slope), "rotor_offset": float(off)}
    if model_id == "sqrt":
        rho = models.density(params, raw, len(x))
        rho = np.broadcast_to(rho, x.shape)[ok]
        k, c = np.polyfit(x[ok], gt[ok] ** 2 * rho, 1)  # v² rho = k x - k x0
        if k <= 0:
            return {}
        return {"sqrt_k": float(k), "sqrt_x0": float(-c / k)}
    return {}


def _residual_fn(model_id, base, free, raw, gt, mask):
    def predict(vec):
        p = dict(base)
        p.update(zip(free, vec))
        return models.apply(model_id, p, raw)

    def resid(vec):
        r = predict(vec)[mask] - gt[mask]
        return np.where(np.isfinite(r), r, 10.0)

    return predict, resid


def _mse(resid, vec):
    r = resid(vec)
    return float(np.mean(r * r))


GRID = {"lead_tau": (0.0, 0.03, 0.08, 0.15, 0.25, 0.4), "ema_alpha": (0.02, 0.05, 0.1, 0.2, 0.4, 0.8)}


def _grid_seed(free, x0, lo, hi, resid):
    """Coarse grid over the dynamics params before Nelder-Mead: the lead/EMA cost surface has a
    long curved valley (more lead needs more smoothing), and a simplex started at lead_tau = 0 tends to
    stall on its flat edge."""
    idx = [i for i, f in enumerate(free) if f in GRID]
    if not idx:
        return x0
    best, best_cost = np.array(x0, dtype=float), _mse(resid, x0)
    import itertools

    for combo in itertools.product(*(GRID[free[i]] for i in idx)):
        x = np.array(x0, dtype=float)
        for i, v in zip(idx, combo):
            x[i] = v
        x = np.clip(x, lo, hi)
        c = _mse(resid, x)
        if c < best_cost:
            best, best_cost = x, c
    return best


def fit_arrays(model_id: str, params: dict, raw: dict, gt, method: str = "robust_lsq", free=None,
               train_mask=None, f_scale: float = 0.3, max_nfev: int = 400) -> FitResult:
    t0 = time.perf_counter()
    method = normalize_method(method, free)
    gt = np.asarray(gt, dtype=float)
    base = models.default_params(model_id)
    base.update({k: float(v) for k, v in params.items() if k in base})
    free = [f for f in (free or default_free(model_id, method)) if f in base and f not in NEVER_FIT]
    if any(f in DYNAMIC_PARAMS for f in free):
        base["filter_kind"] = float(models.FILTER_EMA)  # tuning the EMA: run the chain with it
    if not free:
        return FitResult(False, method, model_id, base, {}, {}, [], 0, "no free parameters")
    try:
        models.feature_channel(model_id, raw)
    except KeyError as e:
        return FitResult(False, method, model_id, base, {}, {}, free, 0, str(e))
    mask = np.isfinite(gt)
    n_skip = int(0.5 * models.FS)  # filter settling at the start of the window
    mask[:n_skip] = False
    if train_mask is not None:
        mask &= train_mask
    n = int(mask.sum())
    if n < 200:
        return FitResult(False, method, model_id, base, {}, {}, free, n,
                         f"only {n} samples with ground truth; need ≥ 200 (1 s)")

    est0 = models.apply(model_id, base, raw)
    before = metrics.compute(np.where(mask, est0, np.nan), gt)
    bnd = models.bounds(model_id)
    lo = np.array([bnd[f][0] for f in free])
    hi = np.array([bnd[f][1] for f in free])
    predict, resid = _residual_fn(model_id, base, free, raw, gt, mask)

    x0 = np.clip([base[f] for f in free], lo, hi)
    guess = initial_guess(model_id, base, raw, np.where(mask, gt, np.nan))
    if guess and any(g in free for g in guess):
        xg = np.clip([guess.get(f, base[f]) for f in free], lo, hi)
        if _mse(resid, xg) < _mse(resid, x0):
            x0 = xg

    msg = ""
    try:
        if method in ("lsq", "robust_lsq"):
            res = least_squares(resid, x0, bounds=(lo, hi), x_scale="jac", max_nfev=max_nfev,
                                loss="soft_l1" if method == "robust_lsq" else "linear", f_scale=f_scale)
            x, ok, msg = res.x, bool(res.success), res.message
        else:
            x0 = _grid_seed(free, x0, lo, hi, resid)
            scale = np.maximum(np.abs(x0) * 0.25, (hi - lo) * 0.05)
            scale = np.where(scale > 0, scale, 1.0)

            def cost(u):
                return _mse(resid, np.clip(x0 + u * scale, lo, hi))

            res = minimize(cost, np.zeros(len(free)), method="Nelder-Mead",
                           options={"xatol": 1e-4, "fatol": 1e-7, "maxfev": max_nfev * 3, "adaptive": True})
            x = np.clip(x0 + res.x * scale, lo, hi)
            ok, msg = bool(res.success) or res.nfev >= max_nfev * 3, str(res.message)
    except Exception as e:  # numerical trouble: keep the old params
        return FitResult(False, method, model_id, base, before, before, free, n, f"fit failed: {e!r}",
                         time.perf_counter() - t0)

    new = dict(base)
    new.update({f: float(v) for f, v in zip(free, x)})
    after = metrics.compute(np.where(mask, predict(x), np.nan), gt)
    if after.get("mse", np.inf) > before.get("mse", np.inf):
        # never hand back something worse than what we started with
        return FitResult(False, method, model_id, base, before, before, free, n,
                         "fit did not improve MSE; parameters unchanged", time.perf_counter() - t0)
    return FitResult(ok or after["mse"] < before["mse"], method, model_id, new, before, after, free, n, msg,
                     time.perf_counter() - t0)


def fit_run(model_id: str, params: dict, run: Run, method: str = "robust_lsq", free=None,
            window_s: float | None = None, lag_s: float = 0.0, **kw) -> FitResult:
    raw, gt = prepare(run, window_s, lag_s)
    return fit_arrays(model_id, params, raw, gt, method, free, **kw)


def cross_validate(model_id: str, params: dict, raw: dict, gt, method: str = "robust_lsq", free=None,
                   k: int = 5) -> dict:
    """Blocked k-fold: contiguous time blocks, so neighbouring (correlated) samples never sit on both
    sides of the split. The model always runs over the full series (filter state stays continuous);
    only the residual mask changes. Held-out MSE close to train MSE means the fit generalises."""
    gt = np.asarray(gt, dtype=float)
    n = len(gt)
    edges = np.linspace(0, n, k + 1).astype(int)
    folds = []
    for i in range(k):
        test = np.zeros(n, bool)
        test[edges[i]:edges[i + 1]] = True
        r = fit_arrays(model_id, params, raw, gt, method, free, train_mask=~test)
        est = models.apply(model_id, r.params, raw)
        m = metrics.compute(np.where(test, est, np.nan), gt, with_lag=False)
        folds.append({"fold": i, "train_mse": r.after.get("mse"), "test_mse": m.get("mse"),
                      "params": {f: r.params[f] for f in r.free}})
    tm = np.array([f["test_mse"] for f in folds if f["test_mse"] is not None], dtype=float)
    return {"k": k, "folds": folds, "test_mse_mean": float(np.nanmean(tm)) if len(tm) else None,
            "test_mse_std": float(np.nanstd(tm)) if len(tm) else None}


def sweep(model_id: str, params: dict, raw: dict, gt, name: str, values) -> list[dict]:
    """MSE as a function of one parameter (e.g. ema_alpha): for trade-off plots in the report."""
    out = []
    for v in values:
        p = dict(params, **{name: float(v)})
        m = metrics.compute(models.apply(model_id, p, raw), gt, with_lag=False)
        out.append({"value": float(v), **m})
    return out


# ---------- load-cell scale from hanging known masses ----------
# Static load-cell calibration with known masses (SPEC §3).
# 
# The board reports force F = (counts - lc_offset) * lc_n_per_count. Hanging masses m_i gives true
# weights W_i = m_i g. A least-squares line through the origin, F = s W, gives the scale error s, so
# lc_n_per_count_new = lc_n_per_count_old / s.
# The fit also reports linearity (max residual as % of full scale) and an intercept check: a large
# intercept means the tare was taken with load on the cell.
G = 9.80665


def solve_weights(masses_g, forces_n, old_n_per_count: float) -> dict:
    m = np.asarray(masses_g, dtype=float) / 1000.0
    f = np.asarray(forces_n, dtype=float)
    ok = np.isfinite(m) & np.isfinite(f)
    m, f = m[ok], f[ok]
    if len(m) < 1 or not np.any(m):
        raise ValueError("need at least one non-zero mass")
    w = m * G
    s = float(np.dot(f, w) / np.dot(w, w))
    resid = f - s * w
    out = {
        "scale_error": s,
        "lc_n_per_count_old": float(old_n_per_count),
        "lc_n_per_count_new": float(old_n_per_count / s),
        "residual_max_n": float(np.max(np.abs(resid))),
        "linearity_pct_fs": float(100 * np.max(np.abs(resid)) / max(np.max(np.abs(f)), 1e-12)),
        "points": [{"mass_g": float(mi * 1000), "weight_n": float(wi), "measured_n": float(fi)}
                   for mi, wi, fi in zip(m, w, f)],
    }
    if len(m) >= 2:
        slope, icpt = np.polyfit(w, f, 1)
        out["intercept_n"] = float(icpt)
    return out
