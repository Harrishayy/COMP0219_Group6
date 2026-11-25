import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from aircam import app, demo, estimator, features, fit, models, sim, stream

PI = Path(__file__).resolve().parents[1]


# -- maps -----------------------------------------------------------------------------------------------------
def test_power_law_round_trip_and_slope():
    m = models.PowerLaw(k=14.0, y0=0.01, p=2.0)
    v = np.array([2.0, 5.0, 10.0])
    assert np.allclose(m.speed(m.reading(v)), v)
    h = 1e-6
    assert np.allclose(m.dreading_dv(v), (m.reading(v + h) - m.reading(v - h)) / (2 * h), rtol=1e-4)
    assert m.speed(0.0) == 0.0                         # below the still-air reading reads zero, not NaN
    rotor = models.PowerLaw(k=2.0, p=1.0, v0=1.0)      # v = 1 + 2·rev/s once turning
    assert rotor.speed(0.0) == 0.0 and rotor.speed(2.0) == pytest.approx(5.0)
    assert models.from_dict(models.to_dict(m)) == m


def test_table_is_monotone_and_invertible():
    t = models.Table(y=[0.0, 0.1, 0.2, 0.4], v=[0.0, 4.0, 3.9, 9.0])   # a dip from noisy points is flattened
    assert np.all(np.diff(t.speed(np.linspace(0, 0.4, 50))) >= 0)
    assert t.speed(t.reading(6.0)) == pytest.approx(6.0)
    assert isinstance(models.from_dict(models.to_dict(t)), models.Table)


# -- estimators -----------------------------------------------------------------------------------------------
def test_expm_matches_constant_rate_transition():
    dt = 0.0083
    assert np.allclose(estimator.expm(np.array([[0, 1], [0, 0]]) * dt), [[1, dt], [0, 1]])
    a = np.array([[-3.0, 1.0], [0.5, -2.0]])
    w, vec = np.linalg.eig(a)
    assert np.allclose(estimator.expm(a), (vec @ np.diag(np.exp(w)) @ np.linalg.inv(vec)).real)


def _ramp_run(est, fps=120.0, glint_at=None):
    m = models.PowerLaw(k=14.0)
    errs = []
    for i in range(int(3 * fps)):
        t = i / fps
        v = 3.0 + 2.0 * t                              # 3 → 9 m/s over 3 s
        y = float(m.reading(v)) + (0.3 if i == glint_at else 0.0)
        est.update(t, y)
        for k in range(1, 3):                          # ticks between frames
            tk = t + k / (3 * fps)
            if t > 1.0:
                errs.append(est.value(tk) - (3.0 + 2.0 * tk))
    return np.abs(errs).max()


def test_kalman_tracks_a_ramp_between_frames_and_ignores_a_glint():
    m = models.PowerLaw(k=14.0)
    assert _ramp_run(estimator.Kalman(m, sigma_y=0.002)) < 0.05
    assert _ramp_run(estimator.Kalman(m, sigma_y=0.002), glint_at=200) < 0.05
    assert _ramp_run(estimator.Hold(m), glint_at=200) > 1.0          # a hold passes the glint straight out


def test_kalman_resets_when_the_reading_really_jumps():
    m = models.PowerLaw(k=14.0)
    k = estimator.Kalman(m, sigma_y=0.002, max_rejects=3)
    for i in range(100):
        k.update(i / 120, float(m.reading(3.0)))
    for i in range(100, 110):
        k.update(i / 120, float(m.reading(8.0)))
    assert k.value(110 / 120) == pytest.approx(8.0, abs=0.2)


def test_matched_instrument_model_undoes_ringing():
    """A lightly damped pendulum rings after a step; the matched filter reads the air, not the ringing."""
    cfg = sim.SimConfig(instrument="second", fn=1.5, zeta=0.08)
    t = np.arange(0, 6, 0.001)
    v = np.where(t < 1.0, 4.0, 8.0)
    y = sim.instrument_response(t, cfg.model.reading(v), cfg)
    plain = estimator.Kalman(cfg.model, sigma_y=0.001, q_v=10.0)
    matched = estimator.Kalman(cfg.model, sigma_y=0.001, q_v=10.0, instrument="second", fn=1.5, zeta=0.08)
    err = {id(plain): [], id(matched): []}
    for i in range(0, t.size, 8):                     # 125 fps
        for e in (plain, matched):
            e.update(t[i], y[i])
            if 2.0 < t[i] < 4.0:
                err[id(e)].append(abs(e.value(t[i]) - v[i]))
    assert np.mean(err[id(matched)]) < 0.25 * np.mean(err[id(plain)])


