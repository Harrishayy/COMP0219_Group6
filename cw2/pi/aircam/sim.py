"""The whole chain without a camera, so design choices are measured rather than argued.

  demo profile → turbulent air (rig.wind) → the ball pendulum's nonlinear motion (rig.simulate: drag, straw,
  gravity, inertia; the screen simulator draws the same) → its chord d from rest → camera frames every 1/fps,
  averaged over the exposure, with centroid noise, dropped frames and the odd glint → estimator → a line every
  5 ms → the logger's pairing against a 200 Hz pitot.

It is calibrated the way the real one is: one run with the default map (protocol order 12), `fit.fit_pendulum`
on what the logger would have recorded, then a scored run in the other order (21) with fresh turbulence. The rig's
parameters are hidden from the estimator and the fit, and the pluck test gives the matched filter its fn and ζ.

Every number here is a guess until the prototype is measured: centroid noise (setup prints the still-air jitter),
latency (`run` prints it), fn and ζ (`aircam pluck`). Change them on the `aircam sim` command line.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from . import demo, estimator, fit, rig
from .models import Pendulum


@dataclass
class SimConfig:
    rig: int = 1                    # seed of the hidden rig (straw length, masses, Cd, turbulence)
    seed: int = 1                   # turbulence and camera noise
    turb: float = -1.0              # turbulence intensity at the ball (σ of v / v); negative keeps the rig's
    # camera
    fps: float = 120.0
    exposure: float = 0.003         # s
    latency: float = 0.012          # s from the end of exposure to the reading reaching the estimator
    sigma_px: float = 0.05          # centroid noise per frame, px
    mm_per_px: float = 0.25         # 40 mm ball at 80 px across
    drop_prob: float = 0.01
    glint_prob: float = 0.002       # a frame whose reading is off by glint_mm (a reflection, a half-drawn frame)
    glint_mm: float = 5.0
    # output and logger
    tick_hz: float = 200.0
    transport: float = 0.002        # s, serial + USB to the laptop
    gt_rate: float = 200.0          # the Dwyer 616KD is sampled at 200 Hz (brief)
    gt_sigma: float = 0.05          # m/s
    # demo
    full: float = 9.0
    sigma_y: float = 0.3            # the Kalman filter's reading noise, mm
    q_v: float = 30.0


def chord_mm(theta, r: rig.Rig):
    return 2000.0 * r.length * np.sin(np.abs(theta) / 2.0)


def frames(t, d, cfg: SimConfig, rng):
    """(mid-exposure time, arrival time, reading) for every frame that is not dropped."""
    start = np.arange(t[0], t[-1] - cfg.exposure - cfg.latency, 1.0 / cfg.fps)
    c = np.concatenate(([0.0], np.cumsum(d) * (t[1] - t[0])))      # ∫d, for the exposure average
    tc = np.concatenate((t, [t[-1] + (t[1] - t[0])]))
    blur = (np.interp(start + cfg.exposure, tc, c) - np.interp(start, tc, c)) / cfg.exposure
    reading = blur + rng.normal(0.0, cfg.sigma_px * cfg.mm_per_px, start.size)
    glint = rng.random(start.size) < cfg.glint_prob
    reading[glint] += cfg.glint_mm * rng.choice([-1.0, 1.0], glint.sum())
    keep = rng.random(start.size) >= cfg.drop_prob
    return start[keep] + cfg.exposure / 2, start[keep] + cfg.exposure + cfg.latency, reading[keep]


def make_estimator(name: str, model, cfg: SimConfig, fn=None, zeta=None):
    if name == "kalman-matched":            # the filter knows the ball's dynamics, from the pluck test
        return estimator.make("kalman", model, 1.0 / cfg.fps, sigma_y=cfg.sigma_y, q_v=2.0, instrument="second",
                              fn=fn, zeta=zeta)
    if name == "kalman":
        return estimator.make("kalman", model, 1.0 / cfg.fps, sigma_y=cfg.sigma_y, q_v=cfg.q_v)
    return estimator.make(name, model, 1.0 / cfg.fps)


def the_rig(cfg: SimConfig) -> rig.Rig:
    r = rig.Rig.from_seed(cfg.rig)
    return r if cfg.turb < 0 else replace(r, turb=cfg.turb)


def demo_run(cfg: SimConfig, model, est_name: str, order: str, seed: int, fn=None, zeta=None) -> dict:
    """One simulated demo: the board's lines (arrival time, m/s) and the pitot (time, m/s). The turbulence shakes
    the ball but is not in the pitot's reading: the pitot is elsewhere in the test section."""
    r = the_rig(cfg)
    rng = np.random.default_rng(seed)
    t, v = demo.profile(full=cfg.full, order=order)
    air = rig.wind(t, v, r, seed)
    d = chord_mm(rig.simulate(t, air, r), r)
    tm, ta, y = frames(t, d, cfg, rng)
    e = make_estimator(est_name, model, cfg, fn, zeta)
    ticks = np.arange(t[0], t[-1] - 0.2, 1.0 / cfg.tick_hz)
    out = np.empty_like(ticks)
    j = 0
    for i, tk in enumerate(ticks):
        while j < ta.size and ta[j] <= tk:
            e.update(tm[j], y[j])
            j += 1
        out[i] = e.value(tk)
    tg, vg = demo.ground_truth(t, v, cfg.gt_rate, 0.0, cfg.gt_sigma, seed=seed + 1)    # the pitot: mean flow
    return {"t_st": ticks + cfg.transport, "v_st": out, "t_gt": tg, "v_gt": vg, "t": t, "v": v,
            "frames": int(y.size)}


