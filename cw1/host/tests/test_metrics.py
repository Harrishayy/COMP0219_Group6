import numpy as np
import pytest

from airspeed_tools import metrics, serialio


def test_error_stats():
    s = metrics.error_stats([1.0, 2.0, 3.0], [1.0, 1.0, 1.0])
    assert s.n == 3
    assert s.bias == pytest.approx(1.0)
    assert s.mse == pytest.approx((0 + 1 + 4) / 3)
    assert s.sigma == pytest.approx(np.std([0.0, 1.0, 2.0]))


def test_best_lag_recovers_offset():
    t = np.arange(0, 10, 0.005)
    truth = 6 + 3 * np.sin(2 * np.pi * 0.3 * t)
    est_t = t - 0.12  # device clock 120 ms behind
    lag = metrics.best_lag(est_t, truth, t, truth, max_lag=0.5)
    assert lag == pytest.approx(0.12, abs=0.006)


def test_parse_lines():
    assert serialio.parse("5.123\r\n").values == [5.123]
    assert serialio.parse("1,2.5,3").values == [1.0, 2.5, 3.0]
    info = serialio.parse("#cols,n,v")
    assert info.is_info and info.values is None
    cols = serialio.Columns()
    assert cols.update(info)
    assert cols.for_values([1.0, 2.0]) == ["n", "v"]
    assert cols.for_values([1.0]) == ["v"]