def test_linear_never_extrapolates():
    m = models.PowerLaw(k=1.0, p=1.0)
    e = estimator.Linear(m, period=0.01)
    e.update(0.00, 1.0)
    e.update(0.01, 2.0)
    assert e.value(0.015) == pytest.approx(1.5)
    assert e.value(0.5) == pytest.approx(2.0)


# -- output ---------------------------------------------------------------------------------------------------
def test_line_format_and_the_seven_field_trap():
    assert stream.line(5.12345) == b"5.123\n"
    assert stream.line(5.0, extra=(0.25, 118)) == b"0.25,118,5.000\n"
    with pytest.raises(ValueError):
        stream.line(5.0, extra=range(6))               # 7 fields: the logger would read it as a GIGA line
    with pytest.raises(ValueError):
        stream.line(5.0, extra=[123456.789] * 9)


def test_ticker_keeps_schedule_catches_up_and_gives_up_after_a_stall():
    now = [0.0]
    tk = stream.Ticker(200, catch_up=4, now=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s))
    times = [tk.wait() for _ in range(3)]
    assert np.allclose(times, [0.005, 0.010, 0.015])
    now[0] += 0.012                                    # held up for ~2 ticks: sent late, none lost
    times = [tk.wait() for _ in range(3)]
    assert np.allclose(times, [0.020, 0.025, 0.030]) and tk.late >= 1 and tk.skipped == 0
    now[0] += 1.0                                      # a 1 s stall: skip ahead rather than burst 200 lines
    tk.wait()
    assert tk.skipped > 150


# -- features -------------------------------------------------------------------------------------------------
def _dot(shape, x, y, r=3.0, peak=250.0, bg=10.0):
    yy, xx = np.mgrid[:shape[0], :shape[1]]
    return (bg + peak * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * r * r))).astype(np.uint8)


def test_centroid_is_sub_pixel_and_reports_a_lost_marker():
    img = _dot((80, 100), 40.37, 22.81)
    cx, cy = features.centroid(img, threshold=60)
    assert abs(cx - 40.37) < 0.08 and abs(cy - 22.81) < 0.08
    assert features.centroid(np.full((80, 100), 10, np.uint8), threshold=60) is None


def test_pendulum_reading_is_tan_of_deflection_and_ignores_camera_roll():
    pivot, length, theta = (200.0, 40.0), 150.0, math.radians(20)
    def scene(roll):
        img = np.zeros((300, 400), np.uint8)
        c, s = math.cos(roll), math.sin(roll)
        def rot(p):                                     # rotate the whole scene about the image centre
            x, y = p[0] - 200, p[1] - 150
            return (200 + c * x - s * y, 150 + s * x + c * y)
        bob = rot((pivot[0] + length * math.sin(theta), pivot[1] + length * math.cos(theta)))
        f1, f2 = rot((60.0, 270.0)), rot((340.0, 270.0))
        for p in (bob, f1, f2):
            img = np.maximum(img, _dot(img.shape, *p, bg=0.0))
        return img, rot(pivot)
    img0, _ = scene(0.0)
    ext = features.Pendulum(roi=features.Roi(0, 100, 400, 140), pivot=pivot, threshold=60,
                            fiducials=((60.0, 270.0), (340.0, 270.0)),
                            fiducial_roi=(features.Roi(30, 240, 60, 60), features.Roi(310, 240, 60, 60)))
    assert ext(img0) == pytest.approx(math.tan(theta), abs=0.003)
    img1, _ = scene(math.radians(2.0))                 # the camera rolled 2°; the pivot moves too (2 px)
    assert ext(img1) == pytest.approx(math.tan(theta), abs=0.04)
    plain = features.Pendulum(roi=features.Roi(0, 100, 400, 140), pivot=pivot, threshold=60)
    assert abs(plain(img1) - math.tan(theta)) > abs(ext(img1) - math.tan(theta))


