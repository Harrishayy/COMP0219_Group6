import csv
import datetime
import json
import math
import os
import re
import socket
import time
from dataclasses import replace

import numpy as np
import pytest

from aircam import demo, rig, screen

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")      # render_array must work with no display at all
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")


# -- physics --------------------------------------------------------------------------------------------------
def test_steady_angle_grows_as_v_squared_and_reads_about_53_deg_at_10():
    v = np.array([2.0, 4.0, 6.0, 8.0, 10.0])
    for r in [rig.Rig()] + [rig.Rig.from_seed(s) for s in range(1, 6)]:
        th = rig.steady_theta(v, r)
        assert np.all(np.diff(th) > 0)
        k = np.tan(th) / v ** 2                       # ∝ v² but for the straw's drag and Cd(Re)
        assert k.max() / k.min() < 1.35
        assert math.degrees(th[-1]) == pytest.approx(53, abs=10)
        assert math.degrees(th[0]) == pytest.approx(3.5, abs=2.0)
    r = rig.Rig.from_seed(1)
    t = np.arange(0, 10, 0.001)
    theta = rig.simulate(t, np.full_like(t, 6.0), r)    # the dynamics settle where the statics say
    assert theta[t > 8].mean() == pytest.approx(float(rig.steady_theta(6.0, r)), abs=math.radians(0.3))


def _ring(t, x):
    i = np.where((x[1:-1] > x[:-2]) & (x[1:-1] >= x[2:]) & (x[1:-1] > 0))[0] + 1
    fn = (i.size - 1) / (t[i[-1]] - t[i[0]])
    d = math.log(x[i[0]] / x[i[-1]]) / (i.size - 1)
    return fn, d / math.sqrt(4 * math.pi ** 2 + d * d)


def test_free_decay_rings_near_1p3_hz_with_light_damping():
    for s in (1, 2, 3):
        t, theta = rig.pluck(rig.Rig.from_seed(s))
        assert math.degrees(theta[0]) == pytest.approx(25.0)
        fn, zeta = _ring(t, theta)
        assert 1.15 < fn < 1.8
        assert 0.02 < zeta < 0.13


def test_pendulum_step_matches_simulate():
    r = rig.Rig.from_seed(2)
    t = np.arange(0, 3, 0.005)
    v = np.where(t < 1, 0.0, 7.0)
    p = rig.Pendulum(r)
    live = [p.theta] + [p.step(0.005, x) for x in v[:-1]]
    assert np.allclose(live, rig.simulate(t, v, r), atol=1e-12)


def test_turbulence_has_its_intensity_and_never_goes_negative():
    r = rig.Rig(turb=0.02, turb_tau=0.06)
    t = np.arange(0, 120, 0.005)
    v = rig.wind(t, np.full_like(t, 8.0), r, seed=4)
    n = v / 8.0 - 1
    assert n.std() == pytest.approx(0.02, rel=0.1)
    lag = round(r.turb_tau / 0.005)
    assert np.corrcoef(n[:-lag], n[lag:])[0, 1] == pytest.approx(math.exp(-1), abs=0.08)
    g = rig.Gust(r.turb_tau, 4)                          # the live loop's stepwise noise is the same stream
    assert np.allclose([8.0 * (1 + r.turb * g.at(x)) for x in t[:500]], v[:500])
    harsh = rig.wind(t, np.full_like(t, 1.0), replace(r, turb=0.8), seed=4)
    assert harsh.min() == 0.0 and (harsh == 0).any()
    assert np.all(rig.wind(t, np.zeros_like(t), r, seed=4) == 0.0)


def test_rig_ranges():
    for s in range(20):
        r = rig.Rig.from_seed(s)
        assert 3.2 <= r.ld <= 4.2 and 0.01 <= r.turb <= 0.025 and 0.03 <= r.turb_tau <= 0.1
    assert rig.Rig.from_seed(7) == rig.Rig.from_seed(7)


# -- receiving and saving -------------------------------------------------------------------------------------
def test_parse_reads_lines_as_the_logger_does():
    assert screen.parse("") is None and screen.parse("   \r") is None
    assert screen.parse("# a comment") is None and screen.parse("#HELLO GIGA") is None
    assert screen.parse("# aircam k=8.640 arm=150.0 est=kalman junk") == {"k": 8.64, "arm": 150.0, "est": "kalman"}
    assert screen.parse("# aircam") == {}
    assert screen.parse("5.125\r\n") == 5.125
    assert screen.parse("0.31,12,4.500") == 4.5                 # last field is the speed
    assert screen.parse("1,2,3,4,5,6,7.25") == 7.25              # 7 fields: no GIGA special case
    for bad in ("abc", "1,2,", "4.5,x", "nan", "1,inf"):
        with pytest.raises(ValueError):
            screen.parse(bad)
    rx = screen.Receiver()
    rx.feed(b"1.0\n# aircam k=2\n\ngarbage\n0.1,2.5\n3.0", now=100.0)
    assert rx.take() == [(100.0, 1.0), (100.0, 2.5), (100.0, 3.0)] and rx.take(2) == [(100.0, 3.0)]
    assert rx.header == {"k": 2.0} and rx.bad == 1


