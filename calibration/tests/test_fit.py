"""(2) Fits recover the simulator's hidden truth for the cup anemometer and the drag plate."""
import pytest

from calib import fit, models
from calib.sources import SimulatedTunnel


def test_drag_plate_fit_recovers_truth():
    sim = SimulatedTunnel(model="sqrt", profile="sweep", seed=3)
    run = sim.generate(80)
    tr, board = sim.truth, sim.board
    k_true = tr.sqrt_k * tr.lc_n_per_count / board["lc_n_per_count"]  # load-cell scale error folds into k
    x0_true = (tr.lc_offset - board["lc_offset"]) * board["lc_n_per_count"]
    r = fit.fit_run("sqrt", models.default_params("sqrt"), run, "robust_lsq")
    assert r.ok, r.message
    assert r.params["sqrt_k"] == pytest.approx(k_true, rel=0.02)
    assert r.params["sqrt_x0"] == pytest.approx(x0_true, abs=0.004)
    assert r.after["mse"] < 0.1 * r.before["mse"]


def test_cup_anemometer_fit_recovers_truth():
    sim = SimulatedTunnel(model="rotor", profile="sweep", seed=4)
    run = sim.generate(110)
    r = fit.fit_run("rotor", models.default_params("rotor"), run, "robust_lsq")
    assert r.ok, r.message
    assert r.params["rotor_slope"] == pytest.approx(sim.truth.rotor_slope, rel=0.05)
    assert r.params["rotor_offset"] == pytest.approx(sim.truth.rotor_offset, abs=0.15)
    assert r.after["mse"] < 0.3 * r.before["mse"]


def test_dynamics_fit_tunes_the_ema_and_lead():
    sim = SimulatedTunnel(model="sqrt", profile="steps", seed=8)
    run = sim.generate(60)
    board = sim.board
    start = dict(models.default_params("sqrt"),
                 sqrt_k=sim.truth.sqrt_k * sim.truth.lc_n_per_count / board["lc_n_per_count"],
                 sqrt_x0=(sim.truth.lc_offset - board["lc_offset"]) * board["lc_n_per_count"])
    r = fit.fit_run("sqrt", start, run, "dynamics")
    assert r.ok, r.message
    assert set(r.free) == {"ema_alpha", "lead_tau"} and r.params["filter_kind"] == 1.0
    assert r.after["mse"] < r.before["mse"]
