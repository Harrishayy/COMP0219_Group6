#!/usr/bin/env python3
"""Score a logged run against ground truth: MSE, RMSE, bias, sigma (as in the assessment).

    python host/evaluate.py data/logs/run.csv --truth data/logs/pitot.csv
    python host/evaluate.py run.csv --truth pitot.csv --truth-t time --truth-v velocity --plot

The device log comes from log_serial.py (time column host_time_s, velocity column v).
The truth CSV is whatever the lab's reference logger produces: name its time and
velocity columns with --truth-t / --truth-v. Both clocks must be in seconds; --auto-lag
removes a constant offset between them before scoring and reports it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from airspeed_tools import metrics


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", type=Path)
    ap.add_argument("--truth", type=Path, required=True)
    ap.add_argument("--log-t", default="host_time_s")
    ap.add_argument("--log-v", default="v")
    ap.add_argument("--truth-t", default="t")
    ap.add_argument("--truth-v", default="v")
    ap.add_argument("--lag", type=float, default=0.0, help="seconds added to the device timestamps")
    ap.add_argument("--auto-lag", action="store_true", help="search for the lag that minimises MSE")
    ap.add_argument("--plot", action="store_true")
    args = ap.parse_args()

    log = metrics.load_csv(args.log)
    ref = metrics.load_csv(args.truth)
    for name, table, path in ((args.log_t, log, args.log), (args.log_v, log, args.log),
                              (args.truth_t, ref, args.truth), (args.truth_v, ref, args.truth)):
        if name not in table:
            print(f"column '{name}' not in {path}; has {list(table)}", file=sys.stderr)
            return 1

    t_e, v_e = log[args.log_t], log[args.log_v]
    t_r, v_r = ref[args.truth_t], ref[args.truth_v]
    lag = metrics.best_lag(t_e, v_e, t_r, v_r) if args.auto_lag else args.lag
    t, est, truth = metrics.align(t_e, v_e, t_r, v_r, lag)
    stats = metrics.error_stats(est, truth)
    print(f"lag {lag:+.3f} s")
    print(stats)

    # Per-speed breakdown: where in the 2-10 m/s range does the error come from?
    edges = np.arange(0.0, 12.0, 2.0)
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (truth >= lo) & (truth < hi)
        if m.sum() >= 10:
            s = metrics.error_stats(est[m], truth[m])
            print(f"  {lo:4.1f}-{hi:4.1f} m/s: n={s.n:6d}  RMSE={s.rmse:.3f}  bias={s.bias:+.3f}  sigma={s.sigma:.3f}")

    if args.plot:
        import matplotlib.pyplot as plt

        fig, (a1, a2) = plt.subplots(2, 1, sharex=True, figsize=(10, 6))
        a1.plot(t, truth, label="ground truth", lw=1)
        a1.plot(t, est, label="estimate", lw=1)
        a1.set_ylabel("airspeed (m/s)")
        a1.legend()
        a2.plot(t, est - truth, lw=0.8)
        a2.axhline(stats.bias, ls="--", c="k", lw=0.8)
        a2.set_ylabel("error (m/s)")
        a2.set_xlabel("time (s)")
        a1.set_title(str(stats), fontsize=8)
        fig.tight_layout()
        plt.show()
    return 0


if __name__ == "__main__":
    sys.exit(main())
