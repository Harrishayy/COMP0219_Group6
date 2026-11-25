"""(5) Websocket smoke test against the simulator: hello -> frame -> set_param -> fit_result -> metrics -> record."""
import time

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

import calib.recorder as recmod  # noqa: E402
from app.server import Engine, create_app  # noqa: E402


def _until(ws, kind, pred=lambda m: True, limit=3000):
    for _ in range(limit):
        m = ws.receive_json()
        if m["type"] == kind and pred(m):
            return m
    raise AssertionError(f"no {kind} message")


def test_websocket_smoke(tmp_path, monkeypatch):
    monkeypatch.setattr(recmod, "RUNS_DIR", tmp_path)
    eng = Engine("sim", window_s=10, seed=1)
    with TestClient(create_app(eng)) as client, client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["method"] == "drag_plate"
        frame = _until(ws, "frame", lambda m: len(m["t"]) > 0)
        assert len(frame["t"]) == len(frame["gt"]) == len(frame["est"]) == len(frame["dev"])

        ws.send_json({"type": "record", "on": True, "label": "smoke"})
        time.sleep(2.5)
        ws.send_json({"type": "set_param", "name": "ema_alpha", "value": 0.4})
        assert _until(ws, "params", lambda m: m["params"]["ema_alpha"] == 0.4)  # the startup GET sends one first
        ws.send_json({"type": "fit", "method": "robust_lsq", "window_s": 2})
        assert _until(ws, "fit_result")["ok"]
        assert isinstance(_until(ws, "metrics", lambda m: m["est"])["diagnosis"], list)
        time.sleep(1.0)
        ws.send_json({"type": "record", "on": False})
        done = _until(ws, "session", lambda m: not m["recording"])
        assert done["psets"] >= 2 and done["rows"] > 200
    assert done["tunnel_run"]["name"].endswith("_simulated")
    sess = recmod.load_session(next(tmp_path.glob("run_*.json")))
    assert sess["segments"][1]["changed"] == ["ema_alpha"]
    # the same recording as a Wind Tunnel Logger run, named "_simulated" as the logger names its own
    from calib import tunnel
    stem = next(tmp_path.glob("run_*.json")).stem
    for suffix in ("_vdas", "_student", "_aligned"):
        assert (tmp_path / f"{stem}_simulated{suffix}.csv").is_file()
    r = tunnel.load(f"{stem}_simulated", dirs=[tmp_path])
    assert r["pairs"] > 100 and r["missing"] == 0 and r["board_rate_hz"] == pytest.approx(200, rel=0.1)


def test_env_reaches_telemetry():
    """The server polls ENV; the simulated board answers like EnvSensors::print_status."""
    from app.server import parse_env

    env = parse_env("#env t_air=22.31 mcp9808=22.30 sht40=nan rh=41.5 p=100870 rho=1.1876")
    assert env["t_air"] == 22.31 and env["sht40"] is None and env["p"] == 100870
    eng = Engine("sim", seed=1)
    eng.sensor_src.connected = True
    eng.poll_env()
    tele = eng.telemetry()["env"]
    assert set(tele) >= {"t_air", "mcp9808", "sht40", "stts22h", "lps22df", "tmp36", "rh", "p", "rho", "age_s"}
    assert 1.1 < tele["rho"] < 1.3
    assert not any(line.startswith("#env") for line in eng.info)  # polled replies stay out of the log


def test_air_temperature_is_one_thermometer_plus_its_offset():
    """No fusion: t_air is the temp_source thermometer's raw reading plus that thermometer's offset."""
    from app.server import parse_env

    eng = Engine("sim", seed=1)
    sim = eng.sensor_src.sim
    sim.command("SET mcp9808_offset 0.5")
    env = parse_env(sim._env_line())
    assert env["t_air"] == pytest.approx(env["mcp9808"] + 0.5, abs=0.011)
    sim.command("SET temp_source 4")
    env = parse_env(sim._env_line())
    assert env["t_air"] == pytest.approx(env["tmp36"], abs=0.011)


def test_bench_without_pitot():
    """Sensors are also tested on the open bench: with the pitot off the stream, telemetry and metrics keep going."""
    eng = Engine("sim", window_s=10, seed=1)
    with TestClient(create_app(eng)) as client, client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "sim", "fan": "off", "pitot": "off", "load_g": 20})
        time.sleep(2.0)
        frame = _until(ws, "frame", lambda m: len(m["t"]) > 0 and m["telemetry"]["gt_hz"] < 1)
        assert frame["telemetry"]["sensor_hz"] > 150
        dev = [v for v in frame["dev"] if v is not None]
        assert dev and 3.0 < dev[-1] < 7.0  # 20 g ≈ 0.196 N on the plate reads as a few m/s
        ws.send_json({"type": "sim", "pitot": "on"})
        _until(ws, "frame", lambda m: m["telemetry"]["gt_hz"] > 1, limit=6000)


