"""From camera-rate readings to a speed at every 200 Hz output tick.

The camera gives a reading y at its frame rate (≤ 148 fps on the HQ camera, so never 200 Hz), stamped with the
middle of its exposure. The output needs a speed at every 5 ms tick, and the logger stamps a line when it
arrives, so every millisecond between the air moving and the line leaving counts as lag against the pitot.

Every estimator here has the same two calls:
  update(t, y)   a reading y taken at time t (seconds, camera clock)
  value(t)       the speed in m/s at time t, for any t at or after the last reading (an output tick)

Hold       zero-order hold of the last reading through the map. Simple; lags by half a frame on average.
Linear     linear interpolation between the last two readings, evaluated one frame period in the past so it
           never extrapolates. Smooth, but a whole frame of extra lag.
Kalman     a Kalman filter on the reading with a model of the instrument and of how the air changes, predicted
           forward to each tick. With instrument="none" it is a constant-rate tracker (the reading follows the
           air instantly). With "first" (time constant tau) or "second" (natural frequency fn, damping zeta) it
           estimates the reading the instrument *would* settle to (u, the static-equivalent reading) from the one
           it shows now, which takes out the instrument's own lag and ringing. Speed is map(u).

The process noise is set in m/s units (q_v, (m/s)²/s³: how hard the air can change its acceleration) and
converted to reading units with the map's slope at the current speed, so one value works across 2–10 m/s.
"""
from __future__ import annotations

import math

import numpy as np


def expm(a: np.ndarray) -> np.ndarray:
    """Matrix exponential by scaling and squaring a Taylor series: accurate for the small matrices here."""
    a = np.asarray(a, float)
    norm = float(np.max(np.sum(np.abs(a), axis=1))) if a.size else 0.0
    s = max(0, int(math.ceil(math.log2(norm))) + 1) if norm > 0.5 else 0
    a = a / (2.0 ** s)
    out = np.eye(a.shape[0])
    term = np.eye(a.shape[0])
    for k in range(1, 18):
        term = term @ a / k
        out = out + term
    for _ in range(s):
        out = out @ out
    return out


class Hold:
    def __init__(self, model):
        self.model, self.y = model, None

    def update(self, t, y):
        if y is not None and np.isfinite(y):
            self.y = y

    def value(self, t):
        return 0.0 if self.y is None else float(self.model.speed(self.y))


class Linear:
    def __init__(self, model, period: float):
        self.model, self.delay = model, period
        self.pts: list[tuple[float, float]] = []

    def update(self, t, y):
        if y is not None and np.isfinite(y):
            self.pts = (self.pts + [(t, y)])[-3:]

    def value(self, t):
        if not self.pts:
            return 0.0
        ts, ys = zip(*self.pts)
        return float(self.model.speed(np.interp(t - self.delay, ts, ys)))   # clamps at both ends


