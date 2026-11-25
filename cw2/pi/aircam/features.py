"""From a camera frame to the instrument's reading y: one number per frame.

Built from NumPy whole-array operations on a small region of interest, so a frame costs well under the 8 ms a
120 fps camera allows (the week 6 lab measured ~3 ms for a colour mask on a third of the frame, 8–9 ms on the
whole 1332x990 frame). Pass the Y plane of a YUV420 frame, or one channel, as `gray`.

Two extractors, for the two instruments we are likely to build:
  Pendulum   a marker on a body hanging (or hinged) in the flow; reading = tan of its deflection angle, which is
             exactly ∝ v² for a sphere on a light rod (drag ∝ v², tanθ = drag / weight), so map p = 2.
  Rotor      a marker on a rotor (cups, Savonius, propeller); reading = revolutions per second from the slope of
             its unwrapped angle over a short window, ∝ v above start-up, so map p = 1. Unambiguous only while it
             turns less than half a revolution per frame (fps / 2 rev/s); more markers or a slower rotor if not.

Angles are taken relative to the line between two fixed fiducial dots when they are given, so a camera that
rolls or shifts on its mount does not move the zero.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass
class Roi:
    x: int
    y: int
    w: int
    h: int

    def crop(self, img: np.ndarray) -> np.ndarray:
        return img[self.y:self.y + self.h, self.x:self.x + self.w]


def chroma(bgr: np.ndarray, channel: int) -> np.ndarray:
    """How much one colour channel (0 B, 1 G, 2 R) stands out over the other two: a colour-marker image."""
    c = bgr.astype(np.int16)
    others = np.delete(c, channel, axis=2).max(axis=2)
    return np.clip(c[..., channel] - others, 0, 255).astype(np.uint8)


def centroid(gray: np.ndarray, threshold: float, dark: bool = False, min_pixels: int = 4):
    """Sub-pixel centroid (x, y) of the pixels above threshold (below it when dark), weighted by how far past
    the threshold they are; None when fewer than min_pixels qualify (marker lost, occluded, out of the ROI)."""
    a = gray.astype(np.float32)
    w = threshold - a if dark else a - threshold
    np.maximum(w, 0.0, out=w)
    if np.count_nonzero(w) < min_pixels:
        return None
    m = float(w.sum())
    cx = float(w.sum(axis=0) @ np.arange(w.shape[1], dtype=np.float32)) / m
    cy = float(w.sum(axis=1) @ np.arange(w.shape[0], dtype=np.float32)) / m
    return cx, cy


def angle(frm, to) -> float:
    """Angle of the vector frm → to, in image coordinates (x right, y down), radians."""
    return math.atan2(to[1] - frm[1], to[0] - frm[0])


def wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


@dataclass
class Pendulum:
    """Marker centroid in roi → deflection from rest about the pivot → reading tan(θ) (or θ)."""
    roi: Roi
    pivot: tuple                    # full-frame pixel coordinates
    threshold: float = 200.0
    dark: bool = False
    rest: float = math.pi / 2       # marker straight below the pivot (image y points down)
    fiducials: tuple | None = None  # ((x, y), (x, y)) of two fixed dots, when the frame should be referenced
    fiducial_roi: tuple | None = None   # (Roi, Roi) around each fiducial, if they are tracked every frame
    output: str = "tan"             # tan | angle

    def __post_init__(self):
        self._f0 = tuple(complex(*f) for f in self.fiducials) if self.fiducials else None

    def _frame(self, gray):
        """(pivot, roll) in this frame. With tracked fiducials, the similarity transform (shift, roll, scale)
        that carries the setup fiducials onto where they are now moves the pivot and the rest angle with the
        camera; otherwise the configured pivot and no roll."""
        if not (self._f0 and self.fiducial_roi):
            return self.pivot, 0.0
        now = []
        for r in self.fiducial_roi:
            c = centroid(r.crop(gray), self.threshold, self.dark)
            if c is None:
                return self.pivot, 0.0
            now.append(complex(c[0] + r.x, c[1] + r.y))
        a = (now[1] - now[0]) / (self._f0[1] - self._f0[0])
        pv = a * complex(*self.pivot) + (now[0] - a * self._f0[0])
        return (pv.real, pv.imag), math.atan2(a.imag, a.real)

    def __call__(self, gray: np.ndarray, t: float | None = None):
        c = centroid(self.roi.crop(gray), self.threshold, self.dark)
        if c is None:
            return None
        pivot, roll = self._frame(gray)
        theta = wrap(angle(pivot, (c[0] + self.roi.x, c[1] + self.roi.y)) - self.rest - roll)
        theta = abs(theta)                       # drag pushes one way; the sign depends on which way the camera faces
        return math.tan(min(theta, 1.4)) if self.output == "tan" else theta


@dataclass
class Rotor:
    """Marker centroid in roi → angle about the hub → unwrapped → rev/s over a sliding window."""
    roi: Roi
    hub: tuple
    threshold: float = 200.0
    dark: bool = False
    markers: int = 1                # identical markers spaced evenly: each step is ambiguous by 1/markers rev
    window: float = 0.1             # s of history for the slope

    def __post_init__(self):
        self._hist: deque = deque()
        self._last: float | None = None
        self._acc = 0.0

    def __call__(self, gray: np.ndarray, t: float):
        c = centroid(self.roi.crop(gray), self.threshold, self.dark)
        if c is None:
            return None
        a = angle(self.hub, (c[0] + self.roi.x, c[1] + self.roi.y)) * self.markers
        if self._last is not None:
            self._acc += wrap(a - self._last)
        self._last = a
        self._hist.append((t, self._acc))
        while self._hist and t - self._hist[0][0] > self.window:
            self._hist.popleft()
        if len(self._hist) < 3:
            return None
        tt, aa = np.array(self._hist).T
        tt = tt - tt.mean()
        slope = float(tt @ (aa - aa.mean()) / (tt @ tt))           # rad/s of the marker pattern
        return abs(slope) / (2 * math.pi * self.markers)          # rev/s of the rotor


def from_config(d: dict):
    d = dict(d)
    kind = d.pop("kind")
    d["roi"] = Roi(*d["roi"])
    if "fiducial_roi" in d and d["fiducial_roi"]:
        d["fiducial_roi"] = tuple(Roi(*r) for r in d["fiducial_roi"])
    for key in ("pivot", "hub"):
        if key in d:
            d[key] = tuple(d[key])
    if d.get("fiducials"):
        d["fiducials"] = tuple(tuple(p) for p in d["fiducials"])
    if kind == "pendulum":
        return Pendulum(**d)
    if kind == "rotor":
        return Rotor(**d)
    raise ValueError(f"unknown feature kind {kind!r}")
