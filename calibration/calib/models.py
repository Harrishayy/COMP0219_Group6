"""Drag-plate model: force -> airspeed, mirroring firmware/lib/airmath exactly (SPEC.md §3).

The static map is `speed_sqrt_density` from airspeed_models.cpp, v = sqrt(k·(F − F0)/rho) with
k = 2/(Cd·A); the output chain is firmware/src/apps/airspeed.cpp tick(): Median3 -> LeadCompensator ->
(Ema | ButterworthLowPass) -> clamp at 0. The firmware computes in float32, we in float64 (~1e-6 m/s).
The dashboard JS uses the same formulas.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
from scipy.signal import lfilter

FS = 200.0
R_SPECIFIC_AIR = 287.05  # J kg^-1 K^-1, dry air
RHO_REF = 1.204  # kg m^-3 at 20 C, 101325 Pa
MODEL_ID = "sqrt"  # protocol / file id of the drag-plate model
BOARD_METHOD, METHOD_MACRO = "drag_plate", "METHOD_DRAG_PLATE"
FILTER_BUTTERWORTH, FILTER_EMA = 0, 1

# Thermometers in the order the board's temp_source numbers them (EnvSensors, Thermometer). They are never
# combined: each has its own offset and one of them gives the air temperature for the density.
THERMOMETERS = ("mcp9808", "sht40", "stts22h", "lps22df", "tmp36", "lm35")
TEMP_PARAMS = ("temp_source", *(f"{t}_offset" for t in THERMOMETERS))

# Names the firmware accepts via SET (SPEC §1). rho_ref is PC-only.
FIRMWARE_PARAMS = ("sqrt_k", "sqrt_x0", "lc_offset", "lc_n_per_count", "lead_tau", "filter_kind", "ema_alpha",
                   "lpf_hz", *TEMP_PARAMS, "pressure_ref_pa")


def air_density(press_pa, temp_c):
    """Ideal gas law: rho = P / (R_specific * T), pressure in Pa, temperature in C."""
    return np.asarray(press_pa, dtype=float) / (R_SPECIFIC_AIR * (np.asarray(temp_c, dtype=float) + 273.15))


def pitot_speed(dp_pa, rho):
    """Pitot: v = sqrt(2·max(dp, 0)/rho), for a ground-truth script that prints differential pressure."""
    return np.sqrt(2.0 * np.maximum(np.asarray(dp_pa, dtype=float), 0.0) / rho)


@dataclass(frozen=True)
class ParamSpec:
    name: str
    value: float
    min: float
    max: float
    step: float
    unit: str
    desc: str
    group: str  # "static" | "dynamics" | "environment" | "board"

    def to_dict(self) -> dict:
        return asdict(self)


PARAMS = (
    ParamSpec("sqrt_k", 171.0, 1.0, 5000.0, 0.1, "1/m²", "k = 2/(Cd·A)", "static"),
    ParamSpec("sqrt_x0", 0.0, -1.0, 1.0, 0.0001, "N", "Zero-flow force F0", "static"),
    ParamSpec("lc_offset", 0.0, -1e7, 1e7, 1.0, "counts", "Load-cell tare (set by TARE)", "board"),
    ParamSpec("lc_n_per_count", 2.0e-5, 1e-8, 1e-2, 1e-8, "N/count", "Load-cell scale (from `calib weights`)", "board"),
    ParamSpec("filter_kind", 1.0, 0.0, 1.0, 1.0, "", "Output smoother: 1 = EMA on ema_alpha, 0 = Butterworth on lpf_hz", "dynamics"),
    ParamSpec("ema_alpha", 0.2, 0.0001, 1.0, 0.0001, "", "EMA weight of the newest sample: V = a·V_new + (1−a)·V_prev", "dynamics"),
    ParamSpec("lpf_hz", 10.0, 0.0, 99.0, 0.1, "Hz", "Butterworth cutoff (2nd order: damps the plate's ringing)", "dynamics"),
    ParamSpec("lead_tau", 0.0, 0.0, 0.5, 0.001, "s", "First-order lead to cancel the plate's lag; 0 = off", "dynamics"),
    ParamSpec("rho_ref", RHO_REF, 1.0, 1.4, 0.001, "kg/m³", "Density used when the stream has no rho column", "environment"),
    ParamSpec("temp_source", 0.0, 0.0, len(THERMOMETERS) - 1.0, 1.0, "",
              "Thermometer for the air density: " + ", ".join(f"{i} {t.upper()}" for i, t in enumerate(THERMOMETERS)),
              "board"),
    *(ParamSpec(f"{t}_offset", 0.0, -10.0, 10.0, 0.01, "°C", f"{t.upper()} calibration: added to its raw reading",
                "board") for t in THERMOMETERS),
    ParamSpec("pressure_ref_pa", 101325.0, 50000.0, 120000.0, 1.0, "Pa",
              "Constant pressure for the density while the LPS22DF is not reading (station pressure)", "board"),
)
STATIC_PARAMS = ("sqrt_k", "sqrt_x0")


def default_params() -> dict[str, float]:
    return {p.name: p.value for p in PARAMS}


def bounds() -> dict[str, tuple[float, float]]:
    return {p.name: (p.min, p.max) for p in PARAMS}


def catalog() -> dict:
    """JSON-ready model description for the dashboard `hello` message."""
    return {MODEL_ID: {"label": "Drag plate", "methods": [METHOD_MACRO], "fit_params": list(STATIC_PARAMS),
                       "params": [p.to_dict() for p in PARAMS]}}


def filter_kind_of(params: dict) -> int:
    """Firmware rule: filter_kind >= 0.5 selects the EMA, anything else (incl. NaN) the Butterworth."""
    try:
        return FILTER_EMA if float(params.get("filter_kind", 0.0)) >= 0.5 else FILTER_BUTTERWORTH
    except (TypeError, ValueError):
        return FILTER_BUTTERWORTH


def smoothing_param(params: dict) -> str:
    """The active smoother's tuning parameter."""
    return "ema_alpha" if filter_kind_of(params) == FILTER_EMA else "lpf_hz"