class Kalman:
    """Linear Kalman filter on [instrument states…, u, du/dt]; u is the static-equivalent reading."""

    def __init__(self, model, sigma_y: float, q_v: float = 10.0, instrument: str = "none",
                 fn: float = 3.0, zeta: float = 0.2, tau: float = 0.1, gate: float = 5.0,
                 max_rejects: int = 5, horizon: float = 0.1, lead: float = 0.0, v_max: float = 15.0):
        self.model, self.r = model, sigma_y ** 2
        self.q_v, self.gate, self.max_rejects = q_v, gate, max_rejects
        self.horizon, self.lead, self.v_max = horizon, lead, v_max
        if instrument == "none":            # x = [u, u']
            a = np.array([[0.0, 1.0], [0.0, 0.0]])
        elif instrument == "first":         # x = [y, u, u']:  y' = (u − y)/tau
            a = np.array([[-1 / tau, 1 / tau, 0.0], [0.0, 0.0, 1.0], [0.0, 0.0, 0.0]])
        elif instrument == "second":        # x = [y, y', u, u']:  y'' = w²(u − y) − 2ζw y'
            w = 2 * math.pi * fn
            a = np.array([[0.0, 1.0, 0.0, 0.0], [-w * w, -2 * zeta * w, w * w, 0.0],
                          [0.0, 0.0, 0.0, 1.0], [0.0, 0.0, 0.0, 0.0]])
        else:
            raise ValueError(f"unknown instrument model {instrument!r}")
        n = a.shape[0]
        self.a, self.n = a, n
        self.iu = n - 2                     # index of u
        self.h = np.zeros(n)
        self.h[0] = 1.0                     # the camera sees y (= u when instrument is "none")
        self.g = np.zeros(n)
        self.g[-1] = 1.0                    # noise drives u''
        self.x: np.ndarray | None = None
        self.p: np.ndarray | None = None
        self.t = 0.0
        self.rejects = 0
        self._cache: dict[int, tuple[np.ndarray, np.ndarray]] = {}

    # -- discretisation, cached per 10 µs step ------------------------------------------------------------------
    def _fq(self, dt: float):
        key = int(round(dt * 1e5))
        hit = self._cache.get(key)
        if hit is None:
            n, dt = self.n, key / 1e5
            m = np.zeros((2 * n, 2 * n))    # Van Loan: Q for unit noise intensity
            m[:n, :n] = -self.a
            m[:n, n:] = np.outer(self.g, self.g)
            m[n:, n:] = self.a.T
            e = expm(m * dt)
            f = e[n:, n:].T
            hit = (f, f @ e[:n, n:])
            if len(self._cache) > 4096:
                self._cache.clear()
            self._cache[key] = hit
        return hit

    def _q(self) -> float:
        """Process noise in reading units: q_v through the map's slope at the current speed."""
        v = float(self.model.speed(self.x[self.iu])) if self.x is not None else 0.0
        return self.q_v * float(self.model.dreading_dv(max(v, 1.0))) ** 2

    def _reset(self, t, y):
        self.x = np.zeros(self.n)
        self.x[0] = y
        self.x[self.iu] = y
        self.p = np.eye(self.n) * self.r
        self.p[-1, -1] = 1e3 * self.r
        if self.n == 4:
            self.p[1, 1] = 1e3 * self.r
        self.t, self.rejects = t, 0

    def update(self, t, y):
        if y is None or not np.isfinite(y):
            return
        if self.x is None:
            return self._reset(t, y)
        dt = t - self.t
        if dt > 0:
            f, q1 = self._fq(dt)
            self.x = f @ self.x
            self.p = f @ self.p @ f.T + self._q() * q1
            self.t = t
        innov = y - self.h @ self.x
        ph = self.p @ self.h
        s = self.h @ ph + self.r
        if innov * innov > self.gate ** 2 * s:      # a glint, an occlusion, a lost marker: skip it
            self.rejects += 1
            if self.rejects <= self.max_rejects:
                return
            # It keeps happening, so the air really moved: stop trusting the state (inflate its covariance
            # along the reading and its rate) and take this reading, rather than reset to it, so the
            # instrument model still separates where the instrument is from where it is heading.
            big = innov * innov
            for i in {0, self.iu}:
                self.p[i, i] += big
            self.p[-1, -1] += big / max(dt, 1e-3) ** 2
            ph = self.p @ self.h
            s = self.h @ ph + self.r
        self.rejects = 0
        k = ph / s
        self.x = self.x + k * innov
        ikh = np.eye(self.n) - np.outer(k, self.h)
        self.p = ikh @ self.p @ ikh.T + np.outer(k, k) * self.r

    def value(self, t):
        if self.x is None:
            return 0.0
        dt = min(max(t + self.lead - self.t, 0.0), self.horizon)
        u = (self._fq(dt)[0] @ self.x)[self.iu] if dt > 0 else self.x[self.iu]
        return float(min(max(self.model.speed(u), 0.0), self.v_max))

    def reading_estimate(self) -> float:
        return float("nan") if self.x is None else float(self.x[self.iu])


def make(name: str, model, period: float, **kw):
    """An estimator by name: hold, linear, kalman (kw: sigma_y, q_v, instrument, fn, zeta, tau, lead, …)."""
    if name == "hold":
        return Hold(model)
    if name == "linear":
        return Linear(model, period)
    if name == "kalman":
        return Kalman(model, **kw)
    raise ValueError(f"unknown estimator {name!r}")
