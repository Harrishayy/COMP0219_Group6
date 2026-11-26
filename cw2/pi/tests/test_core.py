import json
import math
import socket
from pathlib import Path

import numpy as np
import pytest

from aircam import __main__ as cli
from aircam import app, demo, estimator, features, fit, models, stream

PI = Path(__file__).resolve().parents[1]


# -- a synthetic camera frame: two pillars, a crossbar, a straw from the hinge and a shaded ball -------------------
H, W, HINGE = 480, 640, (320.0, 40.0)
_yy, _xx = np.mgrid[:H, :W].astype(np.float32)


def scene(bx, by, r=19.0, noise=2.0, seed=0, ball=230.0, dark=False):
    img = 25 + 8 * np.sin(_xx / 90) + 4 * np.cos(_yy / 70)
    img[:, 40:60] = img[:, 580:600] = 105                         # pillars
    img[30:48, 40:600] = 105                                      # crossbar
    t = np.linspace(0, 1, 800)[:, None]
    sx, sy = HINGE[0] + t * (bx - HINGE[0]), HINGE[1] + t * (by - HINGE[1])
    for dx in (-1, 0, 1):
        img[sy.astype(int), (sx + dx).astype(int)] = 165          # straw
    d = np.hypot(_xx - bx, _yy - by)
    cover = np.clip(r + 0.5 - d, 0, 1)
    img = img * (1 - cover) + (ball - 35 * np.clip(d / r, 0, 1) ** 2) * cover
    img = img + np.random.default_rng(seed).normal(0, noise, img.shape)
    if dark:
        img = 255 - img
    return np.clip(img, 0, 255).astype(np.uint8)


def at(theta_deg, arm=240.0):
    th = math.radians(theta_deg)
    return HINGE[0] + arm * math.sin(th), HINGE[1] + arm * math.cos(th)


# -- the map --------------------------------------------------------------------------------------------------
def test_pendulum_map_round_trip_slope_and_physics():
    m = models.Pendulum(k=8.6, arm_mm=150.0)
    v = np.array([2.0, 5.0, 10.0])
    assert np.allclose(m.speed(m.reading(v)), v)
    h = 1e-6
    assert np.allclose(m.dreading_dv(v), (m.reading(v + h) - m.reading(v - h)) / (2 * h), rtol=1e-4)
    assert m.speed(0.0) == 0.0 and m.speed(-3.0) == 0.0               # below rest reads zero, not NaN
    assert np.all(np.diff(m.speed(np.linspace(0, 400, 200))) >= 0)    # monotone, clamped past 80°
    # a bare ping pong ball: tanθ = drag/weight, so k = √(2mg/(ρ Cd A)) ≈ 8.6 m/s
    assert math.sqrt(2 * 0.0027 * 9.81 / (1.2 * 0.47 * math.pi * 0.02 ** 2)) == pytest.approx(8.6, abs=0.1)
    assert math.degrees(float(m.theta(m.reading(10.0)))) == pytest.approx(53.4, abs=0.5)
    assert models.from_dict(models.to_dict(m)) == m


# -- estimators -----------------------------------------------------------------------------------------------
def _ramp_run(est, fps=120.0, glint_at=None):
    m = models.Pendulum()
    errs = []
    for i in range(int(3 * fps)):
        t = i / fps
        v = 3.0 + 2.0 * t                              # 3 → 9 m/s over 3 s
        y = float(m.reading(v)) + (15.0 if i == glint_at else 0.0)
        est.update(t, y)
        for k in range(1, 3):                          # ticks between frames
            tk = t + k / (3 * fps)
            if t > 1.0:
                errs.append(est.value(tk) - (3.0 + 2.0 * tk))
    return np.abs(errs).max()


def test_expm_matches_constant_rate_transition():
    dt = 0.0083
    assert np.allclose(estimator.expm(np.array([[0, 1], [0, 0]]) * dt), [[1, dt], [0, 1]])
    a = np.array([[-3.0, 1.0], [0.5, -2.0]])
    w, vec = np.linalg.eig(a)
    assert np.allclose(estimator.expm(a), (vec @ np.diag(np.exp(w)) @ np.linalg.inv(vec)).real)


