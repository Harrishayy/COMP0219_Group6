"""From a camera frame to one number: how far the ball has moved from where it hangs in still air, in mm.

Setup (`find_ball`, once, fan off) finds the ball as the biggest round bright blob in the frame and measures it: its
rest position, its radius (a ping pong ball is 40 mm, so the radius sets the mm per pixel), and the grey levels of
the ball and of what is around it. Nothing about the scene is assumed beyond "a round thing brighter than its
surroundings" (`dark` flips that), so the same code tracks the real ball and the screen simulator's.

Every frame (`Ball`), the centre is found by a shrinking mean shift from where it was last: the soft-thresholded
centroid inside a disc of 2, 1.5, then 1.25 radii. The disc keeps the straw, the pillars and glints out of the
centroid; the soft threshold (0 at the midpoint grey, 1 half the contrast above it) gives sub-pixel positions from
the ball's edge. Only a window around the ball is touched, sub-sampled so the ball spans ~32 pixels, so a frame
costs well under a millisecond however close the camera is. If the ball is not where it was (occluded, a glint, a
camera knock), a coarse search over the whole frame finds it again; until then the frame gives no reading and the
estimator just skips it.

The reading is the distance from the rest position, which needs neither the hinge's position in the image nor a
level camera: the map (models.Pendulum) turns the chord into an angle.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

BALL_MM = 40.0


def otsu(values: np.ndarray) -> float:
    """The grey level that best splits values into two classes (Otsu's method on a 256-bin histogram)."""
    h = np.bincount(np.clip(np.asarray(values).ravel(), 0, 255).astype(np.int64), minlength=256).astype(float)
    p = h / max(h.sum(), 1.0)
    w0 = np.cumsum(p)
    mu = np.cumsum(p * np.arange(256))
    between = (mu[-1] * w0 - mu) ** 2 / np.maximum(w0 * (1.0 - w0), 1e-12)
    best = np.flatnonzero(between >= between.max() * (1 - 1e-9))       # an empty gap ties: take its middle
    return 0.5 * float(best[0] + best[-1]) + 0.5


def label(mask: np.ndarray) -> np.ndarray:
    """Connected components (4-neighbour) of a boolean image: 0 for background, otherwise the flat index + 1 of
    one pixel of the component. Minimum propagation with pointer jumping; for setup only, not per frame."""
    h, w = mask.shape
    big = h * w + 1
    lab = np.where(mask, np.arange(1, h * w + 1).reshape(h, w), 0)
    while True:
        m = np.where(mask, lab, big)
        n = m.copy()
        np.minimum(n[1:], m[:-1], out=n[1:])
        np.minimum(n[:-1], m[1:], out=n[:-1])
        np.minimum(n[:, 1:], m[:, :-1], out=n[:, 1:])
        np.minimum(n[:, :-1], m[:, 1:], out=n[:, :-1])
        n = np.where(mask, n, 0)
        flat = n.ravel()
        for _ in range(2):                       # a label names a pixel of the same component: follow it
            on = flat > 0
            flat[on] = flat[flat[on] - 1]
        n = flat.reshape(h, w)
        if np.array_equal(n, lab):
            return lab
        lab = n


def _erode(on: np.ndarray, n: int) -> np.ndarray:
    for _ in range(n):
        e = on.copy()
        e[1:] &= on[:-1]
        e[:-1] &= on[1:]
        e[:, 1:] &= on[:, :-1]
        e[:, :-1] &= on[:, 1:]
        on = e
    return on


def find_ball(gray: np.ndarray, dark: bool = False, near=None):
    """(x, y, r) in pixels of the biggest round blob brighter than its surroundings (darker, with dark), or None.
    near=(x, y) prefers the blob closest to that point instead of the biggest.

    The blob is looked for at several grey levels, from the one that splits the frame from its background up to
    near the brightest pixel: at a low level a light straw or frame can join the ball into one long shape, at a
    higher one the ball stands alone. The biggest round shape at any level wins (a highlight on the ball is
    round too, but smaller)."""
    g = np.asarray(gray, np.float32)
    if dark:
        g = 255.0 - g
    step = max(1, min(g.shape) // 240)
    c = g[::step, ::step]
    lo, hi = otsu(c), float(np.percentile(c, 99.9))
    best, best_key = None, None
    for level in np.linspace(lo, lo + 0.85 * max(hi - lo, 0.0), 6):
        lab = label(_erode(c > level, 2))         # erode: cut a thin straw that joins the ball to the frame
        ids, area = np.unique(lab[lab > 0], return_counts=True)
        for i, a in zip(ids, area):
            if a < 6:
                continue
            ys, xs = np.nonzero(lab == i)
            w, h = int(np.ptp(xs)) + 1, int(np.ptp(ys)) + 1
            fill = a / (w * h)                   # a disc fills π/4 = 0.785 of its bounding box
            if min(w, h) / max(w, h) < 0.7 or not 0.55 < fill < 0.95:
                continue
            x, y = (xs.mean() + 0.5) * step - 0.5, (ys.mean() + 0.5) * step - 0.5
            r = (math.sqrt(a / math.pi) + 2) * step  # + the erosion
            key = -math.hypot(x - near[0], y - near[1]) if near else a
            if best_key is None or key > best_key:
                best, best_key = (x, y, r), key
    if best is None:
        return None
    return measure(gray, *best, dark=dark)


def measure(gray: np.ndarray, x: float, y: float, r: float, dark: bool = False):
    """Refine a rough (x, y, r): grey levels of the ball and its surroundings, the threshold halfway, then the
    centre by mean shift and the radius from the area above the threshold. Returns (x, y, r)."""
    g = np.asarray(gray, np.float32)
    if dark:
        g = 255.0 - g
    for _ in range(3):
        y0, y1 = max(int(y - 3 * r), 0), min(int(y + 3 * r) + 2, g.shape[0])
        x0, x1 = max(int(x - 3 * r), 0), min(int(x + 3 * r) + 2, g.shape[1])
        win = g[y0:y1, x0:x1]
        dist = np.hypot(np.arange(x0, x1)[None, :] - x, np.arange(y0, y1)[:, None] - y)
        inside, ring = win[dist < 0.6 * r], win[(dist > 1.6 * r) & (dist < 2.4 * r)]
        if not inside.size or not ring.size:
            return None
        fg, bg = float(np.median(inside)), float(np.median(ring))
        thr = 0.5 * (fg + bg)
        on = (win > thr) & (dist < 1.25 * r)
        if on.sum() < 5:
            return None
        r = math.sqrt(on.sum() / math.pi)
        ys, xs = np.nonzero(on)
        x, y = xs.mean() + x0, ys.mean() + y0
    return x, y, r


def levels(gray: np.ndarray, x: float, y: float, r: float, dark: bool = False):
    """(ball grey, surrounding grey) medians, in the polarity the tracker works in."""
    g = np.asarray(gray, np.float32)
    if dark:
        g = 255.0 - g
    y0, y1 = max(int(y - 3 * r), 0), min(int(y + 3 * r) + 2, g.shape[0])
    x0, x1 = max(int(x - 3 * r), 0), min(int(x + 3 * r) + 2, g.shape[1])
    win = g[y0:y1, x0:x1]
    dist = np.hypot(np.arange(x0, x1)[None, :] - x, np.arange(y0, y1)[:, None] - y)
    return float(np.median(win[dist < 0.6 * r])), float(np.median(win[(dist > 1.6 * r) & (dist < 2.4 * r)]))


@dataclass
class Ball:
    """Per-frame tracker. Call it with the Y plane: returns d in mm (None when the ball is not found)."""
    rest: tuple                     # (x, y) px: the ball's centre in still air
    radius: float                   # px
    fg: float                       # ball grey level
    bg: float                       # surrounding grey level
    ball_mm: float = BALL_MM
    dark: bool = False

    def __post_init__(self):
        self.rest = (float(self.rest[0]), float(self.rest[1]))
        self.pos = self.rest
        self.mm_per_px = self.ball_mm / (2.0 * self.radius)
        self.thr = 0.5 * (self.fg + self.bg)
        self.band = max(0.25 * abs(self.fg - self.bg), 1.0)     # soft edge: thr → 0, thr + band → 1
        self.step = max(1, int(self.radius // 16))              # work on the ball at ~16 px radius
        self.mass = None                                        # weight of the ball at rest, set by the first fix
        self.lost = 0

    def _weights(self, win):
        a = win.astype(np.float32)
        a = (self.thr - a) if self.dark else (a - self.thr)
        return np.clip(a * (1.0 / self.band), 0.0, 1.0)

    def _centroid(self, gray, x, y, rad):
        s = self.step
        y0, y1 = max(int(y - rad), 0), min(int(y + rad) + 2, gray.shape[0])
        x0, x1 = max(int(x - rad), 0), min(int(x + rad) + 2, gray.shape[1])
        if y1 <= y0 or x1 <= x0:
            return None
        w = self._weights(gray[y0:y1:s, x0:x1:s])
        ys = np.arange(y0, y1, s, dtype=np.float32)
        xs = np.arange(x0, x1, s, dtype=np.float32)
        w *= ((xs[None, :] - x) ** 2 + (ys[:, None] - y) ** 2) <= rad * rad
        m = float(w.sum())
        if m <= 0.0:
            return None
        return float(w.sum(axis=0) @ xs) / m, float(w.sum(axis=1) @ ys) / m, m * s * s

    def _shift(self, gray, x, y):
        r = self.radius
        m = 0.0
        for k in (2.0, 1.5, 1.25):
            c = self._centroid(gray, x, y, k * r)
            if c is None:
                return None
            x, y, m = c
        return x, y, m

    def _search(self, gray):
        """Coarse search of the whole frame: the window of the ball's size holding the most ball-bright pixels."""
        k = max(1, int(self.radius / 3))
        on = self._weights(gray[::k, ::k]) > 0.5
        n = max(1, int(round(2 * self.radius / k)))
        if on.shape[0] <= n or on.shape[1] <= n:
            return None
        ii = np.pad(on.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
        box = ii[n:, n:] - ii[:-n, n:] - ii[n:, :-n] + ii[:-n, :-n]
        j, i = np.unravel_index(int(np.argmax(box)), box.shape)
        if box[j, i] < 0.4 * math.pi / 4 * n * n:
            return None
        return (i + n / 2) * k, (j + n / 2) * k

    def locate(self, gray):
        """The ball's centre (x, y) in px, or None."""
        c = self._shift(gray, *self.pos)
        if c is not None and self.mass is not None and not 0.5 < c[2] / self.mass < 1.5:
            c = None
        if c is None:
            self.lost += 1
            guess = self._search(gray)
            c = None if guess is None else self._shift(gray, *guess)
            if c is None or (self.mass is not None and not 0.5 < c[2] / self.mass < 1.5):
                return None
        if self.mass is None:
            self.mass = c[2]
        self.pos = (c[0], c[1])
        return self.pos

    def __call__(self, gray, t=None):
        c = self.locate(gray)
        if c is None:
            return None
        return math.hypot(c[0] - self.rest[0], c[1] - self.rest[1]) * self.mm_per_px


def setup(frames, dark: bool = False, near=None, ball_mm: float = BALL_MM):
    """From still-air frames: (Ball, median frame, still-air jitter in mm), or (None, median frame, nan).

    The ball is found and measured on the median frame; its rest position is then the median of where the tracker
    itself puts it, frame by frame, so that a shaded ball's centroid offset is in the rest position too."""
    frames = [np.asarray(f) for f in frames]
    still = np.median(np.stack(frames), axis=0).astype(np.uint8)
    found = find_ball(still, dark=dark, near=near)
    if found is None:
        return None, still, float("nan")
    x, y, r = found
    fg, bg = levels(still, x, y, r, dark)
    b = Ball(rest=(x, y), radius=r, fg=fg, bg=bg, ball_mm=ball_mm, dark=dark)
    pos = np.array([b.locate(f) or (np.nan, np.nan) for f in frames], float)
    rest = np.nanmedian(pos, axis=0)
    jitter = float(np.nanstd(np.hypot(*(pos - rest).T))) * b.mm_per_px
    return Ball(rest=(float(rest[0]), float(rest[1])), radius=r, fg=fg, bg=bg, ball_mm=ball_mm, dark=dark), still, jitter


def to_config(b: Ball) -> dict:
    return {"rest": [round(b.rest[0], 3), round(b.rest[1], 3)], "radius_px": round(b.radius, 3),
            "fg": round(b.fg, 1), "bg": round(b.bg, 1), "ball_mm": b.ball_mm, "dark": b.dark}


def from_config(d: dict) -> Ball:
    return Ball(rest=tuple(d["rest"]), radius=float(d["radius_px"]), fg=float(d["fg"]), bg=float(d["bg"]),
                ball_mm=float(d.get("ball_mm", BALL_MM)), dark=bool(d.get("dark", False)))


def save_image(path, gray, marks=()):
    """Save a grayscale frame with circles drawn on it, for checking setup: PNG through OpenCV when it is there
    (it is on the Pi, from apt), else a PGM that needs nothing. marks: (x, y, r) circles. Returns the path."""
    img = np.asarray(gray, np.uint8)
    try:
        import cv2
        out = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        for x, y, r in marks:
            cv2.circle(out, (int(round(x)), int(round(y))), max(int(round(r)), 2), (0, 0, 255), 2)
        cv2.imwrite(str(path), out)
        return str(path)
    except ImportError:
        out = img.copy()
        for x, y, r in marks:
            a = np.linspace(0, 2 * math.pi, 400)
            xx = np.clip(np.round(x + r * np.cos(a)).astype(int), 0, out.shape[1] - 1)
            yy = np.clip(np.round(y + r * np.sin(a)).astype(int), 0, out.shape[0] - 1)
            out[yy, xx] = 255 - out[yy, xx] // 2
        path = str(path).rsplit(".", 1)[0] + ".pgm"
        with open(path, "wb") as f:
            f.write(f"P5 {out.shape[1]} {out.shape[0]} 255\n".encode() + out.tobytes())
        return path
