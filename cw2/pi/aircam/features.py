"""Find the ping pong ball with OpenCV, then follow it every frame: how far it has moved from rest, in mm.

Detection (once, `aircam real detect`, fan off), the standard OpenCV way to find a ball:
  1. Circles: cv2.HoughCircles (HOUGH_GRADIENT_ALT) on the blurred grey frame finds round edges. It needs a sharp
     edge, so an out-of-focus ball is weak or missing: the focus reading on screen says when to turn the lens.
  2. Check: for each circle, the ball's colour is learnt from its inside (HSV), and a colour mask (cv2.inRange)
     of the frame must fill the circle with little of that colour around it. A circle that fails says why. The
     biggest circle that passes is the ball.
  3. You confirm it by eye before anything tracks.

Tracking (every frame, `Ball`), the course lab's per-frame method (week 6, script 3.2) on a small window:
  cv2.cvtColor to HSV and cv2.inRange with the ball's colour, inside a disc of 1.3 radii round where the ball was
  (that keeps the straw, pillars and reflections out), then cv2.moments for the centroid, re-centred until it
  settles so it keeps up with a fast swing. Only ~5 radii square is touched, a fraction of a millisecond at 120 fps.
  If the ball is not there (its area is off by more than half), the whole frame is searched at quarter size for
  the round blob of its colour; until it is found the frame gives no reading.

The reading is the distance from the rest position: it needs neither the hinge's position in the image nor a level
camera. A ping pong ball is 40 mm, so its radius in pixels sets the mm per pixel.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

BALL_MM = 40.0


def _cv2():
    import cv2          # python3-opencv from apt on the Pi; opencv-python-headless on a laptop
    return cv2


def bgr(img):
    """Colour frames pass through; a grey one becomes colour."""
    img = np.asarray(img)
    if img.ndim == 3:
        return img
    cv2 = _cv2()
    return cv2.cvtColor(np.ascontiguousarray(img, np.uint8), cv2.COLOR_GRAY2BGR)


# -- the ball's colour --------------------------------------------------------------------------------------------
def colour_range(hsv_inside: np.ndarray, hsv_ring: np.ndarray):
    """HSV bounds (lo, hi) that take the ball and leave its surroundings: by hue for a coloured ball (orange), by
    brightness for a white one. lo[0] > hi[0] means the hue range wraps through red."""
    s_in, v_in = hsv_inside[:, 1].astype(float), hsv_inside[:, 2].astype(float)
    v_ring = hsv_ring[:, 2].astype(float)
    if np.median(s_in) < 70:                               # white (or grey): brighter than what is around it
        v_lo = 0.5 * (np.percentile(v_in, 5) + np.percentile(v_ring, 95))
        return (0, 0, int(max(v_lo, 25))), (179, int(min(255, np.percentile(s_in, 95) + 50)), 255)
    h = hsv_inside[:, 0].astype(float) * (2 * math.pi / 180)
    centre = math.atan2(np.sin(h).mean(), np.cos(h).mean()) % (2 * math.pi)
    spread = np.abs((h - centre + math.pi) % (2 * math.pi) - math.pi) * (180 / (2 * math.pi))
    dh = float(np.clip(np.percentile(spread, 95) + 6, 8, 25))
    c = centre * 180 / (2 * math.pi)
    lo_h, hi_h = int(round(c - dh)) % 180, int(round(c + dh)) % 180
    return ((lo_h, int(max(40, 0.6 * np.percentile(s_in, 5))), int(max(30, 0.5 * np.percentile(v_in, 5)))),
            (hi_h, 255, 255))


def in_range(hsv, lo, hi):
    """cv2.inRange, with a hue range that may wrap through red."""
    cv2 = _cv2()
    if lo[0] <= hi[0]:
        return cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
    return (cv2.inRange(hsv, np.array(lo, np.uint8), np.array((179, hi[1], hi[2]), np.uint8))
            | cv2.inRange(hsv, np.array((0, lo[1], lo[2]), np.uint8), np.array(hi, np.uint8)))


def _ring(shape, x, y, r_in, r_out):
    yy, xx = np.ogrid[:shape[0], :shape[1]]
    d2 = (xx - x) ** 2 + (yy - y) ** 2
    return (d2 >= r_in * r_in) & (d2 < r_out * r_out)


def edge_width(gray, x, y, r) -> float:
    """How many pixels the ball's edge takes to go from background to ball: ~1–2 px in focus, more when blurred.
    The brightness step across the rim divided by its steepest slope, along 48 rays from the centre."""
    g = np.asarray(gray, np.float32)
    a = np.linspace(0, 2 * math.pi, 48, endpoint=False)
    t = np.linspace(0.6, 1.4, 33) * r
    xs = np.clip(x + np.outer(np.cos(a), t), 0, g.shape[1] - 1.001)
    ys = np.clip(y + np.outer(np.sin(a), t), 0, g.shape[0] - 1.001)
    x0, y0 = np.floor(xs).astype(int), np.floor(ys).astype(int)
    fx, fy = xs - x0, ys - y0
    prof = (g[y0, x0] * (1 - fx) * (1 - fy) + g[y0, x0 + 1] * fx * (1 - fy) + g[y0 + 1, x0] * (1 - fx) * fy
            + g[y0 + 1, x0 + 1] * fx * fy)                     # bilinear samples along each ray
    step = t[1] - t[0]
    rise = np.abs(prof[:, :4].mean(1) - prof[:, -4:].mean(1))  # inside to outside, per ray
    slope = np.abs(np.diff(prof, axis=1)).max(1) / step       # steepest change per pixel
    ok = rise > 10
    if ok.sum() < 8:
        return float("inf")
    return float(np.median(rise[ok] / np.maximum(slope[ok], 1e-6)))


# -- detection ----------------------------------------------------------------------------------------------------
@dataclass
class Candidate:
    x: float
    y: float
    r: float
    ok: bool = False
    why: str = ""
    ball: "Ball | None" = None
    edge_px: float = float("inf")
    sharp: bool = False
    score: dict = field(default_factory=dict)


def circles(img, max_n: int = 8) -> list:
    """Round edges in the frame, strongest first: (x, y, r) px. Worked on a copy at most 640 px wide."""
    cv2 = _cv2()
    gray = cv2.cvtColor(bgr(img), cv2.COLOR_BGR2GRAY)
    s = min(1.0, 640.0 / max(gray.shape))
    small = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else gray
    small = cv2.GaussianBlur(small, (0, 0), 1.0)
    rmax = int(min(small.shape) / 3)
    for strength in (300, 150, 75):                     # sharp, strong edges first; weaker ones only if none
        c = cv2.HoughCircles(small, cv2.HOUGH_GRADIENT_ALT, dp=1.5, minDist=8, param1=strength, param2=0.75,
                             minRadius=5, maxRadius=rmax)
        if c is not None:
            return [(float(x) / s, float(y) / s, float(r) / s) for x, y, r in c[0][:max_n]]
    return []


def check(img, x, y, r, ball_mm: float = BALL_MM) -> Candidate:
    """Is this circle a ball whose colour the tracker can follow? Learns its colour, masks a window round it with
    that colour, and wants the circle filled by it with little of it around."""
    cv2 = _cv2()
    img = bgr(img)
    cand = Candidate(x, y, r)
    h, w = img.shape[:2]
    x0, y0 = max(int(x - 2.4 * r), 0), max(int(y - 2.4 * r), 0)
    x1, y1 = min(int(x + 2.4 * r) + 2, w), min(int(y + 2.4 * r) + 2, h)
    win = img[y0:y1, x0:x1]
    lx, ly = x - x0, y - y0                                         # the circle in window coordinates
    hsv = cv2.cvtColor(win, cv2.COLOR_BGR2HSV)
    inside, ring = _ring(hsv.shape, lx, ly, 0, 0.7 * r), _ring(hsv.shape, lx, ly, 1.5 * r, 2.2 * r)
    if inside.sum() < 20 or ring.sum() < 20:
        cand.why = "too close to the edge of the image"
        return cand
    if np.linalg.norm(np.median(win[inside], axis=0).astype(float) - np.median(win[ring], axis=0)) < 30:
        cand.why = "inside looks like its surroundings (a ring or an edge)"
        return cand
    lo, hi = colour_range(hsv[inside], hsv[ring])
    mask = in_range(hsv, lo, hi) > 0
    cover = float(mask[_ring(hsv.shape, lx, ly, 0, r)].mean())      # how much of the circle the colour fills
    spill = float(mask[ring].mean())                                # how much of the surroundings has it too
    ys, xs = np.nonzero(mask & _ring(hsv.shape, lx, ly, 0, 1.3 * r))
    off = math.hypot(xs.mean() - lx, ys.mean() - ly) / r if xs.size else 9.9
    cand.score = {"cover": cover, "spill": spill, "off": off}
    cand.edge_px = edge_width(cv2.cvtColor(win, cv2.COLOR_BGR2GRAY), lx, ly, r)
    cand.sharp = cand.edge_px <= max(3.0, 0.12 * r)
    if cover < 0.75:
        cand.why = "colour does not fill it"
    elif spill > 0.15:
        cand.why = "background is the same colour"
    elif off > 0.2:
        cand.why = "colour not centred on it"
    else:
        fg, bg_ = np.median(win[inside], axis=0), np.median(win[ring], axis=0)
        cx, cy = float(xs.mean()) + x0, float(ys.mean()) + y0
        cand.ok = True
        cand.ball = Ball(rest=(cx, cy), radius=r, lo=lo, hi=hi, area=float(xs.size), ball_mm=ball_mm,
                         fg=tuple(float(v) for v in fg), bg=tuple(float(v) for v in bg_),
                         outline=(float(x - cx), float(y - cy)))
    return cand


def blob_at(img, x, y) -> Candidate | None:
    """The one-coloured blob under a clicked point, as a circle to check: for a ball the circle finder missed
    (blurred, or low contrast at its edge)."""
    cv2 = _cv2()
    img = bgr(img)
    h, w = img.shape[:2]
    xi, yi = int(np.clip(round(x), 3, w - 4)), int(np.clip(round(y), 3, h - 4))
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    hh, ss, vv = (float(v) for v in np.median(hsv[yi - 3:yi + 4, xi - 3:xi + 4].reshape(-1, 3), axis=0))
    if ss < 70:
        lo, hi = (0, 0, int(max(vv - 40, 25))), (179, int(min(255, ss + 60)), 255)
    else:
        lo, hi = (int(hh - 12) % 180, int(max(40, 0.5 * ss)), int(max(30, 0.5 * vv))), (int(hh + 12) % 180, 255, 255)
    mask = cv2.morphologyEx(in_range(hsv, lo, hi), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(mask)
    k = int(lab[yi, xi])
    if k == 0:
        return None
    return check(img, float(cent[k][0]), float(cent[k][1]), math.sqrt(stats[k, cv2.CC_STAT_AREA] / math.pi))


def detect(img, near=None) -> list:
    """Every circle in the frame, checked: the ball first (the biggest that passes), then the rest, so the screen
    can show why the others were turned down. With `near` (a click), the circle there comes first, whether it
    passed or not; if the circle finder found none there, the one-coloured blob under the click is checked."""
    found = [check(img, *c) for c in circles(img)]
    if near is None:
        return sorted(found, key=lambda c: (not c.ok, -c.r))
    dist = lambda c: math.hypot(c.x - near[0], c.y - near[1])                     # noqa: E731
    pick = min(found, key=dist) if found else None
    if pick is None or dist(pick) > 1.5 * pick.r:
        pick = blob_at(img, *near) or Candidate(float(near[0]), float(near[1]), 0.0,
                                                why="nothing round and of one colour here")
        found.append(pick)
    return [pick] + [c for c in found if c is not pick]


def find_ball(img, near=None):
    """(x, y, r) of the detected ball, or None."""
    c = detect(img, near)
    return (c[0].x, c[0].y, c[0].r) if c and c[0].ok else None


# -- tracking -----------------------------------------------------------------------------------------------------
@dataclass
class Ball:
    """Per-frame tracker. Call it with a frame (BGR): returns d in mm, or None when the ball is not found."""
    rest: tuple                     # (x, y) px: the ball's centre in still air, as this tracker measures it
    radius: float                   # px, from the circle detection (sets the mm per pixel)
    lo: tuple                       # HSV lower bound of the ball's colour (hue wraps through red when lo > hi)
    hi: tuple                       # HSV upper bound
    area: float = 0.0               # px of the ball's colour inside 1.3 radii at rest (0: set by the first fix)
    ball_mm: float = BALL_MM
    fg: tuple = ()                  # the ball's colour (B, G, R) and its surroundings', for reports
    bg: tuple = ()
    outline: tuple = (0.0, 0.0)     # px from the tracked centre to the circle's centre (for drawing)

    def __post_init__(self):
        self.rest = (float(self.rest[0]), float(self.rest[1]))
        self.lo, self.hi = tuple(int(v) for v in self.lo), tuple(int(v) for v in self.hi)
        self.pos = self.rest
        self.mm_per_px = self.ball_mm / (2.0 * self.radius)
        self.lost = 0
        n = int(math.ceil(1.3 * self.radius))
        yy, xx = np.ogrid[-n:n + 1, -n:n + 1]
        self._n = n
        self._disc = ((xx * xx + yy * yy) <= (1.3 * self.radius) ** 2).astype(np.uint8) * 255
        self.contrast = float(np.linalg.norm(np.subtract(self.fg, self.bg))) if self.fg and self.bg else 0.0

    def _moments(self, frame, x, y):
        """Centroid and area of the ball's colour within 1.3 radii of (x, y)."""
        cv2 = _cv2()
        n = self._n
        xi, yi = int(round(x)), int(round(y))
        x0, y0, x1, y1 = xi - n, yi - n, xi + n + 1, yi + n + 1
        h, w = frame.shape[:2]
        cx0, cy0, cx1, cy1 = max(x0, 0), max(y0, 0), min(x1, w), min(y1, h)
        if cx1 <= cx0 or cy1 <= cy0:
            return None
        roi = frame[cy0:cy1, cx0:cx1]
        if roi.ndim == 2:
            roi = cv2.cvtColor(np.ascontiguousarray(roi), cv2.COLOR_GRAY2BGR)
        mask = in_range(cv2.cvtColor(roi, cv2.COLOR_BGR2HSV), self.lo, self.hi)
        mask &= self._disc[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0]
        m = cv2.moments(mask, binaryImage=True)
        if m["m00"] <= 0:
            return None
        return cx0 + m["m10"] / m["m00"], cy0 + m["m01"] / m["m00"], m["m00"]

    def _shift(self, frame, x, y):
        c = None
        for _ in range(4):                                  # re-centre: a fast swing moves it a good part of r
            c = self._moments(frame, x, y)
            if c is None:
                return None
            if abs(c[0] - x) < 0.5 and abs(c[1] - y) < 0.5:   # same window again: same answer
                break
            x, y = c[0], c[1]
        return c

    def _search(self, frame):
        """The whole frame at quarter size: the round blob of the ball's colour and size, nearest where it was."""
        cv2 = _cv2()
        s = 0.25
        small = cv2.resize(bgr(frame), None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        mask = in_range(cv2.cvtColor(small, cv2.COLOR_BGR2HSV), self.lo, self.hi)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        contours = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]
        want = math.pi * (self.radius * s) ** 2
        best = None
        for c in contours:
            a = cv2.contourArea(c)
            if not 0.4 * want < a < 1.8 * want:
                continue
            p = cv2.arcLength(c, True)
            if p <= 0 or 4 * math.pi * a / (p * p) < 0.5:
                continue
            (cx, cy), _ = cv2.minEnclosingCircle(c)
            d = math.hypot(cx / s - self.pos[0], cy / s - self.pos[1])
            if best is None or d < best[0]:
                best = (d, cx / s, cy / s)
        return None if best is None else best[1:]

    def _plausible(self, c):
        return c is not None and (self.area <= 0 or 0.5 < c[2] / self.area < 1.5)

    def locate(self, frame):
        """The ball's centre (x, y) in px, or None."""
        c = self._shift(frame, *self.pos)
        if not self._plausible(c):
            self.lost += 1
            guess = self._search(frame)
            c = None if guess is None else self._shift(frame, *guess)
            if not self._plausible(c):
                return None
        if self.area <= 0:
            self.area = c[2]
        self.pos = (c[0], c[1])
        return self.pos

    def __call__(self, frame, t=None):
        c = self.locate(frame)
        if c is None:
            return None
        return math.hypot(c[0] - self.rest[0], c[1] - self.rest[1]) * self.mm_per_px


