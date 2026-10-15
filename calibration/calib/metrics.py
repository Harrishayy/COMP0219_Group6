"""Accuracy metrics (SPEC §4). The marking ranks on MSE; bias and sigma explain where it comes from:
mse = bias² + sigma²."""
from __future__ import annotations

import numpy as np

def resample(t_src, v_src, t_dst) -> np.ndarray:
    """Linear interpolation of (t_src, v_src) at t_dst; NaN outside the source span or with < 2 points."""
    t_src, v_src, t_dst = (np.asarray(a, dtype=float) for a in (t_src, v_src, t_dst))
    ok = np.isfinite(t_src) & np.isfinite(v_src)
    t_src, v_src = t_src[ok], v_src[ok]
    if len(t_src) < 2:
        return np.full(len(t_dst), np.nan)
    order = np.argsort(t_src, kind="stable")
    t_src, v_src = t_src[order], v_src[order]
    out = np.interp(t_dst, t_src, v_src)
    out[(t_dst < t_src[0]) | (t_dst > t_src[-1])] = np.nan
    return out


def xcorr_lag(est, ref, fs: float = 200.0, max_lag_s: float = 1.0) -> float:
    """Delay of `est` relative to `ref` in seconds (positive = est lags), from the normalised
    cross-correlation peak of the mean-removed derivatives, refined with a parabolic fit.

    Differentiating first makes the peak sharp on step/ramp profiles, where the raw signals are
    dominated by slow plateaus that correlate at almost any lag.
    """
    est, ref = np.asarray(est, dtype=float), np.asarray(ref, dtype=float)
    ok = np.isfinite(est) & np.isfinite(ref)
    if ok.sum() < 50:
        return float("nan")
    # keep the longest contiguous finite run so differentiation stays meaningful
    est, ref = _fill(est, ok), _fill(ref, ok)
    a, b = np.diff(est), np.diff(ref)
    a, b = a - a.mean(), b - b.mean()
    if not (np.any(a) and np.any(b)):
        return float("nan")
    n = len(a)
    m = int(min(max_lag_s * fs, n - 2))
    nfft = 1 << int(np.ceil(np.log2(2 * n)))
    c = np.fft.irfft(np.fft.rfft(a, nfft) * np.conj(np.fft.rfft(b, nfft)), nfft)
    c = np.concatenate((c[-m:], c[: m + 1]))  # lags -m..m
    k = int(np.argmax(c))
    shift = 0.0
    if 0 < k < len(c) - 1:
        y0, y1, y2 = c[k - 1], c[k], c[k + 1]
        den = y0 - 2 * y1 + y2
        if den != 0:
            shift = 0.5 * (y0 - y2) / den
    return (k - m + shift) / fs


def _fill(x, ok):
    idx = np.arange(len(x))
    return np.interp(idx, idx[ok], x[ok])


def _clean(est, gt):
    est, gt = np.asarray(est, dtype=float), np.asarray(gt, dtype=float)
    ok = np.isfinite(est) & np.isfinite(gt)
    return est, gt, ok


def compute(est, gt, fs: float = 200.0, with_lag: bool = True) -> dict:
    est, gt, ok = _clean(est, gt)
    n = int(ok.sum())
    if n < 2:
        return {"n": n}
    e = est[ok] - gt[ok]
    var_gt = float(np.var(gt[ok]))
    mse = float(np.mean(e * e))
    out = {
        "n": n,
        "mse": mse,
        "rmse": float(np.sqrt(mse)),
        "bias": float(np.mean(e)),
        "sigma": float(np.std(e)),
        "mae": float(np.mean(np.abs(e))),
        "max_abs": float(np.max(np.abs(e))),
        "r2": float(1.0 - mse / var_gt) if var_gt > 1e-12 else float("nan"),
    }
    if with_lag:
        out["lag_ms"] = 1000.0 * xcorr_lag(est, gt, fs)
    return out