def test_no_board_is_reported_not_faked(monkeypatch):
    """With no Nucleo plugged in the server waits on `auto`, says so, and sends no data frames."""
    import calib.sources as sources

    monkeypatch.setattr(sources, "find_nucleo_port", lambda: None)
    eng = Engine("live", sensor_port=sources.AUTO_PORT)
    with TestClient(create_app(eng)) as client, client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        tele = _until(ws, "telemetry", limit=50)["telemetry"]
        assert tele["board_state"] == "absent" and not tele["sensor_connected"]
        assert "No Nucleo" in tele["board_detail"]
        for _ in range(5):  # nothing but telemetry and logs: no frames of made-up readings
            assert ws.receive_json()["type"] in ("telemetry", "log", "ports", "runs")


def test_board_states():
    """Silent and data-less boards are told apart from a streaming one; the simulator says it is simulated."""
    import time as _t

    from calib.sources import SerialSensorSource

    assert Engine("sim", seed=1).board_state(200.0, _t.perf_counter())[0] == "sim"
    eng = Engine("live", sensor_port="/dev/null-board")
    src = eng.sensor_src
    assert isinstance(src, SerialSensorSource)
    now = _t.perf_counter()
    src.connected, src.opened_t = True, now - 5
    assert eng.board_state(0.0, now)[0] == "silent"
    src.last_line_t = now - 0.1
    assert eng.board_state(0.0, now)[0] == "no_data"
    src.data_rows, src.last_data_t = 10, now - 0.4
    assert eng.board_state(2.0, now)[0] == "streaming"  # 2 Hz (LAB2 firmware) is still streaming
    src.last_data_t = now - 3
    assert eng.board_state(0.0, now)[0] == "no_data"


def test_sim_env_has_lm35():
    eng = Engine("sim", seed=1)
    eng.sensor_src.connected = True
    eng.poll_env()
    env = eng.telemetry()["env"]
    assert 15 < env["lm35"] < 30 and abs(env["lm35_v"] * 100 - env["lm35"]) < 0.01


def test_datasheets_served():
    eng = Engine("sim", seed=1)
    with TestClient(create_app(eng)) as client:
        for name in ("lm35.pdf", "mcp9808.pdf"):
            r = client.get(f"/datasheets/{name}")
            assert r.status_code == 200 and r.content[:5] == b"%PDF-"


def test_live_reload_watcher(tmp_path):
    """`pnpm dev` live reload: a changed, added or removed dashboard file is reported once."""
    import os
    from pathlib import Path

    from app.server import FileWatcher

    css, js = tmp_path / "a.css", tmp_path / "b.js"
    css.write_text("x")
    w = FileWatcher(tmp_path)
    assert w.changes() == []
    os.utime(css, ns=(1, 10**18))
    js.write_text("y")
    assert sorted(Path(f).name for f in w.changes()) == ["a.css", "b.js"]
    assert w.changes() == []
    js.unlink()
    assert [Path(f).name for f in w.changes()] == ["b.js"]


def test_results_endpoints(tmp_path, monkeypatch):
    """Results page: a window of a run over HTTP; Show in Finder only for files in the run folders."""
    import numpy as np
    from calib import tunnel

    monkeypatch.setattr(tunnel, "RUN_DIRS", (tmp_path,))
    t = 1.7e9 + np.arange(0, 10, 0.2)
    tunnel.write_run(tmp_path / "r", {"t": t, "v": np.full(t.size, 4.0), "pa": None, "device": "GIGA", "port": "p"},
                     {"t": t + 0.001, "v": np.full(t.size, 4.1), "name": "Student", "port": "b"})
    with TestClient(create_app(Engine("sim", seed=1))) as client:
        w = client.get("/api/tunnel/window", params={"name": "r", "a": 2, "b": 4}).json()
        assert w["stats"]["pairs"] == 11 and abs(w["stats"]["bias"] - 0.1) < 1e-6
        assert client.get("/api/tunnel/window", params={"name": "r", "a": 4, "b": 2}).status_code == 404
        assert client.post("/api/reveal", json={"path": "/etc/hosts"}).status_code == 403
        assert client.post("/api/reveal", json={"path": str(tmp_path / "nope.csv")}).status_code == 404
