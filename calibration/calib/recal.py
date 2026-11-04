"""A saved tunnel run replayed through other calibrations: what the board would have read with each one.

A Wind Tunnel Logger run has the board's speed but not its force. The dashboard recording made over the same minutes
(calibration/runs/run_*.csv, from Record) has the force and ρ at 200 Hz. This module finds that recording, aligns it
to the logger's clock by the board's speed (both saved the same stream: the logger through the dashboard's relay),
and runs the firmware chain (models.apply) on the force with each calibration's settings. Each result is scored
the way the logger scores the board: every ground-truth sample against the nearest calibrated sample within 150 ms.

The zero. The board ran whatever TARE it had; the calibrations take their zero from a fan-off TARE (sqrt_x0 0). So
the force is re-zeroed as a TARE would have done it: the mean over the stillest low-force STILL_S of the recording
(force spread under STILL_SD_N, and the tunnel under STILL_GT_MPS there or not reporting). A recording with no
still air keeps the board's own tare, and says so.

Used by the Results page (tunnel.load / tunnel.window with cal=…) and docs/calibration-2026-11-04.md.
"""
from __future__ import annotations

import csv
import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np

from . import models

SESSIONS = Path(__file__).resolve().parents[1] / "runs"
FS = models.FS
STILL_S = 2.0              # length of the still-air window a TARE would average
STILL_SD_N = 0.006         # force spread allowed in it (about 0.6 g in the board's units)
STILL_GT_MPS = 1.0         # the tunnel must read below this there, where it reports
MIN_OVERLAP_S = 10.0
ALIGN_SEARCH_S = 3.0
ALIGN_STEP_S = 0.005
_cache: dict = {}


def _num(x: str) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return math.nan


def _sessions(sessions_dir: Path) -> list[tuple[Path, dict]]:
    """Dashboard recordings with a force column and a sidecar: (csv, sidecar)."""
    out = []
    for js in sorted(Path(sessions_dir).glob("run_*.json")):
        if js.stem.endswith("_vdas"):          # hand-merged copies, not recordings
            continue
        try:
            side = json.loads(js.read_text())
        except (OSError, ValueError):
            continue
        meta = side.get("meta") or {}
        f = js.with_suffix(".csv")
        if meta.get("mode") != "live" or not f.is_file() or "started" not in meta:
            continue
        out.append((f, side))
    return out


def _span(side: dict) -> tuple[float, float]:
    meta = side["meta"]
    a = datetime.fromisoformat(meta["started"]).timestamp()
    if meta.get("stopped"):
        b = datetime.fromisoformat(meta["stopped"]).timestamp()
    else:
        b = a + (meta.get("rows") or 0) / FS
    return a, b


def find_session(t_first: float, t_last: float, sessions_dir: Path | None = None) -> tuple[Path, dict] | None:
    """The dashboard recording that overlaps [t_first, t_last] (epoch s) the most, if by MIN_OVERLAP_S or more."""
    best, best_ov = None, MIN_OVERLAP_S
    for f, side in _sessions(sessions_dir or SESSIONS):
        a, b = _span(side)
        ov = min(b, t_last) - max(a, t_first)
        if ov >= best_ov:
            best, best_ov = (f, side), ov
    return best


