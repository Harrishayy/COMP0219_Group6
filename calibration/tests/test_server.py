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
    sess = recmod.load_session(next(tmp_path.glob("run_*.json")))
    assert sess["segments"][1]["changed"] == ["ema_alpha"]


def test_env_reaches_telemetry():
    """The server polls ENV; the simulated board answers like EnvSensors::print_status."""
    from app.server import parse_env

    env = parse_env("#env t_fused=22.31 sigma=0.02 mcp9808=22.30 sht40=nan rh=41.5 p=100870 rho=1.1876")
    assert env["t_fused"] == 22.31 and env["sht40"] is None and env["p"] == 100870
    eng = Engine("sim", seed=1)
    eng.sensor_src.connected = True
    eng.poll_env()
    tele = eng.telemetry()["env"]
    assert set(tele) >= {"t_fused", "mcp9808", "sht40", "stts22h", "lps22df", "tmp36", "rh", "p", "rho", "age_s"}
    assert 1.1 < tele["rho"] < 1.3
    assert not any(line.startswith("#env") for line in eng.info)  # polled replies stay out of the log


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
