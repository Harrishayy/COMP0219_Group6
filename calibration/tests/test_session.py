"""(4) Session recording: a new param set per change, per-segment scores, CSV + JSON round trip."""
import csv

import numpy as np
import pytest

from calib import models
from calib.data import load_csv
from calib.recorder import Session, load_session


def _feed(sess, t0, secs, offset, fs=200.0):
    t = t0 + np.arange(int(secs * fs)) / fs
    gt = 5 + np.sin(t)
    sess.add_rows(t, gt, v_board=gt + offset, v_pc=gt + 2 * offset, feature=gt * 10, rho=np.full(len(t), 1.2))
    return t[-1] + 1 / fs


def test_param_sets_segments_and_files(tmp_path):
    p = models.default_params()
    s = Session(p, {"mode": "test"}, path=tmp_path / "run.csv", t0=0.0)
    t = _feed(s, 0.0, 2.0, offset=0.1)
    assert not s.change(dict(p), t=t)                       # no-op keeps the param set
    p2 = dict(p, ema_alpha=0.3)
    assert s.change(p2, t=t) and s.pset == 1
    t = _feed(s, t, 2.0, offset=0.3)
    s.note("fan to 8 m/s", t=t)
    s.change(dict(p2, sqrt_k=180.0, lead_tau=0.05), kind="fit", t=t)
    t = _feed(s, t, 2.0, offset=0.0)
    s.close(t)

    sess = load_session(tmp_path / "run.json")
    segs = sess["segments"]
    assert [sg["changed"] for sg in segs] == [[], ["ema_alpha"], ["lead_tau", "sqrt_k"]]
    # board offsets 0.1, 0.3, 0.0 -> MSE 0.01, 0.09, 0 per segment; first 0.5 s after a change not scored
    assert [sg["metrics_board"]["mse"] for sg in segs] == pytest.approx([0.01, 0.09, 0.0], abs=1e-12)
    assert segs[1]["n_scored"] == segs[1]["n"] - 100
    assert sess["param_sets"][1]["params"]["ema_alpha"] == 0.3
    kinds = [e["kind"] for e in sess["events"]]
    assert kinds[0] == "start" and kinds[-1] == "stop" and "note" in kinds and "fit" in kinds
    assert sess["summary"]["board"]["best_pset"] == 2

    rows = list(csv.DictReader((tmp_path / "run.csv").open()))
    assert set(models.default_params()) <= set(rows[0])        # every param is a column
    assert {r["ema_alpha"] for r in rows if r["pset"] == "1"} == {"0.3"}
    run = load_csv(tmp_path / "run.csv")
    assert len(run.t_s) == len(rows) and len(run.gt) == len(rows) and "v" in run.sensor