def test_kalman_tracks_a_ramp_between_frames_and_ignores_a_glint():
    m = models.Pendulum()
    assert _ramp_run(estimator.Kalman(m, sigma_y=0.05)) < 0.05
    assert _ramp_run(estimator.Kalman(m, sigma_y=0.05), glint_at=200) < 0.05
    assert _ramp_run(estimator.Hold(m), glint_at=200) > 0.5           # a hold passes the glint straight out


def test_kalman_follows_a_real_jump():
    m = models.Pendulum()
    k = estimator.Kalman(m, sigma_y=0.05, max_rejects=3)
    for i in range(100):
        k.update(i / 120, float(m.reading(3.0)))
    for i in range(100, 110):
        k.update(i / 120, float(m.reading(8.0)))
    assert k.value(110 / 120) == pytest.approx(8.0, abs=0.2)


def test_matched_instrument_model_undoes_ringing():
    """A lightly damped pendulum rings after a step; the matched filter reads the air, not the ringing."""
    m, fn, z = models.Pendulum(), 1.5, 0.08
    t = np.arange(0, 6, 0.001)
    v = np.where(t < 1.0, 4.0, 8.0)
    u = m.reading(v)
    w = 2 * math.pi * fn
    y, yd, out = float(u[0]), 0.0, np.empty_like(t)
    for i in range(t.size):                            # y'' = w²(u − y) − 2ζw y'
        out[i] = y
        yd += (w * w * (u[i] - y) - 2 * z * w * yd) * 0.001
        y += yd * 0.001
    plain = estimator.Kalman(m, sigma_y=0.05, q_v=10.0)
    matched = estimator.Kalman(m, sigma_y=0.05, q_v=10.0, instrument="second", fn=fn, zeta=z)
    err = {id(plain): [], id(matched): []}
    for i in range(0, t.size, 8):                     # 125 fps
        for e in (plain, matched):
            e.update(t[i], out[i])
            if 2.0 < t[i] < 4.0:
                err[id(e)].append(abs(e.value(t[i]) - v[i]))
    assert np.mean(err[id(matched)]) < 0.25 * np.mean(err[id(plain)])


def test_linear_never_extrapolates():
    m = models.Pendulum()
    e = estimator.Linear(m, period=0.01)
    e.update(0.00, 10.0)
    e.update(0.01, 20.0)
    assert e.value(0.015) == pytest.approx(float(m.speed(15.0)))
    assert e.value(0.5) == pytest.approx(float(m.speed(20.0)))


# -- output ---------------------------------------------------------------------------------------------------
def test_line_format_and_the_seven_field_trap():
    assert stream.line(5.12345) == b"5.123\n"
    assert stream.line(5.0, extra=(0.25, 118)) == b"0.25,118,5.000\n"
    with pytest.raises(ValueError):
        stream.line(5.0, extra=range(6))               # 7 fields: the logger would read it as a GIGA line
    with pytest.raises(ValueError):
        stream.line(5.0, extra=[123456.789] * 9)
    assert stream.comment(k=8.6, arm=150.0) == b"# aircam k=8.6 arm=150\n"


def test_udp_sink_sends_one_datagram_per_line():
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(1.0)
    sink = stream.Sink(f"udp:127.0.0.1:{rx.getsockname()[1]}")
    sink.write(stream.line(6.5))
    sink.write(stream.comment(k=9.0))
    assert rx.recv(100) == b"6.500\n" and rx.recv(100) == b"# aircam k=9\n"
    sink.close()
    rx.close()


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


# -- the ball -------------------------------------------------------------------------------------------------
def test_otsu_and_label():
    assert 50 < features.otsu(np.r_[np.full(100, 20), np.full(100, 200)]) < 200
    m = np.zeros((20, 20), bool)
    m[2:5, 2:5] = m[10:18, 3] = m[10, 3:15] = True       # a square and an L
    lab = features.label(m)
    assert len(np.unique(lab[lab > 0])) == 2 and lab[17, 3] == lab[10, 14]