def _ffill(x) -> np.ndarray:
    """Forward-fill NaNs (leading NaNs take the first finite value; all-NaN becomes 0)."""
    x = np.asarray(x, dtype=float)
    bad = ~np.isfinite(x)
    if not bad.any():
        return x
    if bad.all():
        return np.zeros_like(x)
    idx = np.where(~bad, np.arange(len(x)), 0)
    np.maximum.accumulate(idx, out=idx)
    out = x[idx]
    first = int(np.argmax(~bad))
    out[:first] = x[first]
    return out


def density(params: dict, raw: dict, n: int) -> np.ndarray:
    rho_ref = params.get("rho_ref", RHO_REF)
    if "rho" in raw and np.isfinite(raw["rho"]).any():
        rho = _ffill(raw["rho"])
        return np.where(rho > 0.5, rho, rho_ref)
    return np.full(n, rho_ref)


def static_map(params: dict, raw: dict) -> np.ndarray:
    """speed_sqrt_density: v = sqrt(k·(F − F0)/rho), 0 where the argument is not positive (F < F0 guard)."""
    if "feature" not in raw:
        raise KeyError(f"no feature (drag force) column; have {sorted(raw)}")
    x = _ffill(raw["feature"])
    rho = density(params, raw, len(x))
    with np.errstate(invalid="ignore", divide="ignore"):
        arg = params["sqrt_k"] * (x - params["sqrt_x0"]) / rho
        return np.where(arg > 0.0, np.sqrt(np.where(arg > 0.0, arg, 0.0)), 0.0)


def biquad_coeffs(cutoff_hz: float, fs: float = FS):
    """RBJ Butterworth low-pass, Q = 1/sqrt(2). Returns (b0, b1, b2, a1, a2); pass-through if disabled."""
    if not (cutoff_hz > 0.0) or not (fs > 0.0) or cutoff_hz >= 0.5 * fs:
        return 1.0, 0.0, 0.0, 0.0, 0.0
    w0 = 2.0 * math.pi * cutoff_hz / fs
    cw = math.cos(w0)
    alpha = math.sin(w0) / (2.0 * 0.70710678)
    a0 = 1.0 + alpha
    b0 = (1.0 - cw) / 2.0 / a0
    return b0, (1.0 - cw) / a0, b0, -2.0 * cw / a0, (1.0 - alpha) / a0


