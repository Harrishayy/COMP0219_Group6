"""Accuracy metrics used by the assessment (MSE, bias, sigma) and log alignment."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class ErrorStats:
    n: int
    mse: float
    rmse: float
    bias: float  # mean(estimate - truth)
    sigma: float  # std of the error around the bias
    max_abs: float

    def __str__(self) -> str:
        return (
            f"n={self.n}  MSE={self.mse:.4f} (m/s)^2  RMSE={self.rmse:.4f} m/s  "
            f"bias={self.bias:+.4f} m/s  sigma={self.sigma:.4f} m/s  max|e|={self.max_abs:.3f} m/s"
        )


def error_stats(estimate, truth) -> ErrorStats:
    e = np.asarray(estimate, float) - np.asarray(truth, float)
    e = e[np.isfinite(e)]
    if e.size == 0:
        raise ValueError("no overlapping finite samples")
    mse = float(np.mean(e**2))
    return ErrorStats(
        n=int(e.size),
        mse=mse,
        rmse=float(np.sqrt(mse)),
        bias=float(np.mean(e)),
        sigma=float(np.std(e)),
        max_abs=float(np.max(np.abs(e))),
    )


def align(t_est, v_est, t_ref, v_ref, lag: float = 0.0):
    """Interpolate the reference onto the estimate's timestamps (shifted by lag seconds).

    Returns (t, v_est, v_ref_interp) restricted to the overlapping time span.
    """
    t_est = np.asarray(t_est, float) + lag
    t_ref = np.asarray(t_ref, float)
    order = np.argsort(t_ref)
    t_ref, v_ref = t_ref[order], np.asarray(v_ref, float)[order]
    mask = (t_est >= t_ref[0]) & (t_est <= t_ref[-1])
    return t_est[mask], np.asarray(v_est, float)[mask], np.interp(t_est[mask], t_ref, v_ref)


def best_lag(t_est, v_est, t_ref, v_ref, max_lag: float = 1.0, step: float = 0.005) -> float:
    """Lag (s) that minimises MSE: separates timing offset from genuine estimator error."""
    best, best_mse = 0.0, np.inf
    for lag in np.arange(-max_lag, max_lag + step / 2, step):
        _, e, r = align(t_est, v_est, t_ref, v_ref, lag)
        if e.size < 10:
            continue
        mse = float(np.mean((e - r) ** 2))
        if mse < best_mse:
            best, best_mse = float(lag), mse
    return best


def load_csv(path: str | Path) -> dict[str, np.ndarray]:
    """Load a CSV with a header row into {column: array}. '#' lines are skipped."""
    with open(path, newline="") as f:
        rows = [r for r in csv.reader(f) if r and not r[0].startswith("#")]
    header, body = rows[0], rows[1:]
    cols = {name: [] for name in header}
    for row in body:
        if len(row) != len(header):
            continue
        for name, val in zip(header, row):
            try:
                cols[name].append(float(val))
            except ValueError:
                cols[name].append(np.nan)
    return {k: np.asarray(v) for k, v in cols.items()}
