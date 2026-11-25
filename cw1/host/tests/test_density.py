import math

import pytest

from airspeed_tools import density


def test_isa_sea_level():
    assert density.dry_air_density(15.0, 101325.0) == pytest.approx(1.2250, abs=5e-4)


def test_saturation_vapour_pressure():
    assert density.saturation_vapour_pressure_pa(20.0) == pytest.approx(2339.0, abs=15.0)


def test_humidity_lowers_density():
    dry = density.humid_air_density(25.0, 101325.0, 0.0)
    wet = density.humid_air_density(25.0, 101325.0, 100.0)
    assert wet < dry
    assert wet == pytest.approx(1.1684, abs=2e-3)


def test_fallbacks_match_firmware():
    assert density.air_density() == pytest.approx(1.225, abs=5e-4)
    assert density.air_density(20.0, float("nan"), None) == pytest.approx(density.dry_air_density(20.0))


def test_one_kelvin_is_a_small_speed_error():
    # 1 K at 293 K -> ~0.34% density -> ~0.17% airspeed for sqrt-law transducers
    rel_rho = density.dry_air_density(21.0) / density.dry_air_density(20.0) - 1.0
    assert abs(density.speed_error_from_density_error(rel_rho)) == pytest.approx(0.0017, abs=2e-4)
    assert math.isfinite(rel_rho)
