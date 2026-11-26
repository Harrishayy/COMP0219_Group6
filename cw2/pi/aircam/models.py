"""The calibration map: how far the ball has moved from rest (d, mm) to airspeed (m/s).

The instrument is a ping pong ball on a straw, hung from a hinge between two pillars. In steady air the drag on the
ball and its weight balance about the hinge, tan θ = drag / weight = (½ρ·Cd·A / m·g)·v², so v = k·√tan θ. The
camera measures the chord d the ball's centre has moved from where it hangs in still air, d = 2·arm·sin(θ/2), which
needs no pivot position in the image and stays sensitive at large angles (at 55° the chord still grows at 0.88 of
the arc, where the horizontal shift alone grows at 0.57).

Two numbers: k (m/s) and arm (mm, hinge to ball centre, in the same mm as d). A bare 2.7 g, 40 mm ball has
k = √(2·m·g / (ρ·Cd·A)) ≈ 8.6 m/s; the straw's weight and drag move it, so k (and arm, which also absorbs the
straw) are fitted from a tunnel run (`aircam fit`). The map has `speed(d)`, its inverse `reading(v)` and
`dreading_dv(v)` (the Kalman filter uses the slope to put its process noise in m/s terms).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np

THETA_MAX = math.radians(80.0)      # past this the ball is near the downstream pillar: clamp, do not extrapolate


@dataclass
class Pendulum:
    k: float = 8.6                  # m/s at tan θ = 1
    arm_mm: float = 150.0           # hinge to ball centre
    d0: float = 0.0                 # reading in still air (setup measures the rest position, so 0)
    type: str = field(default="pendulum", init=False)

    def theta(self, d):
        s = np.clip((np.asarray(d, float) - self.d0) / (2.0 * self.arm_mm), 0.0, math.sin(THETA_MAX / 2))
        return 2.0 * np.arcsin(s)

    def speed(self, d):
        return self.k * np.sqrt(np.tan(self.theta(d)))

    def reading(self, v):
        th = np.minimum(np.arctan((np.maximum(np.asarray(v, float), 0.0) / self.k) ** 2), THETA_MAX)
        return self.d0 + 2.0 * self.arm_mm * np.sin(th / 2.0)

    def dreading_dv(self, v):
        s = np.maximum(np.asarray(v, float), 1e-6) / self.k
        th = np.arctan(s * s)
        return self.arm_mm * np.cos(th / 2.0) * (2.0 * s / self.k) / (1.0 + s ** 4)


def to_dict(m) -> dict:
    d = asdict(m)
    d["type"] = m.type
    return d


def from_dict(d: dict):
    d = dict(d)
    kind = d.pop("type", "pendulum")
    if kind == "pendulum":
        return Pendulum(**d)
    raise ValueError(f"unknown map type {kind!r}")
