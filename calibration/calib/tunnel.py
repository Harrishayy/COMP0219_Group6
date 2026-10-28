"""Runs saved by the course's Wind Tunnel Logger (tools/wind-tunnel-logger): the board's speed against the tunnel's
ground truth, overlaid and scored, with the drift of the error over the run.

A run is <name>_aligned.csv (each ground-truth sample paired with the nearest board sample within 150 ms; the file
the module's Score.py reads), <name>_vdas.csv (every ground-truth sample) and <name>_student.csv (every board sample,
200 Hz). The logger in this repo saves them in data/tunnel/; a copy installed with pip saves them in
~/Documents/Wind Tunnel Logger/logs, which is searched too.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
RUN_DIRS = (REPO / "data" / "tunnel", Path.home() / "Documents" / "Wind Tunnel Logger" / "logs")
SUFFIXES = ("_aligned", "_vdas", "_student")
DRIFT_WINDOW_S = 10.0      # the error's moving mean, to show drift under the ground truth's 1 Pa steps
PLOT_POINTS = 4000         # most points per series sent to the dashboard


def _base(path: Path) -> str:
    stem = path.stem
    for s in SUFFIXES:
        if stem.endswith(s):
            return stem[: -len(s)]
    return stem


def list_runs(dirs=RUN_DIRS) -> list[dict]:
    """Every saved run, newest first: {name, dir, files, mtime}. A name found in two folders lists both."""
    runs = {}
    for d in dirs:
        if not Path(d).is_dir():
            continue
        for f in Path(d).glob("*.csv"):
            if not any(f.stem.endswith(s) for s in SUFFIXES):
                continue
            key = (str(d), _base(f))
            r = runs.setdefault(key, {"name": _base(f), "dir": str(d), "files": [], "mtime": 0.0})
            r["files"].append(f.stem[len(r["name"]):].lstrip("_"))
            r["mtime"] = max(r["mtime"], f.stat().st_mtime)
    return sorted(runs.values(), key=lambda r: -r["mtime"])


def find(name: str | None, dirs=RUN_DIRS) -> Path:
    """The run's base path (folder/name, no suffix). None: the newest run. A path to any of its files works too."""
    if name:
        p = Path(name).expanduser()
        if p.suffix == ".csv" or p.parent != Path("."):
            return p.with_name(_base(p))
        for r in list_runs(dirs):
            if r["name"] == name:
                return Path(r["dir"]) / r["name"]
        raise FileNotFoundError(f"no Wind Tunnel Logger run named {name!r} in {', '.join(map(str, dirs))}")
    runs = list_runs(dirs)
    if not runs:
        raise FileNotFoundError(f"no Wind Tunnel Logger runs in {', '.join(map(str, dirs))}")
    return Path(runs[0]["dir"]) / runs[0]["name"]


