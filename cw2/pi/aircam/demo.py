"""The graded demo as a speed profile, and the logger's way of scoring a run against it.

The profile follows cw1/docs/demo-protocol.md (the course email of 4 Nov 2026), which we expect to be reused for
Part 2: Protocol 1 holds full, L2 (≈0.80 full), L1 (≈0.67 full), L2, full for ~15 s each, then coasts to rest;
Protocol 2 pulses the fan on/off for 3 s each, then coasts. The fan time constants come from the email's graph:
spinning up from rest reaches 0.89 of full in 3 s (tau 1.35 s); 3 s off from there falls to 0.48 (tau 4.85 s).
Panel-seal changes settle in 1–2 s (tau 0.5 s).

score() pairs each ground-truth sample with the nearest board line within 150 ms by arrival time, as the
Wind Tunnel Logger writes _aligned.csv, and scores only ground truth in 2–10 m/s (the range the demo tests).
"""
from __future__ import annotations

import numpy as np

TAU_ON, TAU_OFF, TAU_PANEL = 1.35, 4.85, 0.5
SCORED = (2.0, 10.0)
ALIGN_TOL_S = 0.150


def segments(full: float = 9.0, l2: float = 0.80, l1: float = 0.67, order: str = "12", still: float = 5.0):
    """(duration s, target m/s, tau s) for each stretch of the demo."""
    p1 = [(15, full, TAU_ON), (15, l2 * full, TAU_PANEL), (15, l1 * full, TAU_PANEL),
          (15, l2 * full, TAU_PANEL), (15, full, TAU_PANEL), (21, 0.0, TAU_OFF)]
    p2 = [(3, full, TAU_ON), (3, 0.0, TAU_OFF), (3, full, TAU_ON), (20, 0.0, TAU_OFF)]
    seq = [(still, 0.0, TAU_OFF)]
    for p in order:
        seq += p1 if p == "1" else p2
    return seq


def profile(dt: float = 0.001, **kw):
    """True test-section speed (t, v) sampled every dt, by first-order approach to each segment's target."""
    t_out, v_out, v, t0 = [], [], 0.0, 0.0
    for dur, target, tau in segments(**kw):
        n = int(round(dur / dt))
        tt = t0 + dt * np.arange(n)
        decay = np.exp(-(tt - t0) / tau)
        vv = target + (v - target) * decay
        t_out.append(tt)
        v_out.append(vv)
        v = target + (v - target) * float(np.exp(-dur / tau))
        t0 += n * dt
    return np.concatenate(t_out), np.concatenate(v_out)


def ground_truth(t, v, rate_hz: float = 200.0, tau: float = 0.0, sigma: float = 0.0, seed: int = 0):
    """The pitot as the logger sees it: a first-order lag, sampled at rate_hz, with white noise."""
    if tau > 0:
        dt = t[1] - t[0]
        a = 1.0 - np.exp(-dt / tau)
        lag = np.empty_like(v)
        acc = v[0]
        for i, x in enumerate(v):
            acc += a * (x - acc)
            lag[i] = acc
        v = lag
    tg = np.arange(t[0], t[-1], 1.0 / rate_hz)
    vg = np.interp(tg, t, v) + np.random.default_rng(seed).normal(0.0, sigma, tg.size) if sigma else np.interp(tg, t, v)
    return tg, vg


def align(tg, ts, vs, tol: float = ALIGN_TOL_S):
    """Each ground-truth time's nearest board sample within tol (NaN where none is that close)."""
    ts, vs = np.asarray(ts, float), np.asarray(vs, float)
    i = np.clip(np.searchsorted(ts, tg), 1, len(ts) - 1)
    j = np.where(np.abs(ts[i - 1] - tg) <= np.abs(ts[i] - tg), i - 1, i)
    out = vs[j].copy()
    out[np.abs(ts[j] - tg) > tol] = np.nan
    return out


def score(tg, vg, ts, vs, band=SCORED) -> dict:
    """MSE, bias and σ of the board against ground truth in the scored band, paired as the logger does."""
    b = align(np.asarray(tg, float), ts, vs)
    keep = np.isfinite(b) & (vg >= band[0]) & (vg <= band[1])
    e = b[keep] - np.asarray(vg)[keep]
    if not e.size:
        return {"n": 0, "mse": float("nan"), "bias": float("nan"), "sigma": float("nan")}
    return {"n": int(e.size), "mse": float(np.mean(e * e)), "bias": float(np.mean(e)), "sigma": float(np.std(e))}
