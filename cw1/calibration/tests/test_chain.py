"""(1) The PC drag-plate chain is bit-for-bit the firmware tick(): map -> Median3 -> lead -> EMA | Butterworth -> clamp."""
import math

import numpy as np
import pytest

from calib import models


class FirmwareTick:
    """Literal, one-sample-at-a-time port of firmware tick() (airmath Median3, LeadCompensator, Ema,
    ButterworthLowPass), with the filter_kind / ema_alpha rules of the firmware."""

    def __init__(self, lead_tau, filter_kind, ema_alpha, lpf_hz, fs=200.0):
        self.ema = filter_kind >= 0.5
        a = ema_alpha
        self.alpha = 1e-4 if not math.isfinite(a) or a <= 0 else min(max(a, 1e-4), 1.0)
        self.gain = lead_tau * fs if lead_tau > 0 else 0.0
        self.buf, self.count, self.idx = [0.0, 0.0, 0.0], 0, 0
        self.prev, self.lead_primed, self.y, self.primed = 0.0, False, 0.0, False
        if not (lpf_hz > 0) or lpf_hz >= 0.5 * fs:
            self.b = (1.0, 0.0, 0.0, 0.0, 0.0)
        else:
            w0 = 2 * math.pi * lpf_hz / fs
            cw, al = math.cos(w0), math.sin(w0) / (2 * 0.70710678)
            a0 = 1 + al
            self.b = ((1 - cw) / 2 / a0, (1 - cw) / a0, (1 - cw) / 2 / a0, -2 * cw / a0, (1 - al) / a0)
        self.s1 = self.s2 = 0.0

    def median(self, x):
        self.buf[self.idx] = x
        self.idx = (self.idx + 1) % 3
        if self.count < 3:
            self.count += 1
            return x
        return sorted(self.buf)[1]

    def update(self, v):
        m = self.median(v)
        y = m + self.gain * (m - self.prev) if self.lead_primed else m
        self.prev, self.lead_primed = m, True
        if self.ema:
            self.y = y if not self.primed else self.y + self.alpha * (y - self.y)
            self.primed = True
            return max(self.y, 0.0)
        b0, b1, b2, a1, a2 = self.b
        if not self.primed:
            self.s2, self.s1, self.primed = y * (b2 - a2), y * (1 - b0), True
        out = b0 * y + self.s1
        self.s1 = b1 * y - a1 * out + self.s2
        self.s2 = b2 * y - a2 * out
        return max(out, 0.0)


def _inputs(n=600):
    rng = np.random.default_rng(1)
    x = np.abs(np.cumsum(rng.normal(0, 0.01, n))) + rng.normal(0, 0.005, n)  # drag force, N
    rho = 1.19 + rng.normal(0, 0.001, n)
    p = {"sqrt_k": 170.0, "sqrt_x0": 0.003}
    static = [math.sqrt(a) if (a := 170.0 * (f - 0.003) / r) > 0 else 0.0 for f, r in zip(x, rho)]
    return p, {"feature": x, "rho": rho}, static


@pytest.mark.parametrize("lead_tau,filter_kind,ema_alpha,lpf_hz", [
    (0.0, 1, 0.2, 10.0), (0.12, 1, 0.05, 10.0), (0.05, 1, 1.0, 10.0), (0.0, 1, 0.0, 10.0),  # EMA (alpha floor)
    (0.0, 0, 1.0, 10.0), (0.12, 0, 1.0, 6.0), (0.05, 0, 1.0, 0.0),                          # Butterworth
])
def test_chain_matches_firmware(lead_tau, filter_kind, ema_alpha, lpf_hz):
    p, raw, static = _inputs()
    p.update(lead_tau=lead_tau, filter_kind=filter_kind, ema_alpha=ema_alpha, lpf_hz=lpf_hz)
    tick = FirmwareTick(lead_tau, filter_kind, ema_alpha, lpf_hz)
    ref = np.array([tick.update(v) for v in static])
    np.testing.assert_allclose(models.apply(p, raw), ref, atol=1e-9)
    # streaming in odd-sized chunks (as the live server does) gives the same output
    st, parts, i = models.DynState(), [], 0
    for k in (1, 2, 5, 37, 200, 1000):
        sl = slice(i, min(i + k, len(ref)))
        parts.append(models.apply(p, {c: v[sl] for c, v in raw.items()}, state=st))
        i = sl.stop
    np.testing.assert_allclose(np.concatenate(parts), ref, atol=1e-9)


