"""Simulate a demo run end to end, so design choices are measured rather than argued.

  demo profile (true air speed)
    → instrument: its static reading u = map⁻¹(v), seen through its dynamics (none / first order / second order)
    → camera: a frame every 1/fps, averaged over the exposure (motion blur), with reading noise, dropped
      frames and the odd glint; the estimator gets each reading at mid-exposure time, `latency` after the
      exposure ends (readout, ISP, our processing)
    → estimator updated with every frame that has arrived, asked for a speed at every 200 Hz tick
    → line reaches the laptop `transport` later; the logger pairs it with the pitot (lagged, sampled, noisy)

Every parameter is a guess until the prototype is measured: fn and zeta from a pluck test, sigma_y from a
still-air recording, latency from an LED test. Change them in SimConfig or on the `aircam sim` command line.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np

from . import demo, estimator
from .models import PowerLaw


@dataclass
class SimConfig:
    # instrument
    instrument: str = "second"      # none | first | second
    fn: float = 3.0                 # Hz, second order
    zeta: float = 0.15              # damping ratio, second order
    tau: float = 0.15               # s, first order
    model: PowerLaw = field(default_factory=lambda: PowerLaw(k=14.0, y0=0.0, p=2.0))   # 10 m/s ↔ y 0.51
    sigma_y: float = 0.002          # reading noise per frame (a 0.1° angle, a tenth of a pixel)
    # camera
    fps: float = 120.0
    exposure: float = 0.004         # s
    latency: float = 0.012          # s from end of exposure to the reading reaching the estimator
    drop_prob: float = 0.01
    glint_prob: float = 0.002       # a reading off by glint_size (a reflection, a lost marker)
    glint_size: float = 0.2
    # output and logger
    tick_hz: float = 200.0
    transport: float = 0.002        # s, serial + USB to the laptop
    gt_rate: float = 200.0          # Dwyer 616KD sampled at 200 Hz (brief); the VDAS box is ~4 Hz
    gt_tau: float = 0.0             # s, pitot + transducer lag
    gt_sigma: float = 0.05          # m/s
    # demo
    full: float = 9.0
    order: str = "12"
    seed: int = 1


def instrument_response(t, u, cfg: SimConfig):
    """The reading y(t) the instrument shows when its static-equivalent reading is u(t) (ZOH between samples)."""
    if cfg.instrument == "none":
        return u.copy()
    dt = t[1] - t[0]
    if cfg.instrument == "first":
        a, b = np.array([[-1 / cfg.tau]]), np.array([1 / cfg.tau])
    else:
        w = 2 * np.pi * cfg.fn
        a, b = np.array([[0.0, 1.0], [-w * w, -2 * cfg.zeta * w]]), np.array([0.0, w * w])
    n = a.shape[0]
    m = np.zeros((n + 1, n + 1))
    m[:n, :n], m[:n, n] = a, b
    e = estimator.expm(m * dt)
    f, g = e[:n, :n], e[:n, n]
    x = np.zeros(n)
    x[0] = u[0]
    y = np.empty_like(u)
    for i, ui in enumerate(u):
        y[i] = x[0]
        x = f @ x + g * ui
    return y


def frames(t, y, cfg: SimConfig, rng):
    """(mid-exposure time, arrival time, reading) for every frame that is not dropped."""
    start = np.arange(t[0], t[-1] - cfg.exposure - cfg.latency, 1.0 / cfg.fps)
    c = np.concatenate(([0.0], np.cumsum(y) * (t[1] - t[0])))      # ∫y, for the exposure average
    tc = np.concatenate((t, [t[-1] + (t[1] - t[0])]))
    blur = (np.interp(start + cfg.exposure, tc, c) - np.interp(start, tc, c)) / cfg.exposure
    reading = blur + rng.normal(0.0, cfg.sigma_y, start.size)
    glint = rng.random(start.size) < cfg.glint_prob
    reading[glint] += cfg.glint_size * rng.choice([-1.0, 1.0], glint.sum())
    keep = rng.random(start.size) >= cfg.drop_prob
    return start[keep] + cfg.exposure / 2, start[keep] + cfg.exposure + cfg.latency, reading[keep]


def estimator_for(name: str, cfg: SimConfig, **kw):
    period = 1.0 / cfg.fps
    if name == "kalman-matched":            # the filter knows the instrument's dynamics
        kw = {"instrument": cfg.instrument, "fn": cfg.fn, "zeta": cfg.zeta, "tau": cfg.tau, "q_v": 2.0, **kw}
        name = "kalman"
    if name == "kalman":
        kw = {"sigma_y": cfg.sigma_y, **kw}
    return estimator.make(name, cfg.model, period, **kw)


def run(cfg: SimConfig = SimConfig(), est: str = "kalman-matched", **est_kw) -> dict:
    rng = np.random.default_rng(cfg.seed)
    t, v = demo.profile(full=cfg.full, order=cfg.order)
    y = instrument_response(t, cfg.model.reading(v), cfg)
    tm, ta, r = frames(t, y, cfg, rng)
    e = estimator_for(est, cfg, **est_kw)

    ticks = np.arange(t[0], t[-1] - 0.2, 1.0 / cfg.tick_hz)
    out = np.empty_like(ticks)
    j = 0
    for i, tk in enumerate(ticks):
        while j < ta.size and ta[j] <= tk:
            e.update(tm[j], r[j])
            j += 1
        out[i] = e.value(tk)
    arrive = ticks + cfg.transport

    tg, vg = demo.ground_truth(t, v, cfg.gt_rate, cfg.gt_tau, cfg.gt_sigma, seed=cfg.seed + 1)
    logger = demo.score(tg, vg, arrive, out)
    truth = demo.score(t[::5], v[::5], arrive, out)             # against a perfect, instant pitot
    return {"estimator": est, "fps": cfg.fps, "logger": logger, "truth": truth,
            "frames": int(r.size), "t": arrive, "v": out}


def compare(cfg: SimConfig = SimConfig(), names=("hold", "linear", "kalman", "kalman-matched"), fps=(30, 60, 120, 148)):
    """MSE (logger pairing, 2–10 m/s) for each estimator at each frame rate."""
    rows = []
    for f in fps:
        c = replace(cfg, fps=float(f))
        for n in names:
            r = run(c, n)
            rows.append({"fps": f, "estimator": n, "mse": r["logger"]["mse"], "bias": r["logger"]["bias"],
                         "sigma": r["logger"]["sigma"], "true_mse": r["truth"]["mse"]})
    return rows