def test_rotor_rate_from_marker_phase():
    hub, r, rps, fps = (100.0, 100.0), 60.0, 7.0, 120.0
    ext = features.Rotor(roi=features.Roi(0, 0, 200, 200), hub=hub, threshold=60, window=0.2)
    out = None
    for i in range(60):
        t = i / fps
        a = 2 * math.pi * rps * t
        out = ext(_dot((200, 200), hub[0] + r * math.cos(a), hub[1] + r * math.sin(a)), t)
    assert out == pytest.approx(rps, rel=0.01)


# -- fits -----------------------------------------------------------------------------------------------------
def test_align_offset_across_different_clocks():
    rng = np.random.default_rng(3)
    t_pi = np.arange(500.0, 620.0, 0.005)              # Pi boot clock
    v = demo.profile(dt=0.005)[1][: t_pi.size] + rng.normal(0, 0.05, t_pi.size)
    true = 1.79e9 - 500.0 + 0.237                      # laptop epoch, plus transport
    t_b, v_b = t_pi[300:] + true, v[300:]              # the logger started 1.5 s after the Pi
    assert fit.align_offset(t_pi, v, t_b, v_b) == pytest.approx(true, abs=0.006)


def test_fit_power_recovers_map_and_pitot_lag():
    m = models.PowerLaw(k=13.0, y0=0.004, p=2.0)
    t, v = demo.profile(dt=0.002)
    y = m.reading(v) + np.random.default_rng(4).normal(0, 0.0005, v.size)
    tg, vg = demo.ground_truth(t, v, rate_hz=100.0, sigma=0.03)
    r = fit.fit_power(t, y, tg + 0.3, vg)              # the pitot reports 0.3 s late
    assert r["model"].k == pytest.approx(13.0, rel=0.02)
    assert r["model"].y0 == pytest.approx(0.004, abs=0.002)
    assert r["lag"] == pytest.approx(0.3, abs=0.03)


def test_pluck_recovers_frequency_and_damping():
    t = np.arange(0, 8, 1 / 120)
    fn, z = 2.5, 0.06
    wd = 2 * math.pi * fn * math.sqrt(1 - z * z)
    y = 0.1 + 0.2 * np.exp(-z * 2 * math.pi * fn * t) * np.cos(wd * t)
    r = fit.pluck(t, y)
    assert r["fn"] == pytest.approx(fn, rel=0.02) and r["zeta"] == pytest.approx(z, rel=0.1)


# -- demo and simulator ---------------------------------------------------------------------------------------
def test_demo_profile_matches_the_email_graph():
    t, v = demo.profile(full=10.0, order="2", still=0.0)
    at = lambda s: v[np.searchsorted(t, s) - 1]
    assert at(3.0) == pytest.approx(8.9, abs=0.2)      # first pulse peak ≈ 0.89 full
    assert at(6.0) == pytest.approx(4.8, abs=0.3)      # dip ≈ 0.48 full
    assert at(9.0) == pytest.approx(9.4, abs=0.2)      # second peak ≈ 0.94 full


def test_score_pairs_within_150_ms_and_band():
    tg = np.array([0.0, 1.0, 2.0, 3.0])
    vg = np.array([1.0, 5.0, 6.0, 11.0])               # first and last are outside 2–10
    s = demo.score(tg, vg, ts=np.array([0.0, 1.1, 2.3, 3.0]), vs=np.array([0, 5.5, 9, 11]))
    assert s["n"] == 1 and s["mse"] == pytest.approx(0.25)   # 2.0 has no board sample within 150 ms


def test_simulated_demo_kalman_beats_hold():
    cfg = replace(sim.SimConfig(), order="2")          # the pulses: quicker to simulate
    k, h = sim.run(cfg, "kalman")["truth"]["mse"], sim.run(cfg, "hold")["truth"]["mse"]
    assert k < 0.5 * h


def test_example_config_builds():
    cfg = app.load(PI / "config.example.json")
    model, ext, est = app.build(cfg)
    assert isinstance(ext, features.Pendulum) and isinstance(est, estimator.Kalman)
    assert model.speed(model.reading(6.0)) == pytest.approx(6.0)