def _columns(path: Path, *cols) -> list[np.ndarray]:
    if not path.exists():
        return [np.array([]) for _ in cols]
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    def num(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return math.nan

    return [np.array([num(r.get(c)) for r in rows], dtype=float) for c in cols]


def _clean(a) -> list:
    return [None if not math.isfinite(x) else round(float(x), 5) for x in a]


def analyse(t: np.ndarray, gt: np.ndarray, board: np.ndarray) -> dict:
    """Score the paired samples (as the brief does: MSE against the ground truth) and measure drift: the slope of
    the error over the run, and the mean error in its first and last third."""
    ok = np.isfinite(gt) & np.isfinite(board)
    n_pairs = int(ok.sum())
    out = {"pairs": n_pairs, "gt_samples": int(np.isfinite(gt).sum()),
           "missing": int(np.isfinite(gt).sum() - n_pairs)}
    if n_pairs < 3:
        return out
    tt, g, b = t[ok] - t[ok][0], gt[ok], board[ok]
    e = b - g
    out.update(mse=float(np.mean(e * e)), rmse=float(np.sqrt(np.mean(e * e))), bias=float(np.mean(e)),
               sigma=float(np.std(e)), duration_s=float(tt[-1]), gt_mean=float(np.mean(g)), board_mean=float(np.mean(b)))
    if tt[-1] > 0:
        slope = np.polyfit(tt, e, 1)[0]
        third = tt[-1] / 3
        first, last = e[tt <= third], e[tt >= 2 * third]
        out.update(drift_per_min=float(slope * 60),
                   bias_first=float(first.mean()) if first.size else math.nan,
                   bias_last=float(last.mean()) if last.size else math.nan)
    # A constant percentage error at every speed is the drag area: v_board² = v_true² · (k_board / k_true), so the
    # least-squares ratio of the squares is what to multiply sqrt_k by (if the board ran the settings it has now).
    fast = g > 2.0
    if fast.sum() >= 3 and np.any(b[fast] > 0):
        out["k_scale"] = float(np.sum(g[fast] ** 2 * b[fast] ** 2) / np.sum(b[fast] ** 4))
    bands = []
    for lo in range(2, 11):
        m = (g >= lo) & (g < lo + 1)
        if m.sum() >= 5:
            bands.append({"lo": lo, "n": int(m.sum()), "bias": float(e[m].mean()), "sigma": float(e[m].std())})
    out["bands"] = bands
    return out


def load(name: str | None = None, dirs=RUN_DIRS, series: bool = True) -> dict:
    """A run's score, drift and (series=True) its traces for plotting, with t in seconds from the run's start."""
    base = find(name, dirs)
    t, gt, st = _columns(base.with_name(base.name + "_aligned.csv"), "timestamp_epoch", "ground_truth_mps", "student_mps")
    ts, vs = _columns(base.with_name(base.name + "_student.csv"), "timestamp_epoch", "value_raw_mps")
    tg, vg = _columns(base.with_name(base.name + "_vdas.csv"), "timestamp_epoch", "value_raw_mps")
    if not t.size and not ts.size:
        raise FileNotFoundError(f"{base}: no _aligned.csv or _student.csv")
    starts = [a[0] for a in (t, ts, tg) if a.size]
    t0 = min(starts)
    out = {"name": base.name, "dir": str(base.parent), "t0_epoch": float(t0), **analyse(t, gt, st)}
    if ts.size > 1 and ts[-1] > ts[0]:
        # samples over time: the logger stamps a whole serial chunk at once, so gaps between samples mean nothing
        out["board_rate_hz"] = float((ts.size - 1) / (ts[-1] - ts[0]))
        out["board_samples"] = int(ts.size)
    if tg.size > 1:
        out["gt_rate_hz"] = float((tg.size - 1) / (tg[-1] - tg[0])) if tg[-1] > tg[0] else math.nan
        out["gt_device"] = _device(base)
    if series:
        out["series"] = _series(t0, t, gt, st, ts, vs, tg, vg)
    return out


def _series(t0, t, gt, st, ts, vs, tg, vg) -> dict:
    """Board, ground truth and the error's moving mean on one time grid (s from the run's start), for one chart.
    The board is averaged per grid step; the ground truth holds its last sample, as the tunnel reports it."""
    ends = [a[-1] for a in (t, ts, tg) if a.size]
    dur = max(ends) - t0
    grid = np.linspace(0.0, dur, int(min(PLOT_POINTS, max(2, dur * 20))))
    step = grid[1] - grid[0] if grid.size > 1 else 1.0

    def binned(tx, vx):
        if not tx.size:
            return np.full(grid.shape, np.nan)
        k = np.clip(np.round((tx - t0) / step).astype(int), 0, grid.size - 1)
        ok = np.isfinite(vx)
        n = np.bincount(k[ok], minlength=grid.size)
        total = np.bincount(k[ok], weights=vx[ok], minlength=grid.size)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(n > 0, total / n, np.nan)

    def held(tx, vx):
        if not tx.size:
            return np.full(grid.shape, np.nan)
        i = np.searchsorted(tx - t0, grid, side="right") - 1
        return np.where(i >= 0, vx[np.clip(i, 0, None)], np.nan)

    board = binned(ts, vs) if ts.size else binned(t, st)
    truth = held(tg, vg) if tg.size else held(t, gt)
    # moving mean of the paired error over DRIFT_WINDOW_S, sampled on the grid; gaps where no pair is near
    ok = np.isfinite(gt) & np.isfinite(st)
    drift = np.full(grid.shape, np.nan)
    if ok.sum() >= 2:
        tk, ek = t[ok] - t0, st[ok] - gt[ok]
        c = np.concatenate([[0.0], np.cumsum(ek)])
        j1 = np.searchsorted(tk, grid, side="right")
        j0 = np.searchsorted(tk, grid - DRIFT_WINDOW_S, side="left")
        n = j1 - j0
        with np.errstate(invalid="ignore", divide="ignore"):
            drift = np.where(n >= 2, (c[j1] - c[j0]) / n, np.nan)
    return {"t": _clean(grid), "board": _clean(board), "gt": _clean(truth), "error_mean": _clean(drift),
            "window_s": DRIFT_WINDOW_S}


def as_run(path):
    """A logger run as a calib.data.Run, so the dashboard can replay it through its calibration views (score,
    accuracy, error by speed, diagnosis). Board samples become the `v` channel, the tunnel's samples the ground
    truth. There is no force column: views and fits that need it say so. The logger stamps each serial chunk on
    arrival, so the board's samples are re-spaced evenly between its first and last (it sends at a steady rate)."""
    from .data import Run

    base = find(str(path))
    ts, vs = _columns(base.with_name(base.name + "_student.csv"), "timestamp_epoch", "value_raw_mps")
    tg, vg = _columns(base.with_name(base.name + "_vdas.csv"), "timestamp_epoch", "value_raw_mps")
    if not ts.size or not tg.size:  # only the aligned file: pairs at the tunnel's rate
        ta, ga, sa = _columns(base.with_name(base.name + "_aligned.csv"), "timestamp_epoch", "ground_truth_mps", "student_mps")
        ts, vs = (ts, vs) if ts.size else (ta[np.isfinite(sa)], sa[np.isfinite(sa)])
        tg, vg = (tg, vg) if tg.size else (ta, ga)
    if ts.size > 1:
        ts = np.linspace(ts[0], ts[-1], ts.size)
    t0 = min(a[0] for a in (ts, tg) if a.size)
    ok = np.isfinite(vg)
    return Run(ts - t0, {"v": vs}, tg[ok] - t0, vg[ok], {"source": str(base), "format": "wind_tunnel_logger"})


def _device(base: Path) -> str:
    f = base.with_name(base.name + "_vdas.csv")
    try:
        with open(f, newline="", encoding="utf-8") as fh:
            return next(csv.DictReader(fh), {}).get("port") or "VDAS"
    except OSError:
        return "VDAS"


def summary_lines(r: dict) -> list[str]:
    """The run's score and drift as plain text (for `calib tunnel`)."""
    f = lambda x, d=3: "–" if x is None or not math.isfinite(x) else f"{x:+.{d}f}"  # noqa: E731
    lines = [f"{r['name']}  ({r['dir']})"]
    if "mse" not in r:
        return lines + [f"  only {r['pairs']} paired samples: nothing to score"]
    lines += [
        f"  {r['duration_s']:.0f} s, {r['pairs']} pairs ({r['missing']} ground-truth samples with no board sample)"
        + (f", board {r['board_rate_hz']:.0f} Hz" if "board_rate_hz" in r else ""),
        f"  MSE {r['mse']:.4f} m²/s²   RMSE {r['rmse']:.3f} m/s   bias {f(r['bias'])} m/s   σ {r['sigma']:.3f} m/s",
        f"  drift {f(r.get('drift_per_min', math.nan))} m/s per min   bias first third {f(r.get('bias_first', math.nan))}"
        f", last third {f(r.get('bias_last', math.nan))}",
    ]
    if "k_scale" in r:
        lines.append(f"  speeds above 2 m/s fit best with sqrt_k × {r['k_scale']:.3f} (Cd·A ÷ {r['k_scale']:.3f}),"
                     " if the board ran its current settings")
    for b in r.get("bands", []):
        lines.append(f"    {b['lo']:>2}–{b['lo'] + 1:<2} m/s  n {b['n']:>5}  bias {f(b['bias'])}  σ {b['sigma']:.3f}")
    return lines