@dataclass
class DynState:
    """Mirror of the firmware filter state, so streaming chunk-by-chunk equals one batch call."""

    lead_primed: bool = False
    x_prev: float = 0.0
    med_hist: tuple = ()      # last <= 2 Median3 inputs
    med_count: int = 0        # samples seen by Median3 (saturates at 3)
    lpf_primed: bool = False
    s1: float = 0.0
    s2: float = 0.0
    lpf_hz: float = float("nan")
    ema_primed: bool = False
    ema_y: float = 0.0
    filter_kind: int = -1     # last smoother used; switching resets both filters, as on the board


def median3(y: np.ndarray, st: DynState) -> np.ndarray:
    """airmath::Median3: the first three samples pass through, then the median of the last three."""
    hist = np.asarray(st.med_hist, dtype=float)
    ext = np.concatenate((hist, y))
    out = y.copy()
    first = max(0, 3 - st.med_count)  # chunk index of the first median output
    if first < len(y):
        j = np.arange(first, len(y)) + len(hist)
        out[first:] = np.median(np.stack((ext[j - 2], ext[j - 1], ext[j])), axis=0)
    st.med_count = min(st.med_count + len(y), 3)
    st.med_hist = tuple(ext[-2:].tolist())
    return out


def lead(x: np.ndarray, tau: float, fs: float, st: DynState) -> np.ndarray:
    """airmath::LeadCompensator: y = x + tau·fs·(x − x_prev); the first sample passes through."""
    gain = tau * fs if (tau > 0.0 and fs > 0.0) else 0.0
    prev = np.concatenate(([st.x_prev if st.lead_primed else x[0]], x[:-1]))
    st.lead_primed, st.x_prev = True, float(x[-1])
    return x + gain * (x - prev)


def butterworth(y: np.ndarray, cutoff_hz: float, fs: float, st: DynState) -> np.ndarray:
    """airmath::ButterworthLowPass (RBJ, transposed DF-II, primed to the first sample).
    A cutoff change re-configures the filter, which un-primes it, as on the board."""
    if st.lpf_primed and cutoff_hz != st.lpf_hz:
        st.lpf_primed = False
    b0, b1, b2, a1, a2 = biquad_coeffs(cutoff_hz, fs)
    if not st.lpf_primed:
        st.s2, st.s1 = y[0] * (b2 - a2), y[0] * (1.0 - b0)
    # Transposed DF-II with state (s1, s2) is exactly lfilter with zi = [s1, s2].
    z, zf = lfilter([b0, b1, b2], [1.0, a1, a2], y, zi=[st.s1, st.s2])
    st.lpf_primed, st.s1, st.s2, st.lpf_hz = True, float(zf[0]), float(zf[1]), cutoff_hz
    return z


def ema(y: np.ndarray, alpha: float, st: DynState) -> np.ndarray:
    """airmath::Ema: primed to its first input, then y += alpha·(x − y). Alpha clamped to [1e-4, 1]
    (NaN or <= 0 -> 1e-4); changing alpha keeps the state, as set_alpha() does on the board."""
    a = float(alpha)
    a = 1e-4 if not math.isfinite(a) or a <= 0.0 else min(max(a, 1e-4), 1.0)
    if not st.ema_primed:
        st.ema_y, st.ema_primed = float(y[0]), True
    z, _ = lfilter([a], [1.0, -(1.0 - a)], y, zi=[(1.0 - a) * st.ema_y])
    st.ema_y = float(z[-1])
    return z


def dynamics(x, params: dict, fs: float = FS, state: DynState | None = None) -> np.ndarray:
    """On-board output chain (firmware tick()): Median3 -> lead -> (EMA | Butterworth) -> clamp at 0."""
    x = _ffill(x)
    if len(x) == 0:
        return x
    st = state if state is not None else DynState()
    y = lead(median3(x, st), float(params.get("lead_tau", 0.0)), fs, st)
    kind = filter_kind_of(params)
    if kind != st.filter_kind:  # firmware resets both smoothers when filter_kind changes
        st.lpf_primed = st.ema_primed = False
        st.filter_kind = kind
    if kind == FILTER_EMA:
        y = ema(y, float(params.get("ema_alpha", 1.0)), st)
    else:
        y = butterworth(y, float(params.get("lpf_hz", 0.0)), fs, st)
    return np.maximum(y, 0.0)


def apply(params: dict, raw: dict, fs: float = FS, state: DynState | None = None) -> np.ndarray:
    """Full model: static map + dynamics. `raw` is {channel: array} at the board sample rate."""
    full = default_params()
    full.update(params)
    return dynamics(static_map(full, raw), full, fs, state)