def _plate(force, n):  # k = 1, F0 = 0, rho = 1: v = sqrt(F)
    return {"feature": np.full(n, force), "rho": np.ones(n)}


def test_switching_filter_kind_resets_the_smoother():
    p = {"sqrt_k": 1.0, "sqrt_x0": 0.0, "filter_kind": 1, "ema_alpha": 0.1}
    st = models.DynState()
    models.apply(p, _plate(4.0, 50), state=st)
    models.apply(dict(p, filter_kind=0, lpf_hz=5.0), _plate(36.0, 50), state=st)
    back = models.apply(p, _plate(36.0, 5), state=st)
    assert back[0] == pytest.approx(6.0)  # re-primed, not 2 + 0.1 * (6 - 2) from the stale EMA


def test_missing_samples_do_not_poison_the_filter():
    raw = {"feature": np.array([9.0, np.nan, 9.0, 9.0, np.nan, 9.0]), "rho": np.ones(6)}
    y = models.apply({"sqrt_k": 1, "sqrt_x0": 0, "filter_kind": 1, "ema_alpha": 0.3}, raw)
    assert np.all(np.isfinite(y))


def test_pressure_ref_reaches_the_board_and_header():
    """The constant pressure for ρ (no barometer) is a board param: pushed with SET and written to the header."""
    from calib import export

    p = {**models.default_params(), "pressure_ref_pa": 99850.0}
    assert "SET pressure_ref_pa 99850" in export.push_lines(p)
    assert "#define CAL_PRESSURE_REF_PA 99850.0f" in export.header_text(p, "test")
    assert models.default_params()["pressure_ref_pa"] == 101325.0


def test_board_boots_with_the_toolkit_curve():
    """The board's default force -> speed curve is the toolkit's. A board left on sqrt_k 1.0 (Cd·A = 2 m²) read
    a few hundred grams as 2 m/s while the dashboard preview looked right."""
    import re
    from pathlib import Path

    header = (Path(__file__).resolve().parents[2] / "firmware/include/calibration.h").read_text()
    defaults = models.default_params()
    for name in (*models.STATIC_PARAMS, "filter_kind", "lpf_hz", "ema_alpha", "lead_tau"):
        m = re.search(rf"#define CAL_{name.upper()} ([-+0-9.eE]+)f", header)
        assert m, f"CAL_{name.upper()} missing from calibration.h"
        assert float(m.group(1)) == pytest.approx(defaults[name]), name


def test_uncalibrated_build_matches_the_fallback_profile():
    """The airspeed_drag_plate_uncalibrated env boots with what the dashboard's "Uncalibrated" choice pushes."""
    import re
    from pathlib import Path

    from calib import tunnel_fit

    ini = (Path(__file__).resolve().parents[2] / "firmware/platformio.ini").read_text()
    env = ini[ini.index("[env:airspeed_drag_plate_uncalibrated]"):].split("\n[", 1)[0]
    flags = {k.lower(): float(v) for k, v in re.findall(r"-DCAL_(\w+)=([-+0-9.eE]+)f", env)}
    fallback = tunnel_fit.uncalibrated_profile()["params"]
    assert flags == {k: fallback[k] for k in flags} and set(flags) >= {"sqrt_k", "sqrt_x0", "filter_kind", "lpf_hz"}
    assert fallback["sqrt_k"] == models.SQRT_K_THEORY != models.default_params()["sqrt_k"]
