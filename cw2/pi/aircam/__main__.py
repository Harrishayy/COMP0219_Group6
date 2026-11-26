"""aircam: the camera airspeed sensor (a ping pong ball pendulum, tracked by the Pi's camera).

On the Pi (the config is ~/aircam.json unless --config or $AIRCAM_CONFIG says otherwise):
  setup      fan off: find the ball and save where it rests (run it whenever the camera or the rig moves)
  preview    a window with the tracked ball and the live speed (Pi desktop or Pi Connect screen sharing)
  run        stream the speed at 200 Hz to the serial port in the config (--udp HOST streams to the laptop's
             screen simulator instead); prints the speed once a second; Ctrl+C stops
  pluck      deflect the ball in still air and let go: its natural frequency and damping
  cal        print the calibration file (the brief's "calibration file on demand")
  set        change config values: aircam set k=9.1 arm=148 (aliases: k arm port exposure gain fps sigma q)

On the laptop:
  screen     the screen simulator: the pendulum on this screen for the Pi's camera, scored like the logger
  fit        fit the map from a recorded run (<run>_aligned.csv from the logger or the screen simulator)
  sim        the whole chain simulated without a camera, scored on the graded demo
  compare    estimators x frame rates on the simulated demo, as a Markdown table
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import fields, replace
from pathlib import Path

ALIASES = {"k": "map.k", "arm": "map.arm_mm", "port": "output.port", "exposure": "camera.exposure_us",
           "gain": "camera.gain", "fps": "camera.fps", "sigma": "estimator.sigma_y", "q": "estimator.q_v"}
DEFAULT_CONFIG = os.environ.get("AIRCAM_CONFIG", "~/aircam.json")


def _config(a, create: bool = False) -> tuple[Path, dict]:
    from . import app
    path = Path(a.config).expanduser()
    if not path.exists():
        if not create:
            raise SystemExit(f"no config at {path}: run `aircam setup` first (fan off)")
        shutil.copy(app.EXAMPLE, path)
        print(f"created {path} from {app.EXAMPLE.name}")
    return path, app.load(path)


def cmd_setup(a):
    from . import app
    path, cfg = _config(a, create=True)
    near = tuple(float(v) for v in a.near.split(",")) if a.near else None
    app.setup(cfg, path, dark=a.dark, near=near)


def cmd_preview(a):
    from . import app
    app.preview(_config(a)[1])


def cmd_run(a):
    from . import app
    port = f"udp:{a.udp if ':' in a.udp else a.udp + ':5005'}" if a.udp else a.port
    app.run(_config(a)[1], port, a.record, a.seconds)


def cmd_pluck(a):
    import numpy as np
    from . import app, fit
    path, cfg = _config(a)
    print(f"recording {a.seconds:g} s: pull the ball ~20° to one side and let go now")
    t, x = app.record(cfg, a.seconds)
    ok = np.isfinite(x)
    r = fit.pluck(t[ok], x[ok])
    print(f"fn {r['fn']:.3f} Hz, zeta {r['zeta']:.3f} ({r['peaks']} peaks, ball seen in {100 * ok.mean():.0f}% "
          "of frames)")
    if a.write:
        cfg.setdefault("estimator", {}).update(fn=round(r["fn"], 4), zeta=round(r["zeta"], 4))
        app.save(path, cfg)
        print(f"wrote fn and zeta into {path} (used when estimator.instrument is \"second\")")


def cmd_cal(a):
    print(_config(a)[0].read_text(), end="")


def cmd_set(a):
    from . import app
    path, cfg = _config(a)
    for item in a.pairs:
        key, sep, raw = item.partition("=")
        if not sep:
            raise SystemExit(f"expected key=value, got {item!r}")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        *parents, leaf = ALIASES.get(key, key).split(".")
        node = cfg
        for p in parents:
            node = node.setdefault(p, {})
        print(f"{'.'.join(parents + [leaf])}: {node.get(leaf)!r} -> {value!r}")
        node[leaf] = value
    app.save(path, cfg)


def cmd_fit(a):
    import numpy as np
    from . import demo, fit, models
    run = fit.load_run(a.aligned)
    pi = run["meta"].get("aircam") or {}           # the Pi's "# aircam k=… arm=…" lines, kept by `aircam screen`
    k, arm = a.k or pi.get("k"), a.arm or pi.get("arm")
    if k is None or arm is None:
        cfg_path = Path(a.config).expanduser()
        if cfg_path.exists():
            m = json.loads(cfg_path.read_text())["map"]
            k, arm = k or m["k"], arm or m["arm_mm"]
    if k is None or arm is None:
        raise SystemExit("which map did the run use? Pass --k and --arm (the Pi prints them when `run` starts; "
                         "`aircam cal` shows them)")
    old = models.Pendulum(k=float(k), arm_mm=float(arm))
    d = fit.readings(old, run["v_st"])
    r = fit.fit_pendulum(run["t_gt"], run["v_gt"], run["t_st"], d, arm=old.arm_mm, fit_arm=not a.pin_arm)
    new = r["model"]
    before = demo.score(run["t_gt"], run["v_gt"], run["t_st"], run["v_st"])
    v_new = np.where(np.isfinite(d), new.speed(np.nan_to_num(d)), run["v_st"])   # 0 and the clamp stay
    after = demo.score(run["t_gt"], run["v_gt"], run["t_st"], v_new)
    print(f"ran with   k {old.k:.4g} m/s, arm {old.arm_mm:.4g} mm: logger MSE {before['mse']:.4f} "
          f"(bias {before['bias']:+.3f}, σ {before['sigma']:.3f}, n {before['n']})")
    print(f"fitted     k {new.k:.4g} m/s, arm {new.arm_mm:.4g} mm: logger MSE {after['mse']:.4f} "
          f"(bias {after['bias']:+.3f}, σ {after['sigma']:.3f}); best shift {r['shift']:+.2f} s, "
          f"MSE {r['mse']:.4f} there")
    print(f"on the Pi:  aircam set k={new.k:.4f} arm={new.arm_mm:.2f}")
    if a.write:
        from . import app
        path = Path(a.write).expanduser()
        cfg = app.load(path)
        cfg["map"] = models.to_dict(new)
        app.save(path, cfg)
        print(f"wrote the map into {path}")


def _sim_cfg(a):
    from . import sim
    over = {f.name: getattr(a, f.name) for f in fields(sim.SimConfig) if getattr(a, f.name, None) is not None}
    return replace(sim.SimConfig(), **over)


def _add_sim_args(p):
    from . import sim
    for f in fields(sim.SimConfig):
        if isinstance(f.default, (int, float, str)):
            p.add_argument("--" + f.name.replace("_", "-"), dest=f.name, type=type(f.default), default=None)


def cmd_sim(a):
    from . import sim
    r = sim.run(_sim_cfg(a), a.est)
    for key in ("logger", "truth"):
        s = r[key]
        print(f"{key:>6}: MSE {s['mse']:.4f}  bias {s['bias']:+.3f}  σ {s['sigma']:.3f}  (n {s['n']}, 2–10 m/s)")
    print(f"frames {r['frames']} at {r['fps']:g} fps, estimator {r['estimator']}; calibrated k {r['k']:.4g}, "
          f"arm {r['arm']:.4g}")


def cmd_compare(a):
    from . import sim
    rows = sim.compare(_sim_cfg(a), fps=[float(x) for x in a.rates.split(",")])
    print("| fps | estimator | MSE (logger) | bias | σ | MSE (true speed) |")
    print("| --- | --- | --- | --- | --- | --- |")
    for r in rows:
        print(f"| {r['fps']:g} | {r['estimator']} | {r['mse']:.4f} | {r['bias']:+.3f} | {r['sigma']:.3f} | "
              f"{r['true_mse']:.4f} |")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["screen"]:
        from . import screen
        return screen.main(argv[1:])
    ap = argparse.ArgumentParser(prog="aircam", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")

    def command(name, fn, help_):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--config", default=DEFAULT_CONFIG, help=f"config file (default {DEFAULT_CONFIG})")
        p.set_defaults(fn_=fn)
        return p

    p = command("setup", cmd_setup, "fan off: find the ball and save where it rests")
    p.add_argument("--dark", action="store_true", help="the ball is darker than its background")
    p.add_argument("--near", help="X,Y: pick the round blob nearest this pixel instead of the biggest")
    command("preview", cmd_preview, "window with the tracked ball and the live speed")
    p = command("run", cmd_run, "stream the speed at 200 Hz")
    p.add_argument("--port", help="serial device, '-' for stdout, or udp:HOST:PORT (default: output.port)")
    p.add_argument("--udp", help="HOST[:PORT] of the laptop running `aircam screen` (port 5005 by default)")
    p.add_argument("--record", help="directory to save every frame's reading and every line in")
    p.add_argument("--seconds", type=float, help="stop after this long")
    p = command("pluck", cmd_pluck, "natural frequency and damping from a free decay")
    p.add_argument("--seconds", type=float, default=12.0)
    p.add_argument("--write", action="store_true", help="save fn and zeta in the config")
    command("cal", cmd_cal, "print the calibration file")
    p = command("set", cmd_set, "change config values: aircam set k=9.1 arm=148")
    p.add_argument("pairs", nargs="+", metavar="key=value")
    p = command("fit", cmd_fit, "fit the map from a recorded run's _aligned.csv")
    p.add_argument("aligned", help="<run>_aligned.csv from the Wind Tunnel Logger or `aircam screen`")
    p.add_argument("--k", type=float, help="the k the run used (read from the simulator's notes if it has them)")
    p.add_argument("--arm", type=float, help="the arm the run used")
    p.add_argument("--pin-arm", action="store_true", help="keep the arm, fit k only")
    p.add_argument("--write", help="config file to write the fitted map into")
    simulating = argv[:1] in (["sim"], ["compare"])      # only then import the simulator and its flags
    p = sub.add_parser("sim", help="the chain simulated without a camera")
    if simulating:
        _add_sim_args(p)
    p.add_argument("--est", default="kalman", choices=["hold", "linear", "kalman", "kalman-matched"])
    p.set_defaults(fn_=cmd_sim)
    p = sub.add_parser("compare", help="estimators x frame rates, simulated")
    if simulating:
        _add_sim_args(p)
    p.add_argument("--rates", default="30,60,120", help="frame rates to compare, comma-separated")
    p.set_defaults(fn_=cmd_compare)
    sub.add_parser("screen", help="the screen simulator (laptop): aircam screen --help")
    a = ap.parse_args(argv)
    a.fn_(a)


if __name__ == "__main__":
    sys.exit(main())
