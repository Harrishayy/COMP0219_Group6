"""Calibration fits from tunnel runs and bench tests.

The Pi records its own session (Pi clock: every frame's reading, every tick's output); the Wind Tunnel Logger
records our output and the pitot on the laptop's clock. align_offset finds the clock offset by cross-correlating
the output both of them saw. fit_power then fits the map from reading to pitot speed, scored on 2–10 m/s only
(what the demo tests), with a search over the pitot's lag. pluck gives an instrument's natural frequency and
damping from a free decay (deflect it in still air, let go, record).
"""
from __future__ import annotations

import math

import numpy as np

from .models import PowerLaw


def resample(t, v, grid):
    t, v = np.asarray(t, float), np.asarray(v, float)
    ok = np.isfinite(v)
    return np.interp(grid, t[ok], v[ok])


def align_offset(t_a, v_a, t_b, v_b, max_shift: float = 10.0, step: float = 0.005, start=None) -> float:
    """Offset d with b(t) ≈ a(t − d): add d to a's times to put a on b's clock. The clocks may differ by hours
    (Pi boot time vs laptop epoch), so the search is ±max_shift around `start`, by default the offset that lines
    up the two recordings' first samples (start both recordings within a few seconds of each other).

    Pearson correlation per lag, between b's core (its ends trimmed by max_shift) and the matching stretch of a:
    a plain correlation drifts toward lags that overlap more of the demo's long plateaus."""
    t_a, t_b = np.asarray(t_a, float), np.asarray(t_b, float)
    d0 = t_b[0] - t_a[0] if start is None else start
    core = np.arange(t_b[0] + max_shift, t_b[-1] - max_shift, step)
    if core.size < 200:
        raise ValueError("recordings too short to align: they need to overlap by more than 2·max_shift")
    b = resample(t_b, v_b, core)
    b = b - b.mean()
    m, n = int(round(max_shift / step)), core.size
    a = resample(t_a + d0, v_a, core[0] - m * step + step * np.arange(n + 2 * m))
    nfft = 1 << int(np.ceil(np.log2(a.size + n)))
    c = np.fft.irfft(np.fft.rfft(a, nfft) * np.conj(np.fft.rfft(b, nfft)), nfft)[:2 * m + 1]  # Σ a[j+i]·b[i]
    cs, cs2 = np.concatenate(([0.0], np.cumsum(a))), np.concatenate(([0.0], np.cumsum(a * a)))
    j = np.arange(2 * m + 1)
    s1, s2 = cs[j + n] - cs[j], cs2[j + n] - cs2[j]
    r = c / np.sqrt(np.maximum(s2 - s1 * s1 / n, 1e-12) * float(np.sum(b * b)))
    # a's stretch starting at index j sits (j − m)·step later than b's core: b(t) ≈ a(t + (j − m)·step − d0)
    return float(d0 - (int(np.argmax(r)) - m) * step)


def fit_power(t, y, t_gt, v_gt, p: float = 2.0, band=(2.0, 10.0), lags=np.arange(0.0, 0.81, 0.02),
              y0: float | None = None) -> dict:
    """Fit v = k·(y − y0)^(1/p) to the pitot. y0 is fitted unless given (pass the still-air reading to pin the
    zero, which is what dominated the MSE in Part 1). The pitot's lag is searched over `lags`."""
    t, y = np.asarray(t, float), np.asarray(y, float)
    best = None
    for lag in lags:
        g = np.interp(t, np.asarray(t_gt) - lag, v_gt, left=np.nan, right=np.nan)   # pitot, lag removed
        keep = np.isfinite(g) & np.isfinite(y) & (g >= band[0]) & (g <= band[1])
        if keep.sum() < 20:
            continue
        yy, gg = y[keep], g[keep]
        cands = [y0] if y0 is not None else np.linspace(np.percentile(yy, 0) - 0.5 * np.ptp(yy), np.percentile(yy, 1), 60)
        for c in cands:
            s = np.maximum(yy - c, 0.0) ** (1.0 / p)
            if not np.any(s):
                continue
            k = float(np.sum(s * gg) / np.sum(s * s))
            e = k * s - gg
            mse = float(np.mean(e * e))
            if best is None or mse < best["mse"]:
                best = {"model": PowerLaw(k=k, y0=float(c), p=p), "lag": float(lag), "mse": mse,
                        "bias": float(e.mean()), "sigma": float(e.std()), "n": int(e.size)}
    if best is None:
        raise ValueError("no samples with the pitot in the scored band")
    return best


def pluck(t, y) -> dict:
    """Natural frequency and damping ratio from a free decay, by peak spacing and logarithmic decrement."""
    t, y = np.asarray(t, float), np.asarray(y, float)
    tail = y[int(0.8 * y.size):]
    x = y - tail.mean()
    i = np.flatnonzero((x[1:-1] > x[:-2]) & (x[1:-1] >= x[2:]) & (x[1:-1] > 0)) + 1
    i = i[x[i] > 0.05 * x[i].max()] if i.size else i
    if i.size < 3:
        raise ValueError("need at least three peaks: deflect it further, or record longer")
    td = float(np.mean(np.diff(t[i])))
    delta = float(np.mean(np.log(x[i][:-1] / x[i][1:])))
    zeta = delta / math.sqrt(4 * math.pi ** 2 + delta ** 2)
    fd = 1.0 / td
    return {"fn": fd / math.sqrt(1 - zeta ** 2), "zeta": zeta, "fd": fd, "peaks": int(i.size)}
