"""End to end through pixels: the screen simulator's own drawing, filmed by a model of the Pi camera pointed at a
laptop screen, into the tracker, the Kalman filter, the fit and the logger's score. Nothing here hands the Pi
side the scene's geometry: setup finds the ball in a filmed still frame, and the map is fitted from a recorded
calibration run, as on the day.

The camera model (colour, as the Pi's RGB888 stream): the laptop redraws at 60 Hz and shows each frame `lag` later; the camera samples at `fps` with
a `exposure` window, so a frame that straddles a refresh is a blend of two (as a rolling shutter catches a screen
mid-update); the view is tilted and scaled (an affine warp with smoothing), slightly defocused, dimmer than the
screen, with shot and read noise, quantised to 8 bits.

The fast tests run by default. The whole demo (a calibration run, then a scored run, ~2 minutes) runs with
AIRCAM_SLOW=1, or from the command line: python tests/test_e2e.py.
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import numpy as np
import pytest

pg = pytest.importorskip("pygame") if __name__ != "__main__" else __import__("pygame")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aircam import demo, estimator, features, fit, rig, screen   # noqa: E402
from aircam.models import Pendulum                               # noqa: E402

SCREEN = (1440, 900)        # a MacBook's logical resolution
CAMERA = (666, 496)         # half the HQ camera's 1332x990 mode, to keep the replay quick


class Film:
    """A camera filming the screen: screen RGB frames in, camera Y planes out."""

    def __init__(self, scene, tilt_deg=1.5, seed=0):
        self.scene, self.tilt = scene, tilt_deg
        self.rng = np.random.default_rng(seed)
        # frame the pendulum: the hinge and the swing fill the camera, the readout strip at the top stays out
        hx, hy = scene.hinge
        span = scene.arm_px + 1.3 * scene.ball_px
        self.zoom = 0.92 * CAMERA[1] / (span * 1.15)
        self.centre = (hx + 0.25 * scene.arm_px, hy + 0.5 * span)
        self.canvas = pg.Surface(SCREEN)

    def screen(self, theta):
        """The screen's pixels for one drawn frame, as the camera's optics see them (before the sensor)."""
        self.scene.draw(self.canvas, theta)
        cx, cy = self.centre
        w, h = CAMERA[0] / self.zoom * 1.1, CAMERA[1] / self.zoom * 1.1
        sub = self.canvas.subsurface(pg.Rect(int(cx - w / 2), int(cy - h / 2), int(w), int(h)))
        img = pg.transform.rotozoom(sub, self.tilt, self.zoom)
        a = pg.surfarray.array3d(img).swapaxes(0, 1).astype(np.float32)
        y0, x0 = (a.shape[0] - CAMERA[1]) // 2, (a.shape[1] - CAMERA[0]) // 2
        a = a[y0:y0 + CAMERA[1], x0:x0 + CAMERA[0]]
        bgr = a[..., ::-1]                                          # the camera gives B, G, R (RGB888)
        return 0.25 * (bgr + np.roll(bgr, 1, 0) + np.roll(bgr, 1, 1) + np.roll(np.roll(bgr, 1, 0), 1, 1))  # defocus

    def sensor(self, optical):
        """Exposure → 8-bit colour: dimmer than the screen, black level, shot and read noise per channel."""
        s = 10.0 + 0.85 * optical
        s = s + self.rng.normal(0.0, 1.0, s.shape) * np.sqrt(2.0 + 0.04 * s)
        return np.clip(np.round(s), 0, 255).astype(np.uint8)


def filmed_frames(t, theta, film, fps=120.0, exposure=0.003, refresh=60.0, lag=0.025):
    """Yield (mid-exposure time, Y plane) for every camera frame over the run t (theta sampled on t)."""
    cache = {}

    def drawn(n):                                   # the screen frame drawn at n/refresh, lit at n/refresh + lag
        if n not in cache:
            cache.clear() if len(cache) > 4 else None
            cache[n] = film.screen(float(np.interp(n / refresh, t, theta)))
        return cache[n]

    for ts in np.arange(t[0] + 0.1, t[-1] - 0.1, 1.0 / fps):
        a, b = ts - exposure, ts                     # exposure window
        n0, n1 = math.floor((a - lag) * refresh), math.floor((b - lag) * refresh)
        if n0 == n1:
            opt = drawn(n0)
        else:                                        # straddles a refresh: a blend of the two frames
            w = ((n1 / refresh + lag) - a) / exposure
            opt = w * drawn(n0) + (1 - w) * drawn(n1)
        yield ts - exposure / 2, film.sensor(opt)


def setup_from_film(film):
    """What `aircam setup` does, on filmed still-air frames."""
    ball, _, _ = features.setup([film.sensor(film.screen(0.0)) for _ in range(15)])
    assert ball is not None, "setup found no ball in the filmed frame"
    return ball


def _scene(seed=1):
    return screen.Scene(*SCREEN, rig.Rig.from_seed(seed))


# -- fast -----------------------------------------------------------------------------------------------------
def test_setup_finds_the_simulated_ball_through_the_camera():
    film = Film(_scene())
    ball = setup_from_film(film)
    assert ball.radius > 10                              # found, and big enough for sub-pixel positions
    assert abs(ball(film.sensor(film.screen(0.0)))) < 0.3   # mm: at rest it reads ~0


def test_tracker_follows_the_simulated_swing_through_the_camera():
    """The filmed chord grows with the true chord at one scale (the camera's, unknown to the tracker) and the
    residual is a small fraction of a millimetre: the fit's k and arm absorb the scale."""
    film = Film(_scene())
    ball = setup_from_film(film)
    r = rig.Rig.from_seed(1)
    angles = np.radians(np.linspace(0, 55, 23))
    d = np.array([ball(film.sensor(film.screen(th))) for th in angles])
    true = 2000 * r.length * np.sin(angles / 2)
    scale = float(d @ true / (true @ true))
    resid = d - scale * true
    assert np.all(np.isfinite(d))
    assert np.max(np.abs(resid)) < 0.01 * d.max() + 0.3


# -- the whole demo -------------------------------------------------------------------------------------------
def demo_through_camera(film, ball, model, order, seed, est_name="kalman", fps=120.0, turb=0.015):
    r = replace_turb(rig.Rig.from_seed(1), turb)
    t, v = demo.profile(order=order)
    air = rig.wind(t, v, r, seed)
    theta = rig.simulate(t, air, r)
    est = estimator.make(est_name, model, 1.0 / fps, **({"sigma_y": 0.3, "q_v": 30.0} if est_name == "kalman" else {}))
    ticks, out, lost, n = [], [], 0, 0
    tick = t[0] + 0.1
    for tm, gray in filmed_frames(t, theta, film, fps=fps):
        d = ball(gray, tm)
        lost += d is None
        n += 1
        est.update(tm, d)
        while tick <= tm + 0.012:                   # lines go out as this frame's reading lands (12 ms later)
            ticks.append(tick + 0.012)
            out.append(est.value(tick))
            tick += 0.005
    tg, vg = demo.ground_truth(t, v, 200.0, 0.0, 0.05, seed=seed + 1)
    return {"t_st": np.array(ticks), "v_st": np.array(out), "t_gt": tg, "v_gt": vg, "lost": lost / max(n, 1)}


def replace_turb(r, turb):
    from dataclasses import replace
    return replace(r, turb=turb)


def whole_demo(turb=0.015, verbose=True):
    film = Film(_scene(), seed=3)
    ball = setup_from_film(film)
    old = Pendulum()
    cal = demo_through_camera(film, ball, old, "12", 101, turb=turb)
    res = fit.fit_pendulum(cal["t_gt"], cal["v_gt"], cal["t_st"], fit.readings(old, cal["v_st"]), arm=old.arm_mm)
    new = res["model"]
    ball.pos, ball.lost = ball.rest, 0
    run = demo_through_camera(film, ball, new, "21", 1, turb=turb)
    s = demo.score(run["t_gt"], run["v_gt"], run["t_st"], run["v_st"])
    if verbose:
        print(f"setup: ball radius {ball.radius:.1f} px, {ball.mm_per_px:.3f} mm/px; frames without the ball: "
              f"calibration {100 * cal['lost']:.2f}%, scored {100 * run['lost']:.2f}%")
        print(f"fit: k {new.k:.3f} m/s, arm {new.arm_mm:.1f} mm, shift {res['shift']:+.2f} s")
        print(f"scored run (order 21, turbulence {turb}): MSE {s['mse']:.4f}, bias {s['bias']:+.3f}, "
              f"σ {s['sigma']:.3f}, n {s['n']}")
    return s


@pytest.mark.skipif(not os.environ.get("AIRCAM_SLOW"), reason="~2 minutes: set AIRCAM_SLOW=1")
def test_whole_demo_through_the_camera_scores_like_the_pixel_free_simulator():
    from dataclasses import replace
    from aircam import sim
    filmed = whole_demo(verbose=False)
    plain = sim.run(replace(sim.SimConfig(), turb=0.015), "kalman")["logger"]
    assert filmed["mse"] < 2.0 * plain["mse"] + 0.01


if __name__ == "__main__":
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    for turb in [float(x) for x in (sys.argv[1:] or ["0.015"])]:
        whole_demo(turb)