def test_setup_finds_the_ball_not_the_pillars_or_the_straw():
    x, y = at(0)
    found = features.find_ball(scene(x, y))
    assert found is not None
    assert found[0] == pytest.approx(x, abs=0.5) and found[1] == pytest.approx(y, abs=1.0)
    assert found[2] == pytest.approx(19.0, rel=0.05)
    dark = features.find_ball(scene(x, y, dark=True), dark=True)
    assert dark is not None and dark[0] == pytest.approx(x, abs=0.5)
    assert features.find_ball(np.full((H, W), 30, np.uint8)) is None


def _tracker(dark=False):
    x, y = at(0)
    img = scene(x, y, dark=dark)
    fx, fy, r = features.find_ball(img, dark=dark)
    fg, bg = features.levels(img, fx, fy, r, dark)
    return features.Ball(rest=(fx, fy), radius=r, fg=fg, bg=bg, dark=dark), r


def test_ball_distance_from_rest_is_sub_pixel_across_the_swing():
    ball, r = _tracker()
    for deg in (2, 5, 15, 30, 50):
        th = math.radians(deg)
        truth_px = 2 * 240.0 * math.sin(th / 2)
        errs = []
        for seed in range(3):
            ball(scene(*at(deg - 1), seed=seed))       # come from a neighbouring angle, as in a real swing
            d = ball(scene(*at(deg), seed=seed + 10))
            errs.append(d / ball.mm_per_px - truth_px)
        assert abs(np.mean(errs)) < 0.6 and np.std(errs) < 0.1, (deg, errs)   # px; bias is the straw, smooth


def test_ball_lost_and_found_again():
    ball, _ = _tracker()
    assert ball(np.full((H, W), 30, np.uint8)) is None         # nothing there
    hidden = scene(*at(10))
    x, y = at(10)
    hidden[int(y) - 25:int(y) + 25, int(x) - 25:int(x) + 6] = 30   # most of the ball occluded
    assert ball(hidden) is None
    assert ball(scene(*at(40))) is not None and ball.pos[0] == pytest.approx(at(40)[0], abs=0.6)


def test_dark_ball_tracks_too():
    ball, _ = _tracker(dark=True)
    d = ball(scene(*at(20), dark=True))
    assert d / ball.mm_per_px == pytest.approx(2 * 240 * math.sin(math.radians(10)), abs=0.8)


def test_ball_costs_well_under_a_millisecond():
    import time
    ball, _ = _tracker()
    img = scene(*at(25))
    ball(img)
    t = time.perf_counter()
    for _ in range(200):
        ball(img)
    assert (time.perf_counter() - t) / 200 < 0.001


# -- fits -----------------------------------------------------------------------------------------------------
def _write_run(tmp_path, t_gt, v_gt, t_st, v_st, student_col="value_raw_mps", meta=None):
    stem = tmp_path / "run_x"
    paired = demo.align(t_gt, t_st, v_st)
    with open(f"{stem}_aligned.csv", "w") as f:
        f.write("timestamp_iso,timestamp_epoch,ground_truth_mps,student_mps\n")
        for t, g, s in zip(t_gt, v_gt, paired):
            f.write(f"x,{t:.6f},{g:.6f},{'' if np.isnan(s) else f'{s:.6f}'}\n")
    with open(f"{stem}_student.csv", "w") as f:
        f.write(f"timestamp_epoch,{student_col}\n")
        for t, s in zip(t_st, v_st):
            f.write(f"{t:.6f},{s:.6f}\n")
    if meta:
        Path(f"{stem}_meta.json").write_text(json.dumps(meta))
    return Path(f"{stem}_aligned.csv")