def test_receiver_listens_on_udp():
    rx = screen.Receiver()
    port = rx.udp(0, host="127.0.0.1")
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(b"4.000\n5.000\n", ("127.0.0.1", port))
    s.close()
    t0 = time.time()
    while len(rx.take()) < 2 and time.time() - t0 < 2:
        time.sleep(0.01)
    rx.close()
    got = rx.take()
    assert [v for _, v in got] == [4.0, 5.0] and abs(got[0][0] - t0) < 1.0


def test_aligned_csv_is_the_loggers_format(tmp_path):
    tg = 1793276715.0 + np.arange(4) * 0.005
    vg = np.array([5.194811, 5.2, 5.3, 5.4])
    ts, vs = np.array([1793276714.95, 1793276715.0071]), np.array([5.453, 5.5])
    tg = np.append(tg, tg[-1] + 1.0)                    # a second later: no line within 150 ms
    vg = np.append(vg, 6.0)
    path = tmp_path / "x_aligned.csv"
    screen.write_aligned(path, tg, vg, ts, vs)
    raw = path.read_bytes().decode()
    lines = raw.split("\r\n")                           # csv.writer, as the logger uses
    assert lines[0] == "timestamp_iso,timestamp_epoch,ground_truth_mps,student_mps"
    row = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}[+-]\d\d:\d\d,\d+\.\d{6},\d+\.\d{6},(\d+\.\d{6})?$")
    assert all(row.match(x) for x in lines[1:-1]) and lines[-1] == ""
    rows = list(csv.reader(lines[1:-1]))
    assert rows[0] == [datetime.datetime.fromtimestamp(tg[0]).astimezone().isoformat(timespec="milliseconds"),
                       "1793276715.000000", "5.194811", "5.500000"]
    assert [r[3] for r in rows] == ["5.500000"] * 4 + [""]

    score = screen.save_run(tmp_path, "sim_x", tg, vg, np.zeros_like(tg), ts, vs, {"rig_seed": 1})
    assert score == demo.score(tg, vg, ts, vs)
    meta = json.loads((tmp_path / "sim_x_meta.json").read_text())
    assert meta["n_truth"] == 5 and meta["n_student"] == 2 and meta["rig_seed"] == 1
    assert (tmp_path / "sim_x_truth.csv").read_text().startswith("timestamp_epoch,v_true_mps,theta_deg,v_air_mps")
    assert (tmp_path / "sim_x_student.csv").read_text().startswith("timestamp_epoch,value_mps")
    empty = screen.save_run(tmp_path, "sim_y", tg, vg, np.zeros_like(tg), [], [], {})
    assert empty["n"] == 0 and json.loads((tmp_path / "sim_y_meta.json").read_text())["score"]["mse"] is None


# -- drawing --------------------------------------------------------------------------------------------------
def test_render_array_puts_the_ball_where_the_geometry_says():
    pytest.importorskip("pygame")
    sc = screen.Scene(480, 300, rig.Rig.from_seed(1))
    d = sc.ball_px
    centres = []
    for deg in (0.0, 40.0):
        img = sc.render_array(math.radians(deg))
        assert img.shape == (300, 480, 3) and img.dtype == np.uint8
        gray = img.astype(float).mean(axis=2)
        assert np.percentile(gray, 50) < 60                       # a dark background ...
        assert gray.std() > 2                                     # ... that is not uniform
        bx, by = sc.ball_xy(math.radians(deg))
        ys, xs = np.nonzero(gray > 150)                           # the brightest thing on screen is the ball
        assert np.hypot(xs - bx, ys - by).max() < 0.6 * d
        w, h = np.ptp(xs) + 1, np.ptp(ys) + 1
        assert 0.75 < w / h < 1.33 and 0.55 < xs.size / (w * h) < 0.9   # round, not a streak
        cx, cy = xs.mean(), ys.mean()
        assert np.hypot(cx - bx, cy - by) < 0.25 * d              # the lit side pulls it up-left a little
        centres.append((cx, cy))
    (x0, y0), (x1, y1) = centres
    assert x1 > x0 + 0.5 * sc.arm_px * math.sin(math.radians(40)) and y1 < y0   # downstream, and up


# -- the live loop's logic (no pygame: a fake clock drives it) ------------------------------------------------
class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _live(tmp_path, *argv):
    clock = _Clock()
    a = screen._args(["--seed", "3", "--out", str(tmp_path), *argv])
    return screen.Live(a, rig.Rig.from_seed(1), screen.Receiver(), clock=clock), clock


def _run_for(live, clock, seconds):
    for _ in range(round(seconds / 0.02)):          # 50 frames a second
        clock.t += 0.02
        live.advance(clock.t)


