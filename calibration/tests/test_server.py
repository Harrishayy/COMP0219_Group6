"""(5) Websocket smoke test against the simulator: hello -> frame -> set_param -> fit_result -> record."""
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
    eng = Engine("sim", "sqrt", window_s=10, seed=1)
    with TestClient(create_app(eng)) as client, client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["labels"] == {"rotor": "Cup anemometer", "sqrt": "Drag plate"}
        frame = _until(ws, "frame", lambda m: len(m["t"]) > 0)
        assert len(frame["t"]) == len(frame["gt"]) == len(frame["est"]) == len(frame["dev"])

        ws.send_json({"type": "record", "on": True, "label": "smoke"})
        time.sleep(2.5)
        ws.send_json({"type": "set_param", "name": "ema_alpha", "value": 0.4})
        assert _until(ws, "params")["params"]["ema_alpha"] == 0.4
        ws.send_json({"type": "fit", "method": "robust_lsq", "window_s": 2})
        assert _until(ws, "fit_result")["model"] == "sqrt"
        time.sleep(1.0)
        ws.send_json({"type": "record", "on": False})
        done = _until(ws, "session", lambda m: not m["recording"])
        assert done["psets"] >= 2 and done["rows"] > 200
    sess = recmod.load_session(next(tmp_path.glob("run_*.json")))
    assert sess["segments"][1]["changed"] == ["ema_alpha"]
