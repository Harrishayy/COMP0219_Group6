"""(2) Fits recover the simulator's hidden truth; the diagnosis names the right fix."""
import pytest

from calib import fit, metrics, models
from calib.sources import SimulatedTunnel


def _truth(sim):
    """Effective (k, F0) in board units: the load-cell scale error folds into k, the tare error into F0."""
    tr, board = sim.truth, sim.board
    return (tr.sqrt_k * tr.lc_n_per_count / board["lc_n_per_count"],
            (tr.lc_offset - board["lc_offset"]) * board["lc_n_per_count"])


def test_drag_plate_fit_recovers_truth():
    sim = SimulatedTunnel(profile="sweep", seed=3)
    run = sim.generate(80)
    k_true, x0_true = _truth(sim)
    r = fit.fit_run(models.default_params(), run, "robust_lsq")
    assert r.ok, r.message
    assert r.params["sqrt_k"] == pytest.approx(k_true, rel=0.02)
    assert r.params["sqrt_x0"] == pytest.approx(x0_true, abs=0.004)
    assert r.after["mse"] < 0.1 * r.before["mse"]


def test_dynamics_fit_tunes_the_ema_and_lead():
    sim = SimulatedTunnel(profile="steps", seed=8)
    run = sim.generate(60)
    k, x0 = _truth(sim)
    r = fit.fit_run(dict(models.default_params(), sqrt_k=k, sqrt_x0=x0), run, "dynamics")
    assert r.ok, r.message
    assert set(r.free) == {"ema_alpha", "lead_tau"}
    assert r.after["mse"] < r.before["mse"]


def test_diagnose_names_the_static_error():
    sim = SimulatedTunnel(profile="sweep", seed=3)
    run = sim.generate(80)
    raw, gt = fit.prepare(run)
    k, x0 = _truth(sim)

    def findings(**p):
        params = dict(models.default_params(), **p)
        return {f["id"]: f for f in metrics.diagnose(models.apply(params, raw), gt, raw["feature"], raw["rho"], params)}

    wrong = findings(sqrt_k=k * 1.15, sqrt_x0=x0 + 0.01)
    assert {"scale", "static_refit"} <= set(wrong)
    fix = wrong["static_refit"]["apply"]
    assert fix["sqrt_k"] == pytest.approx(k, rel=0.03) and fix["sqrt_x0"] == pytest.approx(x0, abs=0.03 * abs(x0) + 1e-3)
    assert not [f for f in findings(sqrt_k=k, sqrt_x0=x0).values() if f["severity"] == "high"]
