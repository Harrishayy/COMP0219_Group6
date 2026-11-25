"""Accuracy metrics (SPEC §4). The marking ranks on MSE; bias and sigma explain where it comes from:
mse = bias² + sigma²."""
from __future__ import annotations

import numpy as np

from . import models

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


def stream_stats(t_pc, n_col=None, nominal_hz: float = 200.0, window_s: float = 2.0, now: float | None = None) -> dict:
    """Arrival rate over the last `window_s`, and dropped ticks from the board's sample counter `n`
    (PC arrival times are bursty over USB, so gaps in `n` are the reliable drop signal).
    With `now`, a stream that has gone quiet for longer than `window_s` reads 0 Hz instead of its last rate."""
    t = np.asarray(t_pc, dtype=float)
    out = {"hz": 0.0, "drops": 0}
    if len(t) >= 2 and (now is None or now - t[-1] <= window_s):
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


# ---------------------------------------------------------------------------------------- diagnosis
SEVERITY = {"high": 0, "medium": 1, "info": 2}


def steady_mask(gt, fs: float = 200.0, max_accel: float = 0.3, min_speed: float = 1.0) -> np.ndarray:
    """Samples where the ground truth holds a speed: gs = centred 50-sample boxcar of GT, and
    |gs[i+10] − gs[i−10]| / (20·dt) < max_accel, with gt > min_speed (same rule as the dashboard)."""
    gt = np.asarray(gt, dtype=float)
    ok = np.isfinite(gt)
    out = np.zeros(len(gt), bool)
    if ok.sum() < fs:
        return out
    gs = np.convolve(_fill(gt, ok), np.ones(50) / 50, mode="same")
    slope = np.abs(gs[20:] - gs[:-20]) * fs / 20
    out[10:-10] = slope < max_accel
    return out & ok & (gt > min_speed)