def settle(b: Ball, frames, cand: Candidate | None = None):
    """(Ball, median frame, still-air jitter mm): the rest position and the colour area at rest, measured by the
    tracker itself over still-air frames, so whatever offset it has is in the rest position too."""
    frames = [bgr(f) for f in frames]
    still = np.median(np.stack(frames), axis=0).astype(np.uint8)
    pos = [c for c in (b._shift(f, *b.pos) for f in frames) if c is not None]
    if not pos:
        return None, still, float("nan")
    p = np.array(pos)
    rest = np.median(p[:, :2], axis=0)
    jitter = float(np.std(np.hypot(*(p[:, :2] - rest).T))) * b.mm_per_px
    cx, cy = (cand.x, cand.y) if cand is not None else (rest[0] + b.outline[0], rest[1] + b.outline[1])
    out = Ball(rest=(float(rest[0]), float(rest[1])), radius=b.radius, lo=b.lo, hi=b.hi,
               area=float(np.median(p[:, 2])), ball_mm=b.ball_mm, fg=b.fg, bg=b.bg,
               outline=(float(cx - rest[0]), float(cy - rest[1])))
    return out, still, jitter


def setup(frames, near=None, ball_mm: float = BALL_MM):
    """From still-air frames: (Ball, median frame, still-air jitter in mm), or (None, median frame, nan)."""
    frames = [bgr(f) for f in frames]
    still = np.median(np.stack(frames), axis=0).astype(np.uint8)
    found = detect(still, near)
    if not found or not found[0].ok:
        return None, still, float("nan")
    b = found[0].ball
    b.ball_mm, b.mm_per_px = ball_mm, ball_mm / (2.0 * b.radius)
    return settle(b, frames, found[0])


