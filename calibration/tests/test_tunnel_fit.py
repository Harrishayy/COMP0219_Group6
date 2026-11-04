"""Calibration profiles from noisy tunnel ground truth (calib/tunnel_fit.py): GT cleaning, the static fit with
per-run zeros, the filter search, the profile files and the dashboard's /api/calibrations."""
import json

import numpy as np
import pytest
from scipy.signal import butter, lfilter

from calib import export, models
from calib import tunnel_fit as tf

FS = 200.0
RHO = 1.2
K_TRUE = 200.0


def _speed(seconds=80.0):
    """Four plateaus 20 s apart; the fan takes a second or so to change speed."""
    t = np.arange(int(seconds * FS)) / FS
    plateaus = np.array([4.0, 5.5, 6.5, 5.0])
    v = plateaus[np.minimum((t // 20).astype(int), len(plateaus) - 1)]
    b, a = butter(1, 0.5 / (FS / 2))
    return t, lfilter(b, a, v - v[0]) + v[0]


def _rec(x0, seed, gt_noise=0.35, seconds=80.0):
    """A recording as load_recording returns it: the plate reads F = ρv²/k + F0, the GIGA v plus coloured noise."""
    rng = np.random.default_rng(seed)
    t, v = _speed(seconds)
    force = RHO * v ** 2 / K_TRUE + x0 + rng.normal(0, 0.002, len(t))
    b, a = butter(2, 3.0 / (FS / 2))                     # most of the GIGA's noise sits between 0.2 and 5 Hz
    coloured = lfilter(b, a, rng.normal(0, 1, len(t)))
    gt = v + gt_noise * coloured / coloured.std() + rng.normal(0, 0.1, len(t))
    gt[rng.choice(len(t), 20, replace=False)] += 4.0      # pressure spikes
    params = {**models.default_params(), "sqrt_k": 242.5, "sqrt_x0": 0.0}
    return {"name": f"run_{seed}", "path": f"run_{seed}.csv", "t": t, "gt": gt, "feature": force,
            "rho": np.full(len(t), RHO), "params": params}, v


def test_despike_replaces_only_spikes():
    rng = np.random.default_rng(0)
    x = rng.normal(10, 0.1, 4000)
    x[[500, 1500, 3000]] += 5
    y, n = tf.despike(x)
    assert n >= 3 and np.max(np.abs(y - 10)) < 1.0
    assert np.mean(np.abs(y - x) > 0) < 0.01               # the rest is untouched


def test_zero_phase_adds_no_delay():
    x = np.r_[np.zeros(2000), np.ones(2000)]
    y = tf.zero_phase(x, 0.5)
    assert abs(int(np.argmin(np.abs(y - 0.5))) - 2000) <= 2


def test_cleaned_ground_truth_is_closer_to_the_true_speed():
    rec, v = _rec(0.0, 1)
    c = tf.clean_gt(rec)
    sl = tf._trim(len(v))
    raw_err = np.mean((rec["gt"][sl] - v[sl]) ** 2)
    clean_err = np.mean((c["v_clean"][sl] - v[sl]) ** 2)
    assert clean_err < 0.25 * raw_err


def test_static_fit_recovers_k_with_a_zero_per_run():
    recs = [_rec(-0.03, 2)[0], _rec(-0.06, 3)[0]]
    st = tf.fit_static(recs)
    assert st["sqrt_k"] == pytest.approx(K_TRUE, rel=0.04)
    assert st["k_range"][0] <= st["sqrt_k"] <= st["k_range"][1]
    x0 = list(st["x0_runs"].values())
    assert x0[0] == pytest.approx(-0.03, abs=0.006) and x0[1] == pytest.approx(-0.06, abs=0.006)
    assert st["sqrt_x0"] == pytest.approx(-0.045, abs=0.006) and st["still_air_mps"] > 0


def test_tare_fit_goes_through_the_origin():
    recs = [_rec(0.0, 4)[0], _rec(0.0, 5)[0]]
    st = tf.fit_static(recs, zero="tare")
    assert st["sqrt_x0"] == 0.0 and st["sqrt_k"] == pytest.approx(K_TRUE, rel=0.03)


def test_filter_search_prefers_less_lead_on_a_tie():
    recs = [_rec(-0.03, 6)[0]]
    st = tf.fit_static(recs)
    dyn = tf.tune_dynamics(recs, st)
    best = dyn["best_mse"]
    assert dyn["mse"] <= best * (1 + tf.TIE_TOL)
    near = [r for r in dyn["table"] if r["mse"] <= best * (1 + tf.TIE_TOL)]
    assert dyn["params"]["lead_tau"] == min(r["lead_tau"] for r in near)


def _write_session(path, rec):
    cols = ["t", "gt", "v_board", "v_pc", "feature", "rho"]
    with open(path, "w") as f:
        f.write(",".join(cols) + "\n")
        for i in range(len(rec["t"])):
            f.write(f"{rec['t'][i]:.6f},{rec['gt'][i]:.6f},0,0,{rec['feature'][i]:.7g},{RHO}\n")
    path.with_suffix(".json").write_text(json.dumps({"meta": {}, "param_sets": [{"pset": 0, "params": rec["params"]}]}))


def test_profile_files_and_listing(tmp_path, monkeypatch):
    paths = []
    for x0, seed in ((-0.03, 7), (-0.05, 8)):
        rec, _ = _rec(x0, seed, seconds=60.0)
        p = tmp_path / f"run_{seed}.csv"
        _write_session(p, rec)
        paths.append(p)
    record = tf.calibrate(paths)
    for name, m in record["metrics_after"].items():          # the fit beats the settings the runs flew
        assert m["mse"] < record["metrics_before"][name]["mse"]
    out = tf.write_profile(record, "Test 1", "a label", profile_dir=tmp_path / "profiles", header_dir=tmp_path / "inc")
    rec = export.load(out["json"])                            # the export record format: `calib export` reads it
    assert rec["name"] == "test_1" and set(rec["params"]) == set(tf.PROFILE_KEYS)
    hdr = (tmp_path / "inc" / "test_1" / "calibration_profile.h").read_text()
    assert "#define CAL_SQRT_K" in hdr and "CAL_LC_OFFSET" not in hdr and "CAL_MCP9808" not in hdr
    cmd = (tmp_path / "profiles" / "test_1.cmd").read_text().split("\n")
    assert cmd[0].startswith("SET sqrt_k") and cmd[-2] == "GET"
    assert not any(line.startswith(("SET lc_offset", "SET mcp9808", "SET temp_source")) for line in cmd)  # tare kept
    listed = tf.list_profiles(tmp_path / "profiles")
    assert [p["name"] for p in listed] == ["test_1"] and listed[0]["mse"] is not None


def test_recording_without_force_is_refused(tmp_path):
    p = tmp_path / "run_x.csv"
    p.write_text("t,gt,v_board,v_pc\n0,5,5,5\n0.005,5,5,5\n")
    with pytest.raises(ValueError):
        tf.load_recording(p)


def test_api_calibrations(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from app.server import Engine, create_app

    (tmp_path / "p.json").write_text(json.dumps({"model": models.MODEL_ID, "name": "p", "label": "P",
                                                  "params": {"sqrt_k": 180.0, "sqrt_x0": -0.01}}))
    monkeypatch.setattr(tf, "PROFILE_DIR", tmp_path)
    with TestClient(create_app(Engine("sim", window_s=10, seed=1))) as client:
        r = client.get("/api/calibrations").json()
    assert [p["name"] for p in r["profiles"]] == ["default", "calibrated_fast", "uncalibrated", "p"]
    assert r["profiles"][2]["params"]["sqrt_k"] == models.SQRT_K_THEORY
    assert r["profiles"][0]["params"]["sqrt_k"] == models.default_params()["sqrt_k"]