def load_session(f: Path) -> dict:
    with open(f, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    col = lambda k: np.array([_num(r.get(k)) for r in rows], dtype=float)  # noqa: E731
    return {"t": col("t"), "v": col("v_board"), "feature": models._ffill(col("feature")), "rho": col("rho")}


def align_offset(t: np.ndarray, v: np.ndarray, ts: np.ndarray, vs: np.ndarray, guess: float) -> tuple[float, float]:
    """Offset (epoch = t + offset) that best overlays the recording's board speed on the logger's, near guess.
    Returns (offset, rms m/s of the overlay)."""
    if ts.size < 50:
        return guess, math.nan
    grid = np.arange(t[0], t[-1], 0.02)
    a = np.interp(grid, t, np.nan_to_num(v))
    best = (math.inf, guess)
    for d in np.arange(-ALIGN_SEARCH_S, ALIGN_SEARCH_S, ALIGN_STEP_S):
        b = np.interp(grid + guess + d, ts, np.nan_to_num(vs), left=np.nan, right=np.nan)
        m = np.isfinite(b)
        if m.sum() < 500:
            continue
        e = float(np.mean((a[m] - b[m]) ** 2))
        if e < best[0]:
            best = (e, guess + d)
    return best[1], math.sqrt(best[0]) if math.isfinite(best[0]) else math.nan


def still_zero(t: np.ndarray, force: np.ndarray, gt_on_t: np.ndarray) -> dict | None:
    """Where a fan-off TARE would have been taken, and its value: the lowest-force still window."""
    n = int(STILL_S * FS)
    if force.size < n + 100:
        return None
    best = None
    for i in range(100, force.size - n, n // 4):           # skip the recorder's warm-up
        w = force[i:i + n]
        if not np.all(np.isfinite(w)) or np.std(w) > STILL_SD_N:
            continue
        g = gt_on_t[i:i + n]
        g = g[np.isfinite(g)]
        if g.size and np.mean(g) > STILL_GT_MPS:
            continue
        m = float(np.mean(w))
        if best is None or m < best["force"]:
            best = {"force": m, "t_a": float(t[i]), "t_b": float(t[i + n - 1]), "sd": float(np.std(w))}
    return best


def replay(run: dict, profiles: list[dict], sessions_dir: Path | None = None) -> dict | None:
    """run: tunnel._arrays of a logger run (epoch s). Returns None when no recording with force overlaps it, else
    {session, offset, zero, outputs: {name: (epoch t, v)}} (cached per run, recording and calibrations)."""
    t_first = min(a[0] for a in (run["t"], run["ts"], run["tg"]) if a.size)
    t_last = max(a[-1] for a in (run["t"], run["ts"], run["tg"]) if a.size)
    hit = find_session(t_first, t_last, sessions_dir)
    if not hit:
        return None
    f, side = hit
    key = (str(f), f.stat().st_mtime, t_first, json.dumps([p["params"] for p in profiles], sort_keys=True))
    if key in _cache:
        return _cache[key]
    s = load_session(f)
    guess = datetime.fromisoformat(side["meta"]["started"]).timestamp() - float(side["meta"].get("t0", s["t"][0]))
    off, rms = align_offset(s["t"], s["v"], run["ts"], run["vs"], guess)
    te = s["t"] + off
    tg, vg = run["tg"], run["vg"]
    ok = np.isfinite(vg)
    gt_on = np.interp(te, tg[ok], vg[ok], left=np.nan, right=np.nan) if ok.sum() > 1 else np.full(te.shape, np.nan)
    z = still_zero(te, s["feature"], gt_on)
    psets = side.get("param_sets") or []
    flown = {**models.default_params(), **(psets[0]["params"] if psets else {})}
    zero = z["force"] if z else 0.0
    rho = models.density(flown, {"rho": s["rho"]}, len(te))
    outputs = {}
    for p in profiles:
        params = {**flown, **p["params"]}
        outputs[p["name"]] = models.apply(params, {"feature": s["feature"] - zero, "rho": rho})
    out = {"session": str(f), "offset_s": off, "align_rms_mps": rms, "t": te, "v_flown": s["v"],
           "zero": ({"force_n": zero, "grams": zero / 9.80665e-3, "t_a": z["t_a"], "t_b": z["t_b"]} if z else None),
           "flown": {k: flown[k] for k in ("sqrt_k", "sqrt_x0", "filter_kind", "ema_alpha", "lpf_hz", "lead_tau")},
           "outputs": outputs, "labels": {p["name"]: p["label"] for p in profiles}}
    _cache.clear()
    _cache[key] = out
    return out


def score(rep: dict, tg: np.ndarray, vg: np.ndarray) -> dict:
    """Each calibration's score against the tunnel, with the recording's own board output ("as flown") scored on
    the same ground-truth samples (the ones the recording covers), so the numbers compare like for like."""
    from .tunnel import align, analyse

    te = rep["t"]
    ok = np.isfinite(vg) & (tg >= te[0]) & (tg <= te[-1])
    tg, vg = tg[ok], vg[ok]
    out = {}
    for name, v in [("as_flown", rep["v_flown"]), *rep["outputs"].items()]:
        paired = align(tg, vg, te, v)
        a = analyse(tg, vg, paired)
        if rep["zero"]:
            m = (te >= rep["zero"]["t_a"]) & (te <= rep["zero"]["t_b"])
            a["still_air_mps"] = float(np.nanmean(v[m])) if m.any() else None
        low = vg < 2.0
        e = paired - vg
        a["mse_below_2"] = float(np.nanmean(e[low] ** 2)) if np.isfinite(e[low]).any() else None
        out[name] = a
    return {"scores": out, "covered_s": float(tg[-1] - tg[0]) if tg.size else 0.0}
