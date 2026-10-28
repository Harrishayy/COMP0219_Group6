"""Wind Tunnel Logger runs (calib/tunnel.py): read the logger's CSVs, score them and measure drift."""
import csv

import numpy as np
import pytest

from calib import tunnel


def write_run(d, name, t, gt, board):
    with open(d / f"{name}_aligned.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_iso", "timestamp_epoch", "ground_truth_mps", "student_mps"])
        for ti, g, b in zip(t, gt, board):
            w.writerow(["", f"{ti:.6f}", f"{g:.6f}", "" if b is None else f"{b:.6f}"])


def test_score_and_drift(tmp_path):
    t = 1.7e9 + np.arange(0, 120, 0.2)                      # 2 min of ground truth at 5 Hz
    gt = 2 + 8 * (np.arange(t.size) % 300) / 300             # ramps 2 -> 10 m/s
    board = gt + 0.1 + 0.5 * (t - t[0]) / 60                  # +0.1 m/s bias, drifting +0.5 m/s per minute
    write_run(tmp_path, "run1", t, gt, [*board[:-3], None, None, None])
    r = tunnel.load("run1", dirs=[tmp_path])
    assert r["pairs"] == t.size - 3 and r["missing"] == 3
    assert r["drift_per_min"] == pytest.approx(0.5, abs=1e-3)
    assert r["bias_first"] < r["bias_last"]
    sr = r["series"]
    assert len(sr["t"]) == len(sr["board"]) == len(sr["gt"]) == len(sr["error_mean"])
    late = [e for e in sr["error_mean"][-200:-20] if e is not None]
    assert late and min(late) > 0.9                          # the moving mean shows the drift late in the run
    assert [x["name"] for x in tunnel.list_runs([tmp_path])] == ["run1"]


def test_drag_area_suggestion(tmp_path):
    t = 1.7e9 + np.arange(0, 60, 0.2)
    gt = np.linspace(2.5, 10, t.size)
    write_run(tmp_path, "k", t, gt, gt * np.sqrt(1.21))       # board reads 10 % high: its sqrt_k is 21 % too big
    assert tunnel.load(str(tmp_path / "k_aligned.csv"), series=False)["k_scale"] == pytest.approx(1 / 1.21)


def test_no_runs(tmp_path):
    with pytest.raises(FileNotFoundError):
        tunnel.load(None, dirs=[tmp_path])


def test_logger_run_replays_through_the_dashboard(tmp_path):
    """A logger run loads as a replayable Run: board speed as `v`, the tunnel as ground truth, on one time base."""
    from calib.data import load_csv

    t = 1.7e9 + np.arange(0, 10, 0.2)
    write_run(tmp_path, "r", t, np.full(t.size, 5.0), np.full(t.size, 5.2))
    run = load_csv(tmp_path / "r_aligned.csv")
    assert run.channels == ["v"] and run.t_g[0] == 0.0
    assert np.allclose(run.gt, 5.0) and np.allclose(run.sensor["v"], 5.2)
