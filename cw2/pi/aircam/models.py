"""Static calibration maps: the instrument's steady reading y (what the camera measures) to airspeed v in m/s.

Every map has `speed(y)`, its inverse `reading(v)` and `dreading_dv(v)` (the estimators use the slope to put
their process noise in m/s terms). Maps round-trip through JSON so the calibration file the Pi runs from is
the one shown on demand.

PowerLaw covers the two physics we are likely to build:
  - drag on a deflecting body (flap, pendulum, cantilever): reading ∝ force ∝ v², so p = 2 and v = k·√(y − y0)
  - a rotor (cups, propeller, Savonius) read as a rate: rate ∝ v above a start-up speed, so p = 1 and
    v = v0 + k·(y − y0)
Table is a monotone piecewise-linear correction for whatever the physics misses, fitted from tunnel runs.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np


@dataclass
class PowerLaw:
    k: float                # m/s per reading^(1/p)
    y0: float = 0.0         # reading in still air
    p: float = 2.0          # reading ∝ (v − v0)^p
    v0: float = 0.0         # speed at which the reading starts to rise (a rotor's start-up speed)
    type: str = field(default="power", init=False)

    def speed(self, y):
        s = np.maximum(np.asarray(y, float) - self.y0, 0.0)
        v = self.v0 + self.k * s ** (1.0 / self.p)
        return np.where(s > 0, v, 0.0) if self.v0 else v

    def reading(self, v):
        s = np.maximum(np.asarray(v, float) - self.v0, 0.0) / self.k
        return self.y0 + s ** self.p

    def dreading_dv(self, v):
        s = np.maximum(np.asarray(v, float) - self.v0, 1e-6) / self.k
        return self.p / self.k * s ** (self.p - 1.0)


@dataclass
class Table:
    """Monotone piecewise-linear map through calibration points (y must increase; v is made non-decreasing)."""
    y: list
    v: list
    type: str = field(default="table", init=False)

    def __post_init__(self):
        y, v = np.asarray(self.y, float), np.maximum.accumulate(np.asarray(self.v, float))
        if len(y) < 2 or np.any(np.diff(y) <= 0):
            raise ValueError("Table needs at least two points with increasing y")
        self._y, self._v = y, v

    def speed(self, y):
        return np.interp(np.asarray(y, float), self._y, self._v)

    def reading(self, v):
        # The inverse of a non-decreasing table: nudge flat stretches up by a hair so interp has increasing xp.
        return np.interp(np.asarray(v, float), self._v + 1e-9 * np.arange(self._v.size), self._y)

    def dreading_dv(self, v):
        v = np.asarray(v, float)
        dv, dy = np.diff(self._v), np.diff(self._y)
        slope = np.where(dv > 0, dy / np.maximum(dv, 1e-12), np.inf)
        i = np.clip(np.searchsorted(self._v, v, side="right") - 1, 0, len(slope) - 1)
        return np.minimum(slope[i], 1e6)


def to_dict(m) -> dict:
    d = asdict(m)
    d["type"] = m.type
    return d


def from_dict(d: dict):
    d = dict(d)
    kind = d.pop("type", "power")
    if kind == "power":
        return PowerLaw(**d)
    if kind == "table":
        return Table(**d)
    raise ValueError(f"unknown map type {kind!r}")