def test_manual_air_approaches_each_speed_with_the_tunnels_time_constant(tmp_path):
    live, clock = _live(tmp_path, "--mode", "manual")
    assert live.press("speed:6")
    _run_for(live, clock, demo.TAU_ON)              # spinning up from still: τ 1.35 s
    assert live.v_mean == pytest.approx(6 * (1 - math.exp(-1)), abs=0.03)
    _run_for(live, clock, 10)
    live.press("speed:8")                           # between levels: τ 0.5 s
    _run_for(live, clock, demo.TAU_PANEL)
    assert live.v_mean == pytest.approx(8 - 2 * math.exp(-1), abs=0.03)
    _run_for(live, clock, 5)
    live.press("speed:0")                           # coasting down: τ 4.85 s
    _run_for(live, clock, demo.TAU_OFF)
    assert live.v_mean == pytest.approx(8 * math.exp(-1), abs=0.05)
    live.press("speed:+")
    assert live.fan.target == 0.5 and live.fan.tau == demo.TAU_ON
    live.press("speed:+")
    assert live.fan.target == 1.0 and live.fan.tau == demo.TAU_PANEL
    for _ in range(40):
        live.press("speed:+")
    assert live.fan.target == screen.SPEED_MAX
    assert 0.0 < live.v < 2 * live.v_mean           # the ball's air is the mean flow with turbulence on it


def test_demo_run_replays_the_protocol_and_saves_itself(tmp_path):
    live, clock = _live(tmp_path, "--full", "8")
    live.press("start")
    assert live.running and live.press("speed:5") and live.fan.target == 0.0    # no speed buttons in DEMO
    live.press("mode:MANUAL")
    live.press("order:21")
    assert live.mode == "DEMO" and live.order == "12"                            # fixed during a run
    _run_for(live, clock, 60)
    n = len(live.vg)
    planned = demo.profile(dt=screen.TICK, full=8.0, order="12")[1]
    assert np.allclose(live.vg, planned[:n], atol=1e-9)                          # truth is the protocol
    assert np.std(np.array(live.va) - live.vg) > 0.05                            # the ball's air is not
    while not live.finished:
        _run_for(live, clock, 1)
    assert len(live.vg) == planned.size
    live.finish(complete=True)
    meta = json.loads(next(tmp_path.glob("*_meta.json")).read_text())
    assert meta["mode"] == "DEMO" and meta["complete"] and meta["order"] == "12" and meta["full_mps"] == 8.0
    assert meta["n_truth"] == planned.size and meta["n_student"] == 0


def test_manual_recording_and_pluck(tmp_path):
    live, clock = _live(tmp_path, "--mode", "manual")
    live.press("start")
    live.press("speed:7")
    _run_for(live, clock, 3)
    live.press("pluck")                                  # not during a run
    assert not live.plucking and live.fan.target == 7.0
    live.press("start")                                  # STOP: a manual recording is complete when stopped
    meta = json.loads(next(tmp_path.glob("*_meta.json")).read_text())
    assert meta["mode"] == "MANUAL" and meta["complete"] and meta["set_speeds"] == [[0.0, 0.0], [0.0, 7.0]]
    live.press("pluck")
    assert live.plucking and live.v_mean == 0.0 and math.degrees(live.pend.theta) == pytest.approx(25.0)
    _run_for(live, clock, screen.PLUCK_S + 0.1)
    assert not live.plucking and abs(live.pend.theta) < math.radians(25)
    assert not live.press("quit")


def test_buttons_tile_the_panel_and_keys_map_to_the_same_actions():
    w, h = 1280, 800
    x0 = screen.split(w)
    for mode in ("DEMO", "MANUAL"):
        buttons, y_end = screen.panel_buttons(x0, w - x0, h, mode)
        real = [b for b in buttons if not b.name.startswith("text:")]
        for b in real:
            x, y, bw, bh = b.rect
            assert x >= x0 and x + bw <= w and y_end <= h / 2
            assert [c.name for c in real if c.hit((x + bw / 2, y + bh / 2))] == [b.name]
        assert not any(b.hit((x0 - 5, 20)) for b in buttons)       # nothing over the pendulum
        names = {b.name for b in real}
        assert {"mode:DEMO", "mode:MANUAL", "start", "pluck", "quit"} <= names
        assert ({"order:12", "order:21", "full:-", "full:+"} if mode == "DEMO"
                else {f"speed:{v}" for v in screen.MANUAL_SPEEDS} | {"speed:-", "speed:+"}) <= names
    k = screen.key_action
    assert k("space", "DEMO") == k("space", "MANUAL") == "start"
    assert k("escape", "DEMO") == k("q", "MANUAL") == "quit" and k("p", "DEMO") == "pluck"
    assert k("tab", "DEMO") == "mode:MANUAL" and k("tab", "MANUAL") == "mode:DEMO"
    assert k("1", "DEMO") == "order:12" and k("2", "DEMO") == "order:21" and k("up", "DEMO") == "full:+"
    assert k("1", "MANUAL") == "speed:10" and k("0", "MANUAL") == "speed:0" and k("[7]", "MANUAL") == "speed:7"
    assert k("down", "MANUAL") == "speed:-" and k("x", "MANUAL") is None and k("5", "DEMO") is None
