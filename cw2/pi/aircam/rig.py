"""The pendulum the screen simulator animates: a ping pong ball on a straw, hinged at the top, in the airflow.

The physics is deliberately richer than anything the Pi assumes, so the Pi has to calibrate against recorded
truth rather than inherit the simulator's model:
  - the full nonlinear pendulum: gravity ∝ sin θ, drag on the ball from the relative velocity vector (air minus
    the ball's own motion), so it damps itself in wind and barely at all in still air;
  - crossflow drag on the straw's normal component, lumped at the middle of its exposed length;
  - a ball Cd that drifts mildly with Reynolds number, so tan θ is not exactly ∝ v²;
  - the ball's own spin inertia (2/5 m r²), the straw's (m L²/3), the air it drags along (added mass, ½ρV) and
    its buoyancy (1.5 % of a 2.7 g ball's weight);
  - viscous friction at the pivot, and turbulence: multiplicative Ornstein–Uhlenbeck noise on the air speed.
    These are the small, fast eddies near the ball (1–2.5 %, 30–100 ms); the fan's slow unsteadiness is in the
    demo profile already, and the pitot, sitting elsewhere, sees the mean flow, not these eddies.

Every parameter of a rig comes from a seeded RNG, so a rig is repeatable (calibration carries over between runs)
but not known to the Pi. θ = 0 hangs straight down; positive is downstream (flow left to right, side view).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

D_BALL = 0.040          # m, a regulation ping pong ball
G = 9.81                # m/s²
MU = 1.81e-5            # Pa·s, air at ~20 °C
RE_REF = 2.0e4          # the Reynolds number cd_ball is quoted at (≈ 7.5 m/s for a 40 mm ball)
RE_MIN = 2.0e3          # below this the drag is negligible anyway; keeps Re**cd_re finite in still air
DT = 0.001              # s, the integrator's longest substep (1 kHz)


@dataclass
class Rig:
    """The hidden physical parameters of one pendulum. Rig() is a typical one; Rig.from_seed(n) draws one."""
    ld: float = 3.7             # straw length, hinge to ball centre, in ball diameters
    m_ball: float = 2.7e-3      # kg
    m_straw: float = 0.45e-3    # kg
    d_straw: float = 6.0e-3     # m
    cd_ball: float = 0.48       # at RE_REF
    cd_re: float = 0.02         # Cd ∝ Re**cd_re: sphere drag dips near Re 1e4 and climbs towards 1e5
    cd_straw: float = 1.1       # crossflow, a smooth cylinder at Re ~ 1e3
    pivot: float = 5.0e-5       # N·m·s, viscous friction at the hinge
    turb: float = 0.018         # turbulence intensity, σ of v / mean v
    turb_tau: float = 0.06      # s, turbulence correlation time
    rho: float = 1.2            # kg/m³
    seed: int | None = None

    @classmethod
    def from_seed(cls, seed: int) -> "Rig":
        r = np.random.default_rng(seed)

        def u(lo, hi):
            return float(r.uniform(lo, hi))

        return cls(ld=u(3.2, 4.2), m_ball=u(2.6e-3, 2.8e-3), m_straw=u(0.3e-3, 0.6e-3), d_straw=u(5e-3, 7e-3),
                   cd_ball=u(0.45, 0.52), cd_re=u(-0.03, 0.06), cd_straw=u(1.0, 1.2), pivot=u(3.0e-5, 8.0e-5),
                   turb=u(0.01, 0.025), turb_tau=u(0.03, 0.1), seed=int(seed))

    @property
    def length(self) -> float:
        """m, hinge to ball centre."""
        return self.ld * D_BALL


class Pendulum:
    """θ and θ' of one rig, advanced with RK4 in substeps of at most 1 ms. Scalar maths (math, not NumPy):
    one pendulum at a time is ~20 µs a substep, fast enough to run live and to replay a whole demo offline."""

    def __init__(self, rig: Rig, theta: float = 0.0, omega: float = 0.0):
        self.rig, self.theta, self.omega = rig, float(theta), float(omega)
        r = D_BALL / 2
        vol = 4 / 3 * math.pi * r ** 3
        L = rig.length
        self._L = L
        self._ls = (L - r) / 2                                   # middle of the straw outside the ball
        self._inertia = (rig.m_ball * (L * L + 0.4 * r * r) + rig.m_straw * L * L / 3
                         + 0.5 * rig.rho * vol * L * L)          # ball, straw, added mass of air
        self._k_grav = ((rig.m_ball - rig.rho * vol) * L + rig.m_straw * L / 2) * G   # weight less buoyancy
        self._c_ball = 0.5 * rig.rho * math.pi * r * r * rig.cd_ball
        self._c_straw = 0.5 * rig.rho * rig.cd_straw * rig.d_straw * (L - r)
        self._re_per_mps = rig.rho * D_BALL / MU

    def accel(self, theta: float, omega: float, v: float) -> float:
        """θ'' for air speed v (m/s, left to right)."""
        s, c = math.sin(theta), math.cos(theta)
        L, rig = self._L, self.rig
        ux, uy = v - L * omega * c, -L * omega * s               # air relative to the ball
        q = math.hypot(ux, uy)
        cd = (max(q * self._re_per_mps, RE_MIN) / RE_REF) ** rig.cd_re
        ball = L * self._c_ball * cd * q * (v * c - L * omega)   # r × F reduces to L·|u|·(u normal to the straw)
        un = v * c - self._ls * omega
        straw = self._ls * self._c_straw * abs(un) * un
        return (ball + straw - self._k_grav * s - rig.pivot * omega) / self._inertia

    def step(self, dt: float, v_air: float) -> float:
        """Advance dt seconds with the air held at v_air (substeps ≤ 1 ms); return θ."""
        if dt <= 0:
            return self.theta
        n = max(1, math.ceil(dt / DT - 1e-9))
        h = dt / n
        th, om, f = self.theta, self.omega, self.accel
        for _ in range(n):
            a1 = f(th, om, v_air)
            om2 = om + h / 2 * a1
            a2 = f(th + h / 2 * om, om2, v_air)
            om3 = om + h / 2 * a2
            a3 = f(th + h / 2 * om2, om3, v_air)
            om4 = om + h * a3
            a4 = f(th + h * om3, om4, v_air)
            th += h / 6 * (om + 2 * om2 + 2 * om3 + om4)
            om += h / 6 * (a1 + 2 * a2 + 2 * a3 + a4)
        self.theta, self.omega = th, om
        return th


def simulate(t, v_air, rig: Rig, theta0: float = 0.0, omega0: float = 0.0) -> np.ndarray:
    """θ(t) for air speed v_air sampled at times t, each sample held until the next (as the live loop holds
    each 5 ms tick's air). Same integrator as Pendulum.step, so offline runs match the screen."""
    t, v = np.asarray(t, float), np.asarray(v_air, float)
    p = Pendulum(rig, theta0, omega0)
    out = np.empty_like(t)
    out[0] = p.theta
    for i in range(1, t.size):
        out[i] = p.step(t[i] - t[i - 1], v[i - 1])
    return out


def steady_theta(v, rig: Rig) -> np.ndarray:
    """The angle where drag torque balances gravity at steady air speed v (radians; any array shape)."""
    v = np.asarray(v, float)
    p = Pendulum(rig)
    torque = np.vectorize(lambda th, vv: p.accel(th, 0.0, vv))
    lo, hi = np.zeros_like(v), np.full_like(v, math.pi / 2)
    for _ in range(50):                                          # net torque falls monotonically in θ
        mid = (lo + hi) / 2
        up = torque(mid, v) > 0
        lo, hi = np.where(up, mid, lo), np.where(up, hi, mid)
    return (lo + hi) / 2


def pluck(rig: Rig, theta0_deg: float = 25.0, seconds: float = 15.0, dt: float = DT):
    """A free decay in still air from theta0_deg, as `aircam pluck` measures one: (t, θ)."""
    t = np.arange(0.0, seconds, dt)
    return t, simulate(t, np.zeros_like(t), rig, theta0=math.radians(theta0_deg))


class Gust:
    """Unit-variance Ornstein–Uhlenbeck noise with correlation time tau, stepped exactly for any spacing.
    Draws from its RNG in the same order as wind(), so the live loop and an offline replay agree."""

    def __init__(self, tau: float, seed: int):
        self.tau, self._rng = tau, np.random.default_rng(seed)
        self.n, self._t = float(self._rng.standard_normal()), None

    def at(self, t: float) -> float:
        if self._t is not None:
            a = math.exp(-(t - self._t) / self.tau)
            self.n = a * self.n + math.sqrt(1.0 - a * a) * float(self._rng.standard_normal())
        self._t = t
        return self.n


def wind(t, v_profile, rig: Rig, seed: int = 0) -> np.ndarray:
    """The air the ball feels: the profile with turbulence, v·(1 + i·n(t)), n unit-variance OU noise,
    never below zero. Still air stays still (the turbulence is multiplicative)."""
    t, v = np.asarray(t, float), np.asarray(v_profile, float)
    rng = np.random.default_rng(seed)
    x = float(rng.standard_normal())
    a = np.exp(-np.diff(t) / rig.turb_tau)
    kick = np.sqrt(1.0 - a * a) * rng.standard_normal(a.size)
    n = np.empty(t.size)
    n[0] = x
    for i in range(a.size):                                      # a recursion: no closed form in NumPy
        x = a[i] * x + kick[i]
        n[i + 1] = x
    return np.maximum(v * (1.0 + rig.turb * n), 0.0)
