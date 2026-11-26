"""Calibration from one recorded run, and the pluck test.

The run is what the laptop recorded: the Wind Tunnel Logger's files in the tunnel, or the screen simulator's in a
rehearsal. Both write <run>_aligned.csv (ground truth with the nearest board line) and <run>_student.csv (every
board line). The board streamed speeds through the map it ran with, so that map's inverse recovers the reading d
(how far the ball moved, mm) at every line; nothing has to be copied off the Pi. `fit_pendulum` then fits
v = k·√tan θ(d; arm) to the ground truth in 2–10 m/s only (what the demo scores), searching the time shift between
the two (camera latency against the pitot's lag) and, unless it is pinned, the arm.

pluck gives the ball's natural frequency and damping from a free decay (deflect it in still air, let go).
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np

from . import demo
from .models import Pendulum


def _columns(path, *names):
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    out = []
    for name in names:
        out.append(np.array([float(r[name]) if r.get(name) not in ("", None) else np.nan for r in rows]))
    return out


def load_run(aligned) -> dict:
    """Ground truth (t_gt, v_gt), the board's lines (t_st, v_st) and the simulator's notes (meta) for one run."""
    aligned = Path(aligned)
    stem = str(aligned)[: -len("_aligned.csv")] if aligned.name.endswith("_aligned.csv") else str(aligned.with_suffix(""))
    t_gt, v_gt, v_pair = _columns(aligned, "timestamp_epoch", "ground_truth_mps", "student_mps")
    student = Path(stem + "_student.csv")
    if student.exists():
        with open(student, newline="") as f:
            col = "value_raw_mps" if "value_raw_mps" in (csv.DictReader(f).fieldnames or []) else "value_mps"
        t_st, v_st = _columns(student, "timestamp_epoch", col)
    else:
        ok = np.isfinite(v_pair)
        t_st, v_st = t_gt[ok], v_pair[ok]
    meta = Path(stem + "_meta.json")
    return {"t_gt": t_gt, "v_gt": v_gt, "t_st": t_st, "v_st": v_st,
            "meta": json.loads(meta.read_text()) if meta.exists() else {}}


def readings(model, v, v_max: float = 15.0):
    """The reading d behind each streamed speed (NaN where the output sat at 0 or at the clamp)."""
    v = np.asarray(v, float)
    d = model.reading(v)
    return np.where((v > 0.05) & (v < v_max - 0.01), d, np.nan)


def fit_pendulum(t_gt, v_gt, t_st, d_st, arm: float, fit_arm: bool = True, band=demo.SCORED,
                 shifts=np.arange(-0.6, 0.601, 0.02)) -> dict:
    """Best (k, arm, shift) for v_gt(t) ≈ k·√tan θ(d_st(t + shift); arm), least squares on v_gt in the band."""
    t_gt, v_gt = np.asarray(t_gt, float), np.asarray(v_gt, float)
    t_st, d_st = np.asarray(t_st, float), np.asarray(d_st, float)
    ok = np.isfinite(d_st)
    arms = arm * np.linspace(0.6, 1.6, 51) if fit_arm else np.array([arm])
    in_band = (v_gt >= band[0]) & (v_gt <= band[1])
    best = None
    for s in shifts:
        d = np.interp(t_gt + s, t_st[ok], d_st[ok], left=np.nan, right=np.nan)
        keep = in_band & np.isfinite(d)
        if keep.sum() < 50:
            continue
        dd, g = d[keep], v_gt[keep]
        for a in arms:
            w = np.sqrt(np.tan(Pendulum(1.0, a).theta(dd)))
            k = float(np.sum(w * g) / max(float(np.sum(w * w)), 1e-12))   # sums, not @: macOS Accelerate
            e = k * w - g                                                  # warns spuriously in matmul
            mse = float(np.mean(e * e))
            if best is None or mse < best["mse"]:
                best = {"model": Pendulum(k=k, arm_mm=float(a)), "shift": float(s), "mse": mse,
                        "bias": float(e.mean()), "sigma": float(e.std()), "n": int(e.size)}
    if best is None:
        raise ValueError("no ground truth in 2–10 m/s overlapping the board's lines")
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
