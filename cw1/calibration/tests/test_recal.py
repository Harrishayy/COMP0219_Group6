"""calib/recal.py: a logger run replayed through other calibrations from the dashboard recording's force."""
import csv
import json
from datetime import datetime, timezone

import numpy as np
import pytest

from calib import models, recal, tunnel, tunnel_fit

FS = models.FS
EPOCH = 1_793_805_000.0     # when the recording started (epoch s)
T0 = 5000.0                 # its perf-counter clock at that moment
ZERO_N = 0.03               # the board's tare was off by this much (about 3 g)
RHO = 1.2


def _truth(t):
    """Still air for 4 s, a 3 s rise to 8 m/s, a hold, a drop to 4 m/s, a hold, then the fan off."""
    v = np.interp(t, [0, 4, 7, 15, 17, 25, 28, 40], [0, 0, 8, 8, 4, 4, 0, 0])
    return v


def _write_session(folder):
    n = int(40 * FS)
    t = np.arange(n) / FS
    v = _truth(t)
    k = models.default_params()["sqrt_k"]
    force = v ** 2 * RHO / k + ZERO_N + np.random.default_rng(3).normal(0, 0.0005, n)
    flown = {**models.default_params(), **models.UNCALIBRATED}
    v_board = models.apply(flown, {"feature": force, "rho": np.full(n, RHO)})
    f = folder / "run_20261104_120000_live_drag_plate.csv"
    with open(f, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["t", "gt", "v_board", "v_pc", "err_board", "err_pc", "feature", "rho"])
        for i in range(n):
            w.writerow([f"{T0 + t[i]:.6f}", "", f"{v_board[i]:.4f}", "", "", "", f"{force[i]:.6f}", RHO])
    meta = {"started": datetime.fromtimestamp(EPOCH, timezone.utc).isoformat(), "stopped": None, "t0": T0,
            "mode": "live", "rows": n, "csv": f.name}
    f.with_suffix(".json").write_text(json.dumps({"meta": meta, "param_sets": [{"params": flown}]}))
    return t, v, v_board


def _logger_run(t, v, v_board, lag=0.3):
    """The logger's arrays: every board sample (stamped 0.25 s late by the logger) and a 5 Hz tunnel that lags."""
    ts = EPOCH + t + 0.25
    tg = EPOCH + np.arange(0, 40, 0.2)
    vg = _truth(tg - EPOCH - lag)
    return {"t": tg, "gt": vg, "st": np.interp(tg, ts, v_board), "ts": ts, "vs": v_board, "tg": tg, "vg": vg,
            "t0": float(tg[0])}


def test_replay_finds_the_recording_aligns_it_and_tares_in_still_air(tmp_path):
    t, v, vb = _write_session(tmp_path)
    run = _logger_run(t, v, vb)
    rep = recal.replay(run, tunnel_fit.builtin_profiles(), sessions_dir=tmp_path)
    assert rep is not None
    assert rep["offset_s"] == pytest.approx(EPOCH - T0 + 0.25, abs=0.02)
    assert rep["zero"]["force_n"] == pytest.approx(ZERO_N, abs=0.002)
    sc = recal.score(rep, run["tg"], run["vg"])["scores"]
    assert sc["default"]["mse"] < 0.1 < sc["as_flown"]["mse"]
    assert sc["default"]["still_air_mps"] < 0.5 < sc["as_flown"]["still_air_mps"]   # the old tare read wind


def test_replay_needs_an_overlapping_recording(tmp_path):
    t, v, vb = _write_session(tmp_path)
    run = _logger_run(t, v, vb)
    far = {k: (a + 86400 if k in ("t", "ts", "tg") else a) for k, a in run.items() if k != "t0"}
    far["t0"] = run["t0"] + 86400
    assert recal.replay(far, tunnel_fit.builtin_profiles(), sessions_dir=tmp_path) is None


def test_results_load_carries_the_calibrated_trace(tmp_path, monkeypatch):
    t, v, vb = _write_session(tmp_path)
    run = _logger_run(t, v, vb)
    base = tmp_path / "logs" / "run_20261104_120000"
    tunnel.write_run(base, {"t": run["tg"], "v": run["vg"], "pa": None, "device": "VDAS", "port": "VDAS-HID"},
                     {"t": run["ts"], "v": run["vs"], "name": "Student", "port": "relay"})
    monkeypatch.setattr(recal, "SESSIONS", tmp_path)
    r = tunnel.load(base.name, [base.parent], cal="calibrated_fast")
    rc = r["recal"]
    assert rc["available"] and rc["cal"] == "calibrated_fast"
    assert [o["name"] for o in rc["options"]][:4] == ["as_flown", "default", "calibrated_fast", "uncalibrated"]
    assert len(r["series"]["cal"]) == len(r["series"]["t"])
    w = tunnel.window(base.name, 5, 15, [base.parent], cal="default")
    assert len(w["cal"]) == len(w["t"]) and w["stats_cal"]["pairs"] > 10
