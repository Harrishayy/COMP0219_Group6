"""python -m aircam <command>

  sim       simulate the graded demo through one estimator and print its score
  compare   every estimator at several frame rates (--rates 30,60,120), as a Markdown table
  run       the sensor, on the Pi: --config <file> --port /dev/ttyAMA0 (or - for stdout) [--record <dir>]
  cal       print the calibration file the sensor is running from (the brief's "calibration file on demand")
  fit       fit the map from a recorded session and the logger's _aligned.csv, and write it into the config
  pluck     natural frequency and damping from a recorded free decay (a session's frames.csv)
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import fields, replace
from pathlib import Path

import numpy as np

from . import fit, models, sim


def _sim_cfg(a) -> sim.SimConfig:
    cfg = sim.SimConfig()
    over = {f.name: getattr(a, f.name) for f in fields(sim.SimConfig) if getattr(a, f.name, None) is not None}
    return replace(cfg, **over)


def _add_sim_args(p):
    for f in fields(sim.SimConfig):
        if f.name == "model":
            continue
        p.add_argument("--" + f.name.replace("_", "-"), dest=f.name, type=type(f.default), default=None)


def _est_kw(a) -> dict:
    return {k: v for k, v in (("q_v", a.q_v), ("lead", a.lead)) if v is not None}


def cmd_sim(a):
    r = sim.run(_sim_cfg(a), a.est, **_est_kw(a))
    for key in ("logger", "truth"):
        s = r[key]
        print(f"{key:>6}: MSE {s['mse']:.4f}  bias {s['bias']:+.3f}  σ {s['sigma']:.3f}  (n {s['n']}, 2–10 m/s)")
    print(f"frames {r['frames']} at {r['fps']:g} fps, estimator {r['estimator']}")


def cmd_compare(a):
    rows = sim.compare(_sim_cfg(a), fps=[float(x) for x in a.rates.split(",")])
    print("| fps | estimator | MSE (logger) | bias | σ | MSE (true speed) |")
    print("| --- | --- | --- | --- | --- | --- |")
    for r in rows:
        print(f"| {r['fps']:g} | {r['estimator']} | {r['mse']:.4f} | {r['bias']:+.3f} | {r['sigma']:.3f} | {r['true_mse']:.4f} |")


def cmd_run(a):
    from . import app
    app.run(app.load(a.config), a.port, a.record, a.seconds)


def cmd_cal(a):
    print(Path(a.config).read_text())


def _csv(path, *cols):
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    return [np.array([float(r[c]) if r.get(c) not in ("", None) else np.nan for r in rows]) for c in cols]


def cmd_fit(a):
    s = Path(a.session)
    tf, y = _csv(s / "frames.csv", "t", "y")
    tt, vt = _csv(s / "ticks.csv", "t", "v")
    te, gt, st = _csv(a.logger, "timestamp_epoch", "ground_truth_mps", "student_mps")
    d = fit.align_offset(tt, vt, te, st)
    r = fit.fit_power(tf + d, y, te, gt, p=a.p, y0=a.y0)
    m = r["model"]
    print(f"clock offset {d:.3f} s; pitot lag {r['lag']:.2f} s; k {m.k:.4g}, y0 {m.y0:.4g}, p {m.p:g}")
    print(f"fit on 2–10 m/s: MSE {r['mse']:.4f}, bias {r['bias']:+.3f}, σ {r['sigma']:.3f} (n {r['n']})")
    if a.write:
        cfg = json.loads(Path(a.write).read_text())
        cfg["model"] = models.to_dict(m)
        Path(a.write).write_text(json.dumps(cfg, indent=2) + "\n")
        print(f"wrote the map into {a.write}")


def cmd_pluck(a):
    t, y = _csv(Path(a.session) / "frames.csv", "t", "y")
    ok = np.isfinite(y)
    r = fit.pluck(t[ok], y[ok])
    print(f"fn {r['fn']:.3f} Hz, zeta {r['zeta']:.3f} ({r['peaks']} peaks)")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="aircam", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sim")
    _add_sim_args(p)
    p.add_argument("--est", default="kalman", choices=["hold", "linear", "kalman", "kalman-matched"])
    p.add_argument("--q-v", dest="q_v", type=float)
    p.add_argument("--lead", type=float)
    p.set_defaults(fn_=cmd_sim)
    p = sub.add_parser("compare")
    _add_sim_args(p)
    p.add_argument("--rates", default="30,60,120,148", help="frame rates to compare, comma-separated")
    p.set_defaults(fn_=cmd_compare)
    p = sub.add_parser("run")
    p.add_argument("--config", required=True)
    p.add_argument("--port", default="-")
    p.add_argument("--record")
    p.add_argument("--seconds", type=float)
    p.set_defaults(fn_=cmd_run)
    p = sub.add_parser("cal")
    p.add_argument("--config", required=True)
    p.set_defaults(fn_=cmd_cal)
    p = sub.add_parser("fit")
    p.add_argument("session")
    p.add_argument("logger", help="the logger's <run>_aligned.csv")
    p.add_argument("--p", type=float, default=2.0)
    p.add_argument("--y0", type=float, help="pin the still-air reading instead of fitting it")
    p.add_argument("--write", help="config file to write the fitted map into")
    p.set_defaults(fn_=cmd_fit)
    p = sub.add_parser("pluck")
    p.add_argument("session")
    p.set_defaults(fn_=cmd_pluck)
    a = ap.parse_args(argv)
    a.fn_(a)


if __name__ == "__main__":
    sys.exit(main())
