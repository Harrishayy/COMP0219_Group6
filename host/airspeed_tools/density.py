"""Air density, mirroring firmware/lib/airmath/src/air_density.cpp.

Use it to cross-check what the board reports (GET / ENV) and for the report's error budget.
"""

from __future__ import annotations

import math

R_DRY = 287.058  # J/(kg K)
R_VAPOUR = 461.495  # J/(kg K)
KELVIN = 273.15
P_STD = 101325.0  # Pa
T_STD = 15.0  # C


def saturation_vapour_pressure_pa(temp_c: float) -> float:
    """Magnus form, Alduchov & Eskridge (1996) coefficients."""
    return 610.94 * math.exp(17.625 * temp_c / (temp_c + 243.04))


def dry_air_density(temp_c: float, pressure_pa: float = P_STD) -> float:
    return pressure_pa / (R_DRY * (temp_c + KELVIN))


def humid_air_density(temp_c: float, pressure_pa: float, rh_percent: float) -> float:
    rh = min(max(rh_percent, 0.0), 100.0)
    t_k = temp_c + KELVIN
    p_v = rh / 100.0 * saturation_vapour_pressure_pa(temp_c)
    return (pressure_pa - p_v) / (R_DRY * t_k) + p_v / (R_VAPOUR * t_k)


def air_density(temp_c=None, pressure_pa=None, rh_percent=None) -> float:
    """Best estimate from whatever is known; None/NaN inputs fall back like the firmware."""

    def ok(x):
        return x is not None and math.isfinite(x)

    t = temp_c if ok(temp_c) else T_STD
    p = pressure_pa if ok(pressure_pa) else P_STD
    if ok(rh_percent):
        return humid_air_density(t, p, rh_percent)
    return dry_air_density(t, p)


def speed_error_from_density_error(rel_rho_error: float) -> float:
    """Relative airspeed error caused by a relative density error in v = sqrt(2q/rho)."""
    return 1.0 / math.sqrt(1.0 + rel_rho_error) - 1.0