def pluck_test(cfg: SimConfig) -> dict:
    """The pluck test on the simulated rig: let go from 20° in still air, fit fn and ζ to the sideways offset."""
    r = the_rig(cfg)
    t = np.arange(0.0, 12.0, 0.001)
    th = rig.simulate(t, np.zeros_like(t), r, theta0=np.radians(20.0))
    x = 1000.0 * r.length * np.sin(th)
    k = slice(None, None, max(1, int(round(1.0 / (cfg.fps * 0.001)))))
    return fit.pluck(t[k], x[k])


def calibrate(cfg: SimConfig, est_name: str = "kalman", fn=None, zeta=None):
    """Fit the map from a calibration run made with the default map, as on the day."""
    old = Pendulum()
    r = demo_run(cfg, old, est_name, "12", cfg.seed + 100, fn, zeta)
    res = fit.fit_pendulum(r["t_gt"], r["v_gt"], r["t_st"], fit.readings(old, r["v_st"]), arm=old.arm_mm)
    return res["model"]


def run(cfg: SimConfig = SimConfig(), est: str = "kalman", model=None) -> dict:
    p = pluck_test(cfg) if est == "kalman-matched" else {"fn": None, "zeta": None}
    model = model or calibrate(cfg, est, p["fn"], p["zeta"])
    r = demo_run(cfg, model, est, "21", cfg.seed, p["fn"], p["zeta"])
    logger = demo.score(r["t_gt"], r["v_gt"], r["t_st"], r["v_st"])
    truth = demo.score(r["t"][::5], r["v"][::5], r["t_st"], r["v_st"])       # against a perfect, instant pitot
    return {"estimator": est, "fps": cfg.fps, "logger": logger, "truth": truth, "frames": r["frames"],
            "k": model.k, "arm": model.arm_mm, "t": r["t_st"], "v": r["v_st"]}


def compare(cfg: SimConfig = SimConfig(), names=("hold", "linear", "kalman", "kalman-matched"), fps=(30, 60, 120)):
    """MSE (logger pairing, 2–10 m/s) for each estimator at each frame rate, each calibrated as on the day."""
    rows = []
    for f in fps:
        c = replace(cfg, fps=float(f))
        for n in names:
            r = run(c, n)
            rows.append({"fps": f, "estimator": n, "mse": r["logger"]["mse"], "bias": r["logger"]["bias"],
                         "sigma": r["logger"]["sigma"], "true_mse": r["truth"]["mse"]})
    return rows
