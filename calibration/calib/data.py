"""Run container and CSV I/O.

A run holds two independently timestamped streams (PC `perf_counter` seconds):
the sensor (debug columns from the Nucleo) and the ground truth. The CSV is "long-wide": one row per
sample, `src` = `s` (sensor) or `g` (ground truth), unused columns left empty. Lossless, appendable,
and easy to load in a spreadsheet.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass
class Run:
    t_s: np.ndarray = field(default_factory=lambda: np.zeros(0))
    sensor: dict = field(default_factory=dict)  # channel -> array, same length as t_s
    t_g: np.ndarray = field(default_factory=lambda: np.zeros(0))
    gt: np.ndarray = field(default_factory=lambda: np.zeros(0))
    meta: dict = field(default_factory=dict)

    @property
    def channels(self) -> list[str]:
        return list(self.sensor)

    def window(self, seconds: float | None) -> "Run":
        """Last `seconds` of the run (both streams), or the whole run."""
        if not seconds or (len(self.t_s) == 0 and len(self.t_g) == 0):
            return self
        end = max(self.t_s[-1] if len(self.t_s) else -np.inf, self.t_g[-1] if len(self.t_g) else -np.inf)
        ms, mg = self.t_s >= end - seconds, self.t_g >= end - seconds
        return Run(self.t_s[ms], {k: v[ms] for k, v in self.sensor.items()}, self.t_g[mg], self.gt[mg], dict(self.meta))

    def gt_on_sensor(self) -> np.ndarray:
        """GT linearly interpolated onto sensor timestamps; NaN outside the GT time span."""
        from .metrics import resample

        return resample(self.t_g, self.gt, self.t_s)


def save_csv(run: Run, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    chans = run.channels
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "src", "gt", *chans])
        rows = [(t, 0, i) for i, t in enumerate(run.t_s)] + [(t, 1, i) for i, t in enumerate(run.t_g)]
        rows.sort()
        for t, kind, i in rows:
            if kind == 0:
                w.writerow([f"{t:.6f}", "s", "", *(_fmt(run.sensor[c][i]) for c in chans)])
            else:
                w.writerow([f"{t:.6f}", "g", _fmt(run.gt[i]), *([""] * len(chans))])
    return path


def _fmt(x) -> str:
    x = float(x)
    return "" if not np.isfinite(x) else f"{x:.7g}"


def load_csv(path: str | Path) -> Run:
    t_s, t_g, gt = [], [], []
    with Path(path).open(newline="") as f:
        r = csv.reader(f)
        header = next(r)
        if header[:4] == ["t", "gt", "v_board", "v_pc"]:
            return _load_session_csv(path, header, r)
        if header[:3] != ["t", "src", "gt"]:
            raise ValueError(f"{path}: not a calibration run CSV (header {header[:3]})")
        chans = header[3:]
        cols: dict[str, list] = {c: [] for c in chans}
        for row in r:
            if len(row) < 2:
                continue
            try:
                t = float(row[0])
            except ValueError:
                continue
            if row[1] == "g":
                try:
                    gt.append(float(row[2]))
                    t_g.append(t)
                except ValueError:
                    pass
            elif row[1] == "s":
                t_s.append(t)
                for j, c in enumerate(chans):
                    cell = row[3 + j] if 3 + j < len(row) else ""
                    try:
                        cols[c].append(float(cell))
                    except ValueError:
                        cols[c].append(np.nan)
    return Run(np.asarray(t_s), {c: np.asarray(v) for c, v in cols.items()}, np.asarray(t_g), np.asarray(gt),
               {"source": str(path)})


SESSION_SENSOR_COLS = {"v_board": "v", "feature": "feature", "rho": "rho", "temp_c": "temp_c",
                       "pressure_pa": "pressure_pa", "pset": "pset"}


def _load_session_csv(path, header, reader) -> Run:
    """Session CSV (recorder.Session): rows are already aligned, so GT shares the sensor time base.
    The per-row parameter columns are not loaded; `pset` is kept as a channel (params are in the JSON)."""
    idx = {h: i for i, h in enumerate(header)}
    t, gt = [], []
    cols: dict[str, list] = {dst: [] for src, dst in SESSION_SENSOR_COLS.items() if src in idx}
    for row in reader:
        try:
            tv = float(row[idx["t"]])
        except (ValueError, IndexError):
            continue
        t.append(tv)
        g = row[idx["gt"]] if idx["gt"] < len(row) else ""
        gt.append(float(g) if g else np.nan)
        for src, dst in SESSION_SENSOR_COLS.items():
            if src in idx:
                cell = row[idx[src]] if idx[src] < len(row) else ""
                try:
                    cols[dst].append(float(cell))
                except ValueError:
                    cols[dst].append(np.nan)
    t_arr, gt_arr = np.asarray(t), np.asarray(gt)
    ok = np.isfinite(gt_arr)
    return Run(t_arr, {c: np.asarray(v) for c, v in cols.items()}, t_arr[ok], gt_arr[ok],
               {"source": str(path), "format": "session"})