def test_fit_recovers_k_arm_and_shift_from_a_logger_run(tmp_path):
    true, ran = models.Pendulum(k=9.3, arm_mm=135.0), models.Pendulum(k=8.6, arm_mm=150.0)
    t, v = demo.profile(dt=0.005)
    t = t + 1.79e9
    d = true.reading(v)                                # what the ball did
    t_st, v_st = t + 0.08, ran.speed(d)                # streamed through the old map, 80 ms late
    tg, vg = demo.ground_truth(t, v, rate_hz=200.0, sigma=0.03)
    path = _write_run(tmp_path, tg, vg, t_st, v_st, meta={"aircam": {"k": 8.6, "arm": 150.0}})
    run = fit.load_run(path)
    assert run["meta"]["aircam"]["k"] == 8.6 and run["t_st"].size == t_st.size
    r = fit.fit_pendulum(run["t_gt"], run["v_gt"], run["t_st"], fit.readings(ran, run["v_st"]), arm=150.0)
    assert r["model"].k == pytest.approx(9.3, rel=0.02) and r["model"].arm_mm == pytest.approx(135, rel=0.05)
    assert r["shift"] == pytest.approx(0.08, abs=0.021)


def test_fit_command_prints_what_to_set_on_the_pi(tmp_path, capsys):
    ran = models.Pendulum(k=8.6, arm_mm=150.0)
    t, v = demo.profile(dt=0.005)
    tg, vg = demo.ground_truth(t, v, rate_hz=200.0, sigma=0.03)
    path = _write_run(tmp_path, tg, vg, t, ran.speed(models.Pendulum(k=9.0, arm_mm=150.0).reading(v)),
                      student_col="value_mps")
    cli.main(["fit", str(path), "--k", "8.6", "--arm", "150", "--pin-arm", "--config", str(tmp_path / "none.json")])
    out = capsys.readouterr().out
    assert "aircam set k=9.0" in out and "arm=150.00" in out


def test_pluck_recovers_frequency_and_damping():
    t = np.arange(0, 8, 1 / 120)
    fn, z = 1.4, 0.06
    wd = 2 * math.pi * fn * math.sqrt(1 - z * z)
    y = 0.1 + 20 * np.exp(-z * 2 * math.pi * fn * t) * np.cos(wd * t)
    r = fit.pluck(t, y)
    assert r["fn"] == pytest.approx(fn, rel=0.02) and r["zeta"] == pytest.approx(z, rel=0.1)


# -- demo -----------------------------------------------------------------------------------------------------
def test_demo_profile_matches_the_email_graph():
    t, v = demo.profile(full=10.0, order="2", still=0.0)
    at_ = lambda s: v[np.searchsorted(t, s) - 1]
    assert at_(3.0) == pytest.approx(8.9, abs=0.2)     # first pulse peak ≈ 0.89 full
    assert at_(6.0) == pytest.approx(4.8, abs=0.3)     # dip ≈ 0.48 full
    assert at_(9.0) == pytest.approx(9.4, abs=0.2)     # second peak ≈ 0.94 full


def test_score_pairs_within_150_ms_and_band():
    tg = np.array([0.0, 1.0, 2.0, 3.0])
    vg = np.array([1.0, 5.0, 6.0, 11.0])               # first and last are outside 2–10
    s = demo.score(tg, vg, ts=np.array([0.0, 1.1, 2.3, 3.0]), vs=np.array([0, 5.5, 9, 11]))
    assert s["n"] == 1 and s["mse"] == pytest.approx(0.25)   # 2.0 has no board sample within 150 ms


# -- config and commands --------------------------------------------------------------------------------------
def test_example_config_builds_once_setup_has_found_the_ball():
    cfg = app.load(PI / "config.example.json")
    assert cfg["ball"] is None
    cfg["ball"] = {"rest": [320, 280], "radius_px": 19, "fg": 220, "bg": 30}
    model, ball, est = app.build(cfg)
    assert isinstance(ball, features.Ball) and isinstance(est, estimator.Kalman)
    assert model.speed(model.reading(6.0)) == pytest.approx(6.0)


def test_set_understands_aliases_and_dotted_keys(tmp_path):
    path = tmp_path / "aircam.json"
    path.write_text((PI / "config.example.json").read_text())
    cli.main(["set", "k=9.25", "arm=141", "port=udp:10.0.0.2:5005", "estimator.instrument=second",
              "--config", str(path)])
    cfg = app.load(path)
    assert cfg["map"]["k"] == 9.25 and cfg["map"]["arm_mm"] == 141
    assert cfg["output"]["port"] == "udp:10.0.0.2:5005" and cfg["estimator"]["instrument"] == "second"