def stream_stats(t_pc, n_col=None, nominal_hz: float = 200.0, window_s: float = 2.0) -> dict:
    """Arrival rate over the last `window_s`, and dropped ticks from the board's sample counter `n`
    (PC arrival times are bursty over USB, so gaps in `n` are the reliable drop signal)."""
    t = np.asarray(t_pc, dtype=float)
    out = {"hz": 0.0, "drops": 0}
    if len(t) >= 2:
        recent = t[t >= t[-1] - window_s]
        if len(recent) >= 2 and recent[-1] > recent[0]:
            out["hz"] = float((len(recent) - 1) / (recent[-1] - recent[0]))
    if n_col is not None and len(n_col) >= 2:
        n = np.asarray(n_col, dtype=float)
        n = n[np.isfinite(n)]
        d = np.diff(n)
        out["drops"] = int(np.sum(np.where((d > 1) & (d < 1e6), d - 1, 0)))
        out["resets"] = int(np.sum(d < 0))
    elif len(t) >= 2:
        d = np.diff(t)
        out["drops"] = int(np.sum(d > 1.5 * 10.0 / nominal_hz))  # coarse: only big stalls are visible
    return out


def detect_steps(gt, fs: float = 200.0, min_jump: float = 1.0, min_gap_s: float = 2.5) -> list[int]:
    gt = np.asarray(gt, dtype=float)
    if len(gt) < int(2 * fs):
        return []
    g = np.where(np.isfinite(gt), gt, np.nanmedian(gt))
    k = max(int(0.2 * fs), 1)
    gs = np.convolve(g, np.ones(k) / k, mode="same")
    w = int(0.5 * fs)
    d = np.zeros_like(gs)
    d[w:-w] = gs[2 * w:] - gs[:-2 * w]
    idx = []
    cand = np.where(np.abs(d) > min_jump)[0]
    i = 0
    while i < len(cand):
        j = i
        while j + 1 < len(cand) and cand[j + 1] - cand[j] <= 1:
            j += 1
        seg = cand[i:j + 1]
        peak = int(seg[np.argmax(np.abs(d[seg]))])
        if not idx or peak - idx[-1] >= min_gap_s * fs:
            idx.append(peak)
        i = j + 1
    return idx


def step_metrics(est, gt, fs: float = 200.0) -> dict:
    """Per detected GT step: 10–90 % rise of est and gt, est overshoot %, and the t90 delay est vs gt."""
    est, gt = np.asarray(est, dtype=float), np.asarray(gt, dtype=float)
    steps = []
    for i in detect_steps(gt, fs):
        a, b = i - int(1.5 * fs), i + int(3.0 * fs)
        if a < 0 or b > len(gt):
            continue
        pre = np.nanmedian(gt[a:i - int(0.5 * fs)])
        post = np.nanmedian(gt[i + int(2.0 * fs):b])
        span = post - pre
        if not np.isfinite(span) or abs(span) < 0.8:
            continue
        s0 = i - int(0.5 * fs)

        def cross(x, frac):
            y = (x[s0:b] - pre) / span
            hit = np.where(y >= frac)[0]
            return hit[0] / fs if len(hit) else np.nan

        e10, e90, g10, g90 = cross(est, 0.1), cross(est, 0.9), cross(gt, 0.1), cross(gt, 0.9)
        seg = (est[s0:b] - pre) / span
        steps.append({
            "t_index": int(i), "from": float(pre), "to": float(post),
            "rise_est_s": float(e90 - e10), "rise_gt_s": float(g90 - g10),
            "delay_t90_ms": float(1000 * (e90 - g90)),
            "overshoot_pct": float(max(0.0, np.nanmax(seg) - 1.0) * 100) if np.isfinite(seg).any() else np.nan,
        })
    summary = {}
    if steps:
        for k in ("rise_est_s", "rise_gt_s", "delay_t90_ms", "overshoot_pct"):
            vals = np.array([s[k] for s in steps], dtype=float)
            summary[k] = float(np.nanmedian(vals)) if np.isfinite(vals).any() else float("nan")
    return {"steps": steps, "median": summary}


def by_speed_band(est, gt, edges=(0, 2, 4, 6, 8, 10, 15)) -> list[dict]:
    """Error breakdown per GT speed band: shows whether the static curve shape is wrong somewhere."""
    est, gt, ok = _clean(est, gt)
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = ok & (gt >= lo) & (gt < hi)
        if m.sum() < 10:
            continue
        e = est[m] - gt[m]
        rows.append({"band": f"{lo}-{hi}", "n": int(m.sum()), "bias": float(e.mean()),
                     "sigma": float(e.std()), "rmse": float(np.sqrt(np.mean(e * e)))})
    return rows
