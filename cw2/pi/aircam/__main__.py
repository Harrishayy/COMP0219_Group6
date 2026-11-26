"""aircam: the camera airspeed sensor (a ping pong ball pendulum, tracked by the Pi's camera).

The physical rig, on the Pi (calibration in ~/aircam-real.json, output on the serial port to the logger):
  aircam real detect     1. fan off: find the ping pong ball, show it in colour, confirm it (nothing runs before)
  aircam real view       2. watch the tracking live: a window on the Pi's desktop, or --web in the laptop's browser
  aircam real run        3. stream 200 Hz to the logger; prints the speed once a second; Ctrl+C stops
  aircam real check      10 s of tracking, then a report      aircam real pluck   the ball's swing and damping
  aircam real cal        the calibration file                 aircam real set k=… arm=…   change it

The screen simulation, on the Pi, its camera on the laptop running `aircam screen` (calibration in ~/aircam-sim.json):
  aircam sim detect      1. find the simulated ball, confirm it
  aircam sim view        2. watch the tracking (also feeds the simulator's panel, once run has its address)
  aircam sim run LAPTOP  3. stream to the simulator over Wi-Fi (the address is on its panel; remembered after)
  aircam sim check | pluck | cal | set …

On the laptop:
  aircam screen          the simulator: the pendulum on this screen, DEMO / MANUAL, truth against the Pi
  aircam fit RUN_aligned.csv    calibrate from a recorded run (the logger's, or the simulator's)
  aircam offline [--compare]    the whole chain without a camera, for design choices
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import fields, replace
from pathlib import Path

ALIASES = {"k": "map.k", "arm": "map.arm_mm", "port": "output.port", "exposure": "camera.exposure_us",
           "gain": "camera.gain", "fps": "camera.fps", "sigma": "estimator.sigma_y", "q": "estimator.q_v"}
CONFIGS = {"real": "~/aircam-real.json", "sim": "~/aircam-sim.json"}


def _config(a, create: bool = False) -> tuple[Path, dict]:
    from . import app
    path = Path(a.config).expanduser()
    if not path.exists():
        if not create:
            raise SystemExit(f"no config at {path}: run `aircam {a.target} detect` first (fan off)")
        cfg = app.load(app.EXAMPLE)
        cfg["target"] = a.target
        if a.target == "sim":
            cfg["output"]["port"] = ""                      # the laptop's address, from `aircam sim run LAPTOP`
        app.save(path, cfg)
        print(f"created {path}")
    cfg = app.load(path)
    cfg.setdefault("target", a.target)
    return path, cfg


def cmd_detect(a):
    from . import app
    path, cfg = _config(a, create=True)
    near = tuple(float(v) for v in a.near.split(",")) if a.near else None
    app.detect(cfg, path, near=near, web=a.web, port=a.web_port, yes=a.yes)


def cmd_view(a):
    from . import app
    _, cfg = _config(a)
    port = cfg.get("output", {}).get("port", "")
    udp = port[4:] if a.target == "sim" and port.startswith("udp:") else None   # the simulator's panel, too
    app.view(cfg, web=a.web, port=a.web_port, udp=udp)


def cmd_run(a):
    from . import app, stream
    path, cfg = _config(a)
    port = a.port
    if a.target == "sim":
        if a.laptop:
            cfg["output"]["port"] = stream.udp_port(a.laptop)
            app.save(path, cfg)
        port = port or cfg["output"].get("port")
        if not port:
            raise SystemExit("where is the simulator? aircam sim run LAPTOP_ADDRESS (it is on the simulator's "
                             "panel); it is remembered after the first time")
    app.run(cfg, port, a.record, a.seconds)


def cmd_check(a):
    from . import app
    path, cfg = _config(a)
    print(f"tracking the ball for {a.seconds:g} s (fan off: the camera's noise floor; fan on: how it follows)")
    r = app.check(cfg, a.seconds, image=path.with_name(f"aircam-{a.target}-check.png"))
    print(app.report_text(r))
    if "image" in r:
        print(f"the last frame, with the ball circled and its rest marked: {r['image']}")


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
        print(f"wrote fn and zeta into {path}")


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
    target = "sim" if run["meta"] else "real"      # only the simulator writes _meta.json
    k, arm = a.k or pi.get("k"), a.arm or pi.get("arm")
    if k is None or arm is None:
        raise SystemExit("which map did the run use? Pass --k and --arm (`aircam real cal` on the Pi shows them)")
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
          f"(bias {after['bias']:+.3f}, σ {after['sigma']:.3f}); best shift {r['shift']:+.2f} s")
    print(f"on the Pi:  aircam {target} set k={new.k:.4f} arm={new.arm_mm:.2f}")


def _sim_cfg(a):
    from . import sim
    over = {f.name: getattr(a, f.name) for f in fields(sim.SimConfig) if getattr(a, f.name, None) is not None}
    return replace(sim.SimConfig(), **over)


def cmd_offline(a):
    from . import sim
    if a.compare:
        rows = sim.compare(_sim_cfg(a), fps=[float(x) for x in a.rates.split(",")])
        print("| fps | estimator | MSE (logger) | bias | σ | MSE (true speed) |")
        print("| --- | --- | --- | --- | --- | --- |")
        for r in rows:
            print(f"| {r['fps']:g} | {r['estimator']} | {r['mse']:.4f} | {r['bias']:+.3f} | {r['sigma']:.3f} | "
                  f"{r['true_mse']:.4f} |")
        return
    r = sim.run(_sim_cfg(a), a.est)
    for key in ("logger", "truth"):
        s = r[key]
        print(f"{key:>6}: MSE {s['mse']:.4f}  bias {s['bias']:+.3f}  σ {s['sigma']:.3f}  (n {s['n']}, 2–10 m/s)")
    print(f"frames {r['frames']} at {r['fps']:g} fps, estimator {r['estimator']}; calibrated k {r['k']:.4g}, "
          f"arm {r['arm']:.4g}")


def _target(sub, name: str, help_: str):
    t = sub.add_parser(name, help=help_)
    cmds = t.add_subparsers(dest="cmd", required=True, metavar="command")

    def command(cmd, fn, help_):
        p = cmds.add_parser(cmd, help=help_)
        p.add_argument("--config", default=CONFIGS[name], help=f"calibration file (default {CONFIGS[name]})")
        p.set_defaults(fn_=fn, target=name)
        return p

    p = command("detect", cmd_detect, "1. fan off: find the ball, show it in colour, confirm it")
    p.add_argument("--near", help="X,Y: the ball is near this pixel (when the biggest round thing is not it)")
    p.add_argument("--web", action="store_true", help="show it in the laptop's browser even with a desktop")
    p.add_argument("--web-port", type=int, default=8080)
    p.add_argument("--yes", action="store_true", help="accept the detection without asking")
    p = command("view", cmd_view, "2. the live colour feed with the tracking drawn on it")
    p.add_argument("--web", action="store_true", help="in the laptop's browser instead of a window")
    p.add_argument("--web-port", type=int, default=8080)
    p = command("run", cmd_run, "3. stream the speed at 200 Hz")
    if name == "sim":
        p.add_argument("laptop", nargs="?", help="the simulator's address, from its panel (remembered)")
    else:
        p.set_defaults(laptop=None)
    p.add_argument("--port", help="override: a serial device, '-' for stdout, or udp:HOST:PORT")
    p.add_argument("--record", help="directory to save every frame's reading and every line in")
    p.add_argument("--seconds", type=float, help="stop after this long")
    p = command("check", cmd_check, "10 s of tracking, then a report")
    p.add_argument("--seconds", type=float, default=10.0)
    p = command("pluck", cmd_pluck, "the ball's natural frequency and damping, from a free swing")
    p.add_argument("--seconds", type=float, default=12.0)
    p.add_argument("--write", action="store_true", help="save fn and zeta in the config")
    command("cal", cmd_cal, "print the calibration file")
    p = command("set", cmd_set, "change values: set k=9.1 arm=148 (k arm port exposure gain fps sigma q)")
    p.add_argument("pairs", nargs="+", metavar="key=value")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["screen"]:
        from . import screen
        return screen.main(argv[1:])
    ap = argparse.ArgumentParser(prog="aircam", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="group", required=True, metavar="real | sim | screen | fit | offline")
    _target(sub, "real", "the physical rig (on the Pi)")
    _target(sub, "sim", "the screen simulation (on the Pi, its camera on the laptop's screen)")
    sub.add_parser("screen", help="the simulator display (laptop): aircam screen --help")
    p = sub.add_parser("fit", help="calibrate from a recorded run's _aligned.csv (laptop)")
    p.add_argument("aligned", help="<run>_aligned.csv from the Wind Tunnel Logger or `aircam screen`")
    p.add_argument("--k", type=float, help="the k the run used (the simulator's runs record it)")
    p.add_argument("--arm", type=float, help="the arm the run used")
    p.add_argument("--pin-arm", action="store_true", help="keep the arm, fit k only")
    p.set_defaults(fn_=cmd_fit)
    p = sub.add_parser("offline", help="the whole chain without a camera (laptop design tool)")
    if argv[:1] == ["offline"]:                     # only then import the simulator for its flags
        from . import sim
        for f in fields(sim.SimConfig):
            if isinstance(f.default, (int, float, str)):
                p.add_argument("--" + f.name.replace("_", "-"), dest=f.name, type=type(f.default), default=None)
    p.add_argument("--est", default="kalman", choices=["hold", "linear", "kalman", "kalman-matched"])
    p.add_argument("--compare", action="store_true", help="every estimator at several frame rates")
    p.add_argument("--rates", default="30,60,120")
    p.set_defaults(fn_=cmd_offline)
    a = ap.parse_args(argv)
    a.fn_(a)


if __name__ == "__main__":
    sys.exit(main())