def diagnose(est, gt, feature, rho, params: dict, fs: float = 200.0) -> list[dict]:
    """What to change when the PC preview `est` (with `params`) disagrees with the ground truth.

    Static checks use steady samples only, so filter lag on steps is not mistaken for a calibration error.
    Each finding: {id, severity: high|medium|info, title, why, action, apply: {param: value} | None, gain},
    where `gain` is the expected MSE reduction (m²/s²) when it can be estimated. Sorted by severity, then gain.
    """
    est, gt = np.asarray(est, dtype=float), np.asarray(gt, dtype=float)
    force = np.asarray(feature, dtype=float) if feature is not None else np.full(len(gt), np.nan)
    rho = models.density(params, {} if rho is None else {"rho": np.asarray(rho, dtype=float)}, len(gt))
    p = {**models.default_params(), **params}
    k, f0 = float(p["sqrt_k"]), float(p["sqrt_x0"])
    ok = np.isfinite(est) & np.isfinite(gt)
    st = steady_mask(gt, fs) & ok
    out: list[dict] = []

    def add(id_, sev, title, why, action, apply=None, gain=None):
        out.append({"id": id_, "severity": sev, "title": title, "why": why, "action": action, "apply": apply,
                    "gain": None if gain is None else float(gain)})

    def mse(a, m):
        return float(np.mean((a[m] - gt[m]) ** 2)) if m.any() else float("nan")

    def bias(m):
        return float(np.mean(est[m] - gt[m])) if m.sum() >= 100 else float("nan")

    # static_refit: F = F0 + Cd·A·(rho·gt²/2) on steady samples
    fit_ok = st & np.isfinite(force)
    if fit_ok.sum() >= 200:
        q = 0.5 * rho[fit_ok] * gt[fit_ok] ** 2
        cda, f0_new = np.polyfit(q, force[fit_ok], 1)
        if cda > 0:
            k_new = 2.0 / cda
            arg = k_new * (force - f0_new) / rho
            est_new = np.sqrt(np.where(arg > 0, arg, 0.0))
            before, after = mse(est, fit_ok), mse(est_new, fit_ok)
            if after < 0.9 * before and before - after > 0.002:
                add("static_refit", "high" if before > 0.05 else "medium",
                    f"Calibration curve is off: Cd·A {2e4 / k:.1f} → {cda * 1e4:.1f} cm², F₀ {f0:+.4f} → {f0_new:+.4f} N",
                    f"A straight-line fit of force against ½ρv² on steady speeds cuts steady-state MSE from "
                    f"{before:.4f} to {after:.4f} m²/s².",
                    "Apply the refit (or run the curve fit), then push.",
                    {"sqrt_k": k_new, "sqrt_x0": float(f0_new)}, before - after)

    # scale: reads r·gt across the range
    hi = st & (gt > 3)
    if hi.sum() >= 100:
        r = float(np.sum(est[hi] * gt[hi]) / np.sum(gt[hi] ** 2))
        if abs(r - 1) > 0.02:
            add("scale", "high" if abs(r - 1) > 0.05 else "medium",
                f"Reads {abs(r - 1) * 100:.1f} % {'high' if r > 1 else 'low'} across the range",
                "A constant percentage error at every speed is a wrong drag area Cd·A (or load-cell scale).",
                f"Multiply Cd·A by {r * r:.3f} (k ÷ {r * r:.3f}). If you trust Cd·A, redo the hanging-weights step.",
                {"sqrt_k": k / (r * r)}, mse(est, hi) - mse(est / r, hi))

    # low_speed_offset: low-speed bias differs from high-speed bias
    low, high = st & (gt > 1.5) & (gt < 4), st & (gt > 6)
    d = bias(low) - bias(high)
    if np.isfinite(d) and abs(d) > 0.15:
        df0 = float(np.mean(rho[low] * (2.0 / k) * (est[low] ** 2 - gt[low] ** 2) / 2.0))
        add("low_speed_offset", "high" if abs(d) > 0.3 else "medium",
            f"Low speeds read {abs(d):.2f} m/s too {'high' if d > 0 else 'low'} compared with high speeds",
            "An error that grows as speed drops (v ∝ √F) is a wrong zero-flow force F₀.",
            f"Change F₀ by {df0:+.4f} N, or re-measure F₀ with the fan off.", {"sqrt_x0": f0 + df0})

    # dead_zone: output stuck at 0 while there is wind
    wind = ok & (gt > 1.5)
    if wind.sum() >= 100:
        frac = float(np.mean(est[wind] <= 1e-6))
        if frac > 0.02:
            add("dead_zone", "high", f"Reads 0 m/s for {frac * 100:.0f} % of samples with wind",
                "F − F₀ is negative there: F₀ is set too high, or the TARE was taken with load or wind on the plate.",
                "Fan off, TARE, then re-measure F₀.")

    # drift: bias moves during the run
    idx = np.flatnonzero(st)
    if len(idx) >= 600:
        a, b = idx[: len(idx) // 3], idx[-(len(idx) // 3):]
        drift = float(np.mean(est[b] - gt[b]) - np.mean(est[a] - gt[a]))
        if abs(drift) > 0.1:
            add("drift", "medium", f"Bias drifted {drift:+.2f} m/s during the run",
                "The load-cell zero moves as the electronics warm up or the temperature changes.",
                "Power the board 10 min before calibrating, then TARE and re-measure F₀ right before the run.")

    # dynamics
    lag_ms = 1000.0 * xcorr_lag(np.where(ok, est, np.nan), np.where(ok, gt, np.nan), fs)
    sigma = float(np.std(est[st] - gt[st])) if st.sum() >= 100 else float("nan")
    ema_on = models.filter_kind_of(p) == models.FILTER_EMA
    a_, lpf = float(p["ema_alpha"]), float(p["lpf_hz"])
    if np.isfinite(lag_ms) and lag_ms > 40:
        add("lag", "high" if lag_ms > 100 else "medium", f"Lags the pitot by {lag_ms:.0f} ms",
            "On speed changes every millisecond of lag adds error; the smoother is too slow for the steps.",
            (f"Raise α to {min(a_ * 1.5, 1.0):.3g}" if ema_on else f"Raise the cutoff to {lpf * 1.5:.3g} Hz")
            + ", or add lag compensation, then refit the response.",
            {"ema_alpha": min(a_ * 1.5, 1.0)} if ema_on else {"lpf_hz": lpf * 1.5})
    if np.isfinite(sigma) and sigma > 0.12 and not (np.isfinite(lag_ms) and lag_ms > 40):
        add("noise", "medium", f"Noisy at steady speed: σ {sigma:.3f} m/s",
            "Turbulence and plate vibration get through; the response is fast enough to afford more smoothing.",
            f"Lower α to {a_ * 0.7:.3g}" if ema_on else f"Lower the cutoff to {lpf * 0.7:.3g} Hz",
            {"ema_alpha": a_ * 0.7} if ema_on else {"lpf_hz": lpf * 0.7})
    ov = step_metrics(est, gt, fs)["median"].get("overshoot_pct", float("nan"))
    if np.isfinite(ov) and ov > 8:
        lt = float(p["lead_tau"])
        add("overshoot", "medium", f"Overshoots steps by {ov:.0f} %",
            "Too much lag compensation, or the plate's mechanical ringing (~6 Hz) passing through the smoother.",
            f"Halve lag compensation to {lt / 2:.3g} s." if lt > 0 else
            "Switch to the Butterworth filter at ~4 Hz to damp the ringing.",
            {"lead_tau": lt / 2} if lt > 0 else {"filter_kind": 0.0, "lpf_hz": 4.0})

    # nonlinear: per-band bias spread left after scale/offset are fixed
    masks = [st & (gt >= lo_) & (gt < hi_) for lo_, hi_ in ((2, 4), (4, 6), (6, 8), (8, 11))]
    bands = [float(np.mean(est[m] - gt[m])) for m in masks if m.sum() > fs]
    if len(bands) >= 3:
        spread = max(bands) - min(bands)
        if spread > 0.2 and not any(f["id"] in ("static_refit", "low_speed_offset") for f in out):
            add("nonlinear", "info", f"Error changes with speed by {spread:.2f} m/s even after scale and offset",
                "Cd is not constant: it changes with Reynolds number, or the plate bends at high speed.",
                "Fit the curve on the competition speed range only, or accept it. A stiffer, sharp-edged plate keeps Cd constant.")

    # environment and reference
    med = float(np.nanmedian(rho))
    if not 1.10 <= med <= 1.30:
        add("rho", "high", f"Air density {med:.3f} kg/m³ is implausible",
            "ρ goes straight into v = √(2F/(ρ·Cd·A)); a wrong baro or temperature reading scales every speed.",
            "Check the baro (Pa, absolute) and temperature (°C) readings with ENV.")
    slow = st & (gt < 3)
    if slow.sum() >= 200:
        g = _fill(gt, np.isfinite(gt))
        resid = g - np.convolve(g, np.ones(int(fs)) / int(fs), mode="same")
        gsd = float(np.std(resid[slow]))
        if gsd > 0.15:
            add("gt_noise", "info", f"The pitot itself is noisy at low speed (σ {gsd:.2f} m/s)",
                "Dynamic pressure is tiny below 3 m/s (≈5 Pa), so the reference wanders.",
                "Use the robust fit and hold each low speed longer.")

    out.sort(key=lambda f: (SEVERITY[f["severity"]], -(f["gain"] or 0.0)))
    return out