def to_config(b: Ball) -> dict:
    r = lambda v: [round(float(c), 1) for c in v]                         # noqa: E731
    return {"rest": [round(b.rest[0], 3), round(b.rest[1], 3)], "radius_px": round(b.radius, 3),
            "hsv_lo": list(b.lo), "hsv_hi": list(b.hi), "area_px": round(b.area, 1), "ball_mm": b.ball_mm,
            "fg": r(b.fg), "bg": r(b.bg), "outline": [round(b.outline[0], 3), round(b.outline[1], 3)]}


def from_config(d: dict) -> Ball:
    if "hsv_lo" not in d:
        raise SystemExit("this calibration was made by an older version: run detect again")
    return Ball(rest=tuple(d["rest"]), radius=float(d["radius_px"]), lo=tuple(d["hsv_lo"]), hi=tuple(d["hsv_hi"]),
                area=float(d.get("area_px", 0.0)), ball_mm=float(d.get("ball_mm", BALL_MM)),
                fg=tuple(d.get("fg", ())), bg=tuple(d.get("bg", ())), outline=tuple(d.get("outline", (0.0, 0.0))))


def save_image(path, img, marks=()):
    """Save a frame with circles drawn on it (green). marks: (x, y, r). Returns the path."""
    cv2 = _cv2()
    out = bgr(np.asarray(img, np.uint8)).copy()
    for x, y, r in marks:
        cv2.circle(out, (int(round(x)), int(round(y))), max(int(round(r)), 2), (0, 255, 0), 2)
    cv2.imwrite(str(path), out)
    return str(path)
