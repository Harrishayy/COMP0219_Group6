"""The wind tunnel on a screen: the Mac draws the pendulum, animates it with real physics, receives the Pi's
lines and scores them exactly as the Wind Tunnel Logger would.

The pendulum takes the left 62 % of the screen and nothing else is drawn there; a side panel on the right holds
the controls and the readout. Frame the camera on the pendulum only. The Pi must not inherit the simulator's
model, so:
- the rig's parameters are hidden (rig.py, drawn from --rig);
- the physics is the full nonlinear pendulum with turbulence;
- the drawing is shaded like a real object on a non-uniform background.

The Pi calibrates against each run's recorded truth, as it would against the pitot. The panel uses rectangles
only, so nothing in it looks like a ball.

Modes (buttons at the top of the panel):
  DEMO     plays the graded demo (demo.segments: protocol 1 levels, protocol 2 pulses, in order 1→2 or 2→1)
           at a set full speed, and draws the plan as a diagram with a cursor and the Pi's line over it.
  MANUAL   speed buttons; the air approaches each set speed as the tunnel's does, not instantly (τ 1.35 s
           spinning up from still, 4.85 s coasting to 0, 0.5 s between levels: demo.TAU_ON / OFF / PANEL).
START / STOP (SPACE) plays or aborts the demo, or starts and ends a MANUAL recording. Every run that ends is
saved to --out as the logger's _aligned.csv plus _truth.csv, _student.csv and _meta.json, and scored with
demo.score (2–10 m/s, the logger's 150 ms pairing). PLUCK (P) releases the ball from 25° in still air for a
15 s free decay; QUIT (Q, ESC) quits. KEYS lists the rest.

Clocks: physics runs on time.perf_counter, in 5 ms ticks of held air integrated at 1 kHz. Ground truth is the
mean flow at every tick, as a pitot elsewhere in the section reads it; the ball feels that plus the rig's small
fast eddies (rig.wind), which the Pi has to filter out, not report. Truth is stamped on this Mac's epoch clock
and the Pi's lines with time.time() on arrival, so, as with the logger, all latency counts as lag.

The screen adds lag of its own (vsync, compositor, panel: tens of ms) that the tunnel does not have. Measure it
(film the screen and a running clock in slow motion) and pass --display-lag, or the Pi gets tuned to lead the
screen rather than the air. --turb overrides the rig's turbulence intensity, to try harsher air.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import datetime
import json
import math
import os
import socket
import sys
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np

from . import demo
from .rig import D_BALL, Gust, Pendulum, Rig

TICK = 1.0 / 200            # s: the ground truth's rate, and how long each air sample is held
PLUCK_S, PLUCK_DEG = 15.0, 25.0
CHART_S = 20.0
SCENE_FRAC = 0.62           # of the screen's width for the pendulum; the panel takes the rest
SUB = 8                     # sub-pixel ball positions per pixel, so its motion is not quantised to whole pixels
MANUAL_SPEEDS = (0, 2, 3, 4, 5, 6, 7, 8, 9, 10)
SPEED_MAX, FULL_RANGE = 12.0, (2.0, 12.0)
CW2 = Path(__file__).resolve().parents[2]
OUT = CW2 / "data" / "sim"
ALIGNED_HEADER = ["timestamp_iso", "timestamp_epoch", "ground_truth_mps", "student_mps"]
KEYS = {
    "DEMO": "SPACE start / stop · 1 / 2 order · Up / Down full speed · TAB manual · P pluck · Q quit",
    "MANUAL": "SPACE start / stop recording · 0 still · 2–9 m/s · 1 = 10 m/s · Up / Down ±0.5 · TAB demo · "
              "P pluck · Q quit",
}

STRAW = (205, 200, 186, 120)        # light, translucent
STRAW_SHINE = (235, 232, 222, 40)
PIN, PIN_RIM = (52, 54, 59), (140, 142, 148)
PANEL_BG, DIVIDER = (21, 22, 25), (70, 72, 78)
LABEL, VALUE, WARN = (150, 152, 160), (236, 236, 240), (225, 120, 100)
BTN, BTN_ON, BTN_REC, BTN_EDGE = (44, 46, 52), (64, 92, 122), (128, 52, 50), (98, 100, 108)
GRID, AXIS = (40, 42, 47), (70, 72, 78)
TRUTH_C, PI_C, PLAN_C = (225, 225, 230), (240, 170, 60), (95, 97, 104)


def _pygame():
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    try:
        import pygame
        import pygame.gfxdraw  # noqa: F401  (translucent polygons blend straight onto the target)
    except ImportError as e:
        raise ImportError("the screen simulator needs pygame: pip install -r requirements-laptop.txt") from e
    return pygame


def _from_array(pg, a: np.ndarray, fmt: str):
    """A Surface copied from an H×W×(3|4) uint8 array."""
    a = np.ascontiguousarray(a, np.uint8)
    frombytes = getattr(pg.image, "frombytes", None) or pg.image.fromstring
    return frombytes(a.tobytes(), (a.shape[1], a.shape[0]), fmt)


# -- the scene ------------------------------------------------------------------------------------------------
def _smooth_noise(rng, h: int, w: int, cell: int) -> np.ndarray:
    """Unit-ish noise varying over `cell` pixels (bilinear between random grid points): blotches, not grain."""
    g = rng.standard_normal((h // cell + 2, w // cell + 2)).astype(np.float32)
    fy, fx = np.arange(h, dtype=np.float32) / cell, np.arange(w, dtype=np.float32) / cell
    iy, ix = fy.astype(int), fx.astype(int)
    ty, tx = (fy - iy)[:, None], (fx - ix)[None, :]
    top = g[iy][:, ix] * (1 - tx) + g[iy][:, ix + 1] * tx
    bot = g[iy + 1][:, ix] * (1 - tx) + g[iy + 1][:, ix + 1] * tx
    return top * (1 - ty) + bot * ty


def _bar(img, x0, x1, y0, y1, rgb, across: str, streak=None):
    """A shaded metal bar lit from the upper left: brightness varies across its width ('x', a pillar) or its
    height ('y', a crossbar), with a highlight a third of the way in; optional brushed streaks along it."""
    x0, x1, y0, y1 = (int(round(v)) for v in (x0, x1, y0, y1))
    n = (x1 - x0) if across == "x" else (y1 - y0)
    u = (np.arange(n, dtype=np.float32) + 0.5) / n
    shade = 0.55 + 0.4 * np.sin(np.pi * u) ** 0.7 + 0.2 * np.exp(-((u - 0.3) / 0.12) ** 2)
    prof = np.asarray(rgb, np.float32) * shade[:, None]
    img[y0:y1, x0:x1] = prof[None, :, :] if across == "x" else prof[:, None, :]
    if streak is not None:
        img[y0:y1, x0:x1] *= (1 + 0.05 * streak[x0:x1])[None, :, None]


def _ball_sprites(pg, d: float):
    """SUB×SUB shaded spheres d px across, sprite [j][k] centred at (c + k/SUB, c + j/SUB) of its own pixels:
    off-white, Lambert-lit from the upper left, a specular highlight, a darker limb, anti-aliased edge."""
    r = d / 2
    size = int(math.ceil(d)) + 4
    c = size // 2
    yy, xx = (np.mgrid[0:size, 0:size].astype(np.float32) + 0.5)
    light = np.array([-0.45, -0.6, 0.66], np.float32)
    light /= np.linalg.norm(light)
    half = light + np.array([0, 0, 1], np.float32)
    half /= np.linalg.norm(half)
    albedo = np.array([238, 234, 224], np.float32)
    rows = []
    for j in range(SUB):
        row = []
        for k in range(SUB):
            dx, dy = (xx - c - k / SUB) / r, (yy - c - j / SUB) / r
            rho2 = dx * dx + dy * dy
            cover = np.clip((1 - np.sqrt(rho2)) * r + 0.5, 0, 1)
            nz = np.sqrt(np.clip(1 - rho2, 0, 1))
            lam = np.clip(dx * light[0] + dy * light[1] + nz * light[2], 0, None)
            spec = np.clip(dx * half[0] + dy * half[1] + nz * half[2], 0, None) ** 40
            lum = (0.36 + 0.64 * lam) * (0.68 + 0.32 * nz)
            rgb = np.clip(albedo * lum[..., None] + 120 * spec[..., None], 0, 255)
            row.append(_from_array(pg, np.dstack([rgb, cover * 255]), "RGBA"))
        rows.append(row)
    return rows, c


class Scene:
    """The rig side on, flow left to right, to the rig's own proportions: a ball ball_px across on an arm
    arm_px long (hinge to ball centre), as large as fits width x height at scale 1, hinge centred. The live app
    passes the pendulum's area (the screen's left 62 %) and draws onto that part of the screen.

    Everything that does not move is baked into one background (a dark gradient with a vignette, soft blotches
    and grain; two pillars, a crossbar and a base plate). Each frame draws only the straw, the ball (a
    pre-shaded sprite picked by sub-pixel position) and the hinge pin. Needs no display: render_array draws
    off-screen, for tests that feed the Pi's tracker."""

    def __init__(self, width: int, height: int, rig: Rig, scale: float = 1.0, seed: int = 0):
        pg = self._pg = _pygame()
        self.size = w, h = int(width), int(height)
        self.ball_px = d = max(8.0, scale * min(h / 9, w / 12.5))     # 12.5 d spans the widest rig
        self.arm_px = L = rig.ld * d
        self.straw_px = max(1.5, rig.d_straw / D_BALL * d)
        top = (h - (L + 2.15 * d)) / 2
        self.hinge = (w / 2, top + 0.45 * d)
        hx = w / 2
        inner = L + 0.6 * d                     # pillar faces: beyond the ball's reach at any angle
        pw = 0.42 * d
        base_y = top + L + 1.7 * d              # 0.75 d below the ball at rest

        rng = np.random.default_rng(seed)
        y = np.linspace(0, 1, h, dtype=np.float32)[:, None]
        x = np.linspace(0, 1, w, dtype=np.float32)[None, :]
        img = np.array([34, 36, 41], np.float32) + np.array([-19, -20, -22], np.float32) * y[..., None]
        vignette = 1 - 0.38 * np.clip(((x - 0.5) / 0.5) ** 2 * 0.55 + ((y - 0.45) / 0.55) ** 2 * 0.45, 0, 1)
        blotch = 1 + 0.08 * _smooth_noise(rng, h, w, max(8, max(h, w) // 9))
        img = img * (vignette * blotch)[..., None]
        streak = _smooth_noise(rng, 1, w, 3)[0] + 0.5 * rng.standard_normal(w).astype(np.float32)
        for side in (-1, 1):
            x0 = hx + side * inner if side > 0 else hx - inner - pw
            _bar(img, x0, x0 + pw, top, base_y, (118, 120, 125), "x", streak)
        _bar(img, hx - inner - pw - 0.12 * d, hx + inner + pw + 0.12 * d, top, top + 0.35 * d, (124, 126, 131), "y")
        _bar(img, hx - 0.13 * d, hx + 0.13 * d, top + 0.35 * d, self.hinge[1] + 0.05 * d, (78, 80, 85), "x")
        bx0, bx1 = hx - inner - pw - 0.45 * d, hx + inner + pw + 0.45 * d
        _bar(img, bx0, bx1, base_y, base_y + 0.1 * d, (120, 122, 126), "y")          # the plate's lit top face
        _bar(img, bx0, bx1, base_y + 0.1 * d, base_y + 0.45 * d, (66, 68, 72), "y")   # its front face
        img += rng.normal(0.0, 1.6, (h, w, 1)).astype(np.float32)
        self._bg = _from_array(pg, np.clip(img, 0, 255), "RGB")
        self._sprites, self._c = _ball_sprites(pg, d)
        self._canvas = None

    def convert(self):
        """Match the display's pixel format once it is open: blits get several times faster."""
        self._bg = self._bg.convert()
        self._sprites = [[s.convert_alpha() for s in row] for row in self._sprites]

    def ball_xy(self, theta: float):
        """Where the ball's centre is drawn, in screen pixels (x right, y down)."""
        return self.hinge[0] + self.arm_px * math.sin(theta), self.hinge[1] + self.arm_px * math.cos(theta)

    def draw(self, surface, theta: float):
        pg = self._pg
        surface.blit(self._bg, (0, 0))
        hx, hy = self.hinge
        bx, by = self.ball_xy(theta)
        nx, ny = math.cos(theta) * self.straw_px / 2, -math.sin(theta) * self.straw_px / 2

        def strip(a, b, colour):                  # gfxdraw only: pygame.draw.aaline speckles on the display
            q = [(round(hx + a * nx), round(hy + a * ny)), (round(bx + a * nx), round(by + a * ny)),
                 (round(bx + b * nx), round(by + b * ny)), (round(hx + b * nx), round(hy + b * ny))]
            pg.gfxdraw.filled_polygon(surface, q, colour)
            pg.gfxdraw.aapolygon(surface, q, colour)

        strip(1, -1, STRAW)
        strip(-0.2, -0.55, STRAW_SHINE)           # a soft highlight on the lit (upper-left) side
        ix, iy = math.floor(bx), math.floor(by)
        k, j = round((bx - ix) * SUB), round((by - iy) * SUB)
        ix, k = (ix + 1, 0) if k == SUB else (ix, k)
        iy, j = (iy + 1, 0) if j == SUB else (iy, j)
        surface.blit(self._sprites[j][k], (ix - self._c, iy - self._c))
        rp = max(2, round(0.11 * self.ball_px))
        pg.gfxdraw.filled_circle(surface, round(hx), round(hy), rp, PIN)
        pg.gfxdraw.aacircle(surface, round(hx), round(hy), rp, PIN_RIM)

    def render_array(self, theta: float) -> np.ndarray:
        """The scene at angle theta as an H×W×3 RGB uint8 array, drawn off-screen."""
        if self._canvas is None:
            self._canvas = self._pg.Surface(self.size)
        self.draw(self._canvas, theta)
        tobytes = getattr(self._pg.image, "tobytes", None) or self._pg.image.tostring
        return np.frombuffer(bytearray(tobytes(self._canvas, "RGB")), np.uint8).reshape(self.size[1], self.size[0], 3)


# -- receiving the Pi's lines ---------------------------------------------------------------------------------
def _num(s: str):
    try:
        return float(s)
    except ValueError:
        return s


def parse(line: str):
    """One received line, read as the logger reads it: None for a blank or '#' line; the Pi's
    '# aircam key=value …' header as a dict; otherwise the last comma field as the speed (float). Raises
    ValueError for anything else, nan and inf included (they would leave the logger's MSE undefined)."""
    s = line.strip()
    if not s:
        return None
    if s.startswith("#"):
        if s == "# aircam" or s.startswith("# aircam "):
            return {k: _num(v) for k, eq, v in (kv.partition("=") for kv in s[9:].split()) if k and eq}
        return None
    v = float(s.split(",")[-1])
    if not math.isfinite(v):
        raise ValueError(f"not a finite speed: {s!r}")
    return v


def lan_ip() -> str:
    """This Mac's LAN address: the source the OS would use to reach the internet (a UDP connect sends nothing)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class Receiver:
    """The Pi's lines from UDP or a USB–serial adapter, read on a background thread and stamped with time.time()
    on arrival. `samples` only grows, as (epoch s, m/s); `header` is the latest '# aircam' dict."""

    def __init__(self):
        self.samples, self.header, self.bad, self.error, self.where = [], {}, 0, None, ""
        self.ip = self.port = None
        self._lock, self._stop = threading.Lock(), threading.Event()

    def feed(self, data: bytes, now: float):
        for raw in data.decode(errors="ignore").splitlines():
            try:
                x = parse(raw)
            except ValueError:
                self.bad += 1
                continue
            if isinstance(x, dict):
                self.header = x
            elif x is not None:
                with self._lock:
                    self.samples.append((now, x))

    def take(self, since: int = 0) -> list:
        with self._lock:
            return self.samples[since:]

    def udp(self, port: int = 5005, host: str = "0.0.0.0") -> int:
        """Listen for datagrams of newline-terminated lines; return the port (pass 0 for any free one)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.settimeout(0.2)
        self.ip, self.port = lan_ip(), sock.getsockname()[1]
        self.where = f"udp {self.ip}:{self.port}"
        threading.Thread(target=self._udp, args=(sock,), daemon=True).start()
        return self.port

    def _udp(self, sock):
        while not self._stop.is_set():
            try:
                data = sock.recv(65536)
            except socket.timeout:
                continue
            except OSError as e:
                self.error = str(e)
                break
            self.feed(data, time.time())
        sock.close()

    def serial(self, dev: str, baud: int = 115200):
        import serial
        ser = serial.Serial(dev, baud, timeout=0.2)
        self.where = f"serial {dev} at {baud}"
        threading.Thread(target=self._serial, args=(ser,), daemon=True).start()

    def _serial(self, ser):
        buf = b""
        while not self._stop.is_set():
            try:
                chunk = ser.read(ser.in_waiting or 1)
            except Exception as e:        # unplugged: say so on screen rather than die
                self.error = str(e)
                break
            if not chunk:
                continue
            now = time.time()
            done, _, buf = (buf + chunk).rpartition(b"\n")      # keep a partial line for the next read
            if done:
                self.feed(done, now)
        ser.close()

    def close(self):
        self._stop.set()


# -- saving and scoring a run ---------------------------------------------------------------------------------
def iso(t: float) -> str:
    """The logger's timestamp_iso: local time with its UTC offset, to the millisecond."""
    return datetime.datetime.fromtimestamp(t).astimezone().isoformat(timespec="milliseconds")


def write_aligned(path, tg, vg, ts, vs):
    """<run>_aligned.csv exactly as the Wind Tunnel Logger writes it: every ground-truth sample with the nearest
    Pi line within 150 ms by arrival time (demo.align), student_mps blank where none is that close."""
    tg, vg = np.asarray(tg, float), np.asarray(vg, float)
    paired = demo.align(tg, ts, vs) if len(ts) else np.full(tg.size, np.nan)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(ALIGNED_HEADER)
        for t, g, s in zip(tg, vg, paired):
            w.writerow([iso(t), f"{t:.6f}", f"{g:.6f}", f"{s:.6f}" if np.isfinite(s) else ""])


def _finite(d: dict) -> dict:
    return {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in d.items()}


def save_run(out, name: str, tg, vg, theta, ts, vs, meta: dict, v_air=None) -> dict:
    """Write <name>_aligned.csv, _truth.csv, _student.csv and _meta.json into out; return demo.score's dict.
    vg is the ground truth (the mean flow); v_air, the turbulent air the ball felt, goes in _truth.csv only."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    tg, vg, theta = (np.asarray(a, float) for a in (tg, vg, theta))
    v_air = vg if v_air is None else np.asarray(v_air, float)
    ts, vs = np.asarray(ts, float), np.asarray(vs, float)
    order = np.argsort(ts, kind="stable")
    ts, vs = ts[order], vs[order]
    write_aligned(out / f"{name}_aligned.csv", tg, vg, ts, vs)
    with open(out / f"{name}_truth.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_epoch", "v_true_mps", "theta_deg", "v_air_mps"])
        w.writerows([f"{t:.6f}", f"{v:.6f}", f"{d:.4f}", f"{x:.6f}"]
                    for t, v, d, x in zip(tg, vg, np.degrees(theta), v_air))
    with open(out / f"{name}_student.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_epoch", "value_mps"])
        w.writerows([f"{t:.6f}", f"{v:.6f}"] for t, v in zip(ts, vs))
    score = demo.score(tg, vg, ts, vs) if ts.size else {"n": 0, "mse": math.nan, "bias": math.nan, "sigma": math.nan}
    meta = {"name": name, **meta, "n_truth": int(tg.size), "n_student": int(ts.size), "score": _finite(score)}
    (out / f"{name}_meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return score


def score_text(s: dict) -> str:
    if not s["n"]:
        return "nothing to score: no Pi line within 150 ms of a 2–10 m/s truth sample"
    return f"MSE {s['mse']:.4f}  bias {s['bias']:+.3f}  σ {s['sigma']:.3f}  (n {s['n']}, 2–10 m/s)"


# -- the air and the run --------------------------------------------------------------------------------------
class Fan:
    """The tunnel's mean flow: a first-order approach to the set speed with the demo's time constants, so manual
    steps behave like the tunnel's (and the demo's segments are exactly demo.profile)."""

    def __init__(self):
        self.v = self.target = 0.0
        self.tau = demo.TAU_OFF

    def set(self, target: float):
        target = float(target)
        if target != self.target:
            self.tau = demo.TAU_OFF if target == 0 else demo.TAU_ON if self.target == 0 else demo.TAU_PANEL
            self.target = target

    def step(self, dt: float) -> float:
        self.v = self.target + (self.v - self.target) * math.exp(-dt / self.tau)
        return self.v


def plan(full: float, order: str):
    """The demo's segments (duration s, target m/s, tau s) as demo.segments gives them, with a label each."""
    segs = demo.segments(full=full, order=order)
    names = [""]
    for p in order:
        names += ["Full", "L2", "L1", "L2", "Full", "coast"] if p == "1" else ["pulses", "", "", "coast"]
    return segs, names if len(names) == len(segs) else [""] * len(segs)


class Live:
    """The air, the pendulum, the mode and the records: everything but the drawing, so tests can drive it with a
    fake clock. Physics runs on `clock` (monotonic seconds); truth is stamped on the epoch clock."""

    def __init__(self, a, rig: Rig, rx: Receiver, scene: Scene | None = None, clock=None):
        self.a, self.rig, self.rx, self.scene = a, rig, rx, scene
        perf0 = time.perf_counter()
        self.clock = clock or (lambda: time.perf_counter() - perf0)
        self.pend, self.fan = Pendulum(rig), Fan()
        self.mode, self.order, self.full = a.mode.upper(), a.order, a.full
        self.running = self.finished = False
        self.seed0 = int(time.time()) if a.seed is None else a.seed
        self.runs, self.gust = 0, Gust(rig.turb_tau, self.seed0 + 100_000)     # idle air; each run reseeds
        self.v = self.v_mean = 0.0
        self.t_phys = self.next_tick = self.clock()
        self.pluck_until = -math.inf
        self.chart_truth = deque(maxlen=int(CHART_S / TICK) + 50)
        self.chart_pi = deque(maxlen=int(2 * CHART_S / TICK))
        self.arrivals = deque()
        self.seen, self.last_pi, self.last_arrival = 0, math.nan, math.nan
        self.k, self.tg, self.vg, self.va, self.th, self.events = 0, [], [], [], [], []
        self.run_mode, self.run_plan, self.score, self.frames, self._scored_at = None, None, None, 0, 0.0
        self.last_name, self.last_aborted, self.result = None, False, ""   # the last saved run, its score in words
        self._sync()

    def _sync(self):
        """Re-anchor the clock to the epoch clock (at each run, so NTP slewing cannot accumulate)."""
        self.epoch0 = time.time() - self.clock()

    @property
    def plucking(self) -> bool:
        return self.t_phys < self.pluck_until

    @property
    def run_s(self) -> float:
        return self.k * TICK

    @property
    def demo_s(self) -> float:
        return self.edges[-1] * TICK if self.run_mode == "DEMO" else 0.0

    def _tick(self, t: float):
        """(mean flow, air at the ball) from tick t on, then the fan stepped to the next tick."""
        if self.running and self.run_mode == "DEMO":
            i = bisect.bisect_right(self.edges, self.k)
            if i < len(self.segs):
                _, self.fan.target, self.fan.tau = self.segs[i]
            else:
                self.running, self.finished = False, True       # finish() saves it after this frame
        v = self.fan.v
        air = max(v * (1.0 + self.rig.turb * self.gust.at(t)), 0.0)
        self.fan.step(TICK)
        return v, air

    def advance(self, now: float):
        """Integrate up to `now`, holding each tick's air for 5 ms; record the truth at every tick of a run."""
        if not self.running and now - self.t_phys > 1.0:          # a stall outside a run: just skip ahead
            self.t_phys = self.next_tick = now
        lag = self.a.display_lag
        while self.next_tick <= now:
            self.pend.step(self.next_tick - self.t_phys, self.v)
            self.t_phys = self.next_tick
            self.v_mean, self.v = self._tick(self.next_tick)
            te = self.epoch0 + self.next_tick + lag
            self.chart_truth.append((te, self.v_mean))
            if self.running:
                self.tg.append(te)
                self.vg.append(self.v_mean)
                self.va.append(self.v)
                self.th.append(self.pend.theta)
                self.k += 1
            self.next_tick += TICK
        self.pend.step(now - self.t_phys, self.v)
        self.t_phys = now

    def pull(self):
        """The Pi's new lines, for the charts, the rate and the latest value."""
        new = self.rx.take(self.seen)
        self.seen += len(new)
        self.chart_pi.extend(new)
        self.arrivals.extend(t for t, _ in new)
        if new:
            self.last_arrival, self.last_pi = new[-1]
        cut = time.time() - 1.0
        while self.arrivals and self.arrivals[0] < cut:
            self.arrivals.popleft()

    # -- what the buttons and keys do
    def press(self, name: str) -> bool:
        """Do what a button or key names ("start", "mode:MANUAL", "speed:6", "speed:+", ...); False to quit.
        The mode, order and full speed are fixed during a run; speed buttons work in MANUAL, recording or not."""
        kind, _, arg = name.partition(":")
        if kind == "quit":
            return False
        if kind == "start":
            self.finish(complete=self.run_mode == "MANUAL") if self.running else self.start()
        elif kind == "pluck":
            self.pluck()
        elif self.running and kind in ("mode", "order", "full"):
            pass
        elif kind == "mode" and arg != self.mode:
            self.mode = arg
            self.fan.set(0.0)
        elif kind == "order":
            self.order = arg
        elif kind == "full":
            self.full = min(max(self.full + (0.5 if arg == "+" else -0.5), FULL_RANGE[0]), FULL_RANGE[1])
        elif kind == "speed" and self.mode == "MANUAL":
            step = {"+": 0.5, "-": -0.5}.get(arg)
            self.fan.set(min(max(self.fan.target + step, 0.0), SPEED_MAX) if step else float(arg))
            if self.running:
                self.events.append((round(self.run_s, 3), self.fan.target))
        return True

    def start(self):
        now = self.clock()
        self.advance(now)
        self._sync()
        self.seed = self.seed0 + self.runs
        self.runs += 1
        self.gust = Gust(self.rig.turb_tau, self.seed)
        self.run_mode, self.pluck_until = self.mode, -math.inf
        if self.mode == "DEMO":
            self.segs, self.seg_names = plan(self.full, self.order)
            self.edges = np.cumsum([round(d / TICK) for d, _, _ in self.segs]).tolist()
            self.run_plan = (self.order, self.full)
        self.next_tick, self.k = now, 0
        self.tg, self.vg, self.va, self.th = [], [], [], []
        self.events = [(0.0, self.fan.target)]
        self.run_from, self.started, self.frames = len(self.rx.take(0)), time.time(), 0
        self.score, self.finished, self.running = None, False, True

    def finish(self, complete: bool):
        """End the run: save it and score it (unless it lasted under 2 s)."""
        self.running = self.finished = False
        if self.run_mode == "DEMO":
            self.fan.set(0.0)                                    # an aborted demo coasts down, as the fan would
        if len(self.tg) * TICK < 2.0:
            print("run stopped before 2 s: not saved", flush=True)
            return
        self.pull()
        st = [(t, v) for t, v in self.rx.take(self.run_from) if self.tg[0] - 1.0 <= t <= self.tg[-1] + 1.0]
        ts, vs = (np.array([s[i] for s in st], float) for i in (0, 1))
        name = "sim_" + time.strftime("%Y%m%d_%H%M%S", time.localtime(self.started))
        a, sc = self.a, self.scene
        meta = {"complete": complete, "mode": self.run_mode,
                "truth": "the mean flow (as a pitot reads it); _truth.csv v_air_mps is what the ball felt",
                "rig_seed": a.rig, "rig": asdict(self.rig), "turb_override": a.turb, "turbulence_seed": self.seed,
                "display_lag_s": a.display_lag, "start_epoch": self.tg[0], "duration_s": len(self.tg) * TICK,
                "frame_hz": self.frames / max(1e-9, time.time() - self.started), "source": self.rx.where,
                "bad_lines": self.rx.bad, "aircam": self.rx.header}
        if self.run_mode == "DEMO":
            meta.update(order=self.run_plan[0], full_mps=self.run_plan[1])
        else:
            meta["set_speeds"] = self.events                     # (run s, m/s) each time a speed was set
        if sc is not None:
            meta.update(screen_px=list(sc.size), scale=a.scale, ball_px=sc.ball_px, arm_px=sc.arm_px,
                        straw_px=sc.straw_px, hinge_px=list(sc.hinge))
        score = save_run(a.out, name, self.tg, self.vg, self.th, ts, vs, meta, v_air=self.va)
        self.score, self.last_name, self.last_aborted = score, name, not complete
        self.result = score_text(score) if ts.size else "no Pi lines arrived; saved the truth only"
        print(f"{name}{' (aborted)' if not complete else ''}: {self.result}\n  saved in {Path(a.out).resolve()}\n"
              f"  calibrate: {self.fit_command()}", flush=True)

    def fit_command(self) -> str:
        """How to calibrate from the last run (the path relative to cw2/, where ./aircam lives, if it is inside)."""
        p = (Path(self.a.out) / f"{self.last_name}_aligned.csv").resolve()
        try:
            p = p.relative_to(CW2)
        except ValueError:
            pass
        return f"./aircam fit {p}"

    def pluck(self):
        """Release the ball from 25° in still air (the air is stilled at once: a simulator convenience)."""
        if self.running:
            return
        self.fan.v = self.fan.target = self.v = self.v_mean = 0.0
        self.fan.tau = demo.TAU_OFF
        self.pend.theta, self.pend.omega = math.radians(PLUCK_DEG), 0.0
        self.pluck_until = self.t_phys + PLUCK_S

    def update_score(self):
        """The run's MSE, bias and n so far, twice a second (lines yet to arrive are unpaired, not wrong)."""
        if not self.running or time.time() - self._scored_at < 0.5 or len(self.tg) < 2:
            return
        self._scored_at = time.time()
        st = self.rx.take(self.run_from)
        if st:
            ts, vs = np.array(st, float).T
            self.score = demo.score(np.array(self.tg), np.array(self.vg), ts, vs)


# -- the panel ------------------------------------------------------------------------------------------------
def split(width: int) -> int:
    """The pendulum area's width; the panel takes the rest of the screen."""
    return round(width * SCENE_FRAC)


@dataclass
class Button:
    name: str           # what Live.press does with it; "text:…" is a slot for a value, not a button
    label: str
    rect: tuple         # x, y, w, h in screen pixels

    def hit(self, pos) -> bool:
        x, y, w, h = self.rect
        return not self.name.startswith("text:") and x <= pos[0] < x + w and y <= pos[1] < y + h


def panel_buttons(x0: int, w: int, h: int, mode: str):
    """The panel's buttons for a mode (rectangles only), and the y below the last row."""
    m, g, bh = max(8, round(h / 70)), max(4, round(h / 160)), max(24, round(h / 24))
    out = []

    def row(y, items):
        avail = w - 2 * m - g * (len(items) - 1)
        total, x = sum(i[2] for i in items), x0 + m
        for name, label, weight in items:
            bw = round(avail * weight / total)
            out.append(Button(name, label, (x, y, bw, bh)))
            x += bw + g
        return y + bh + g

    y = row(m, [("mode:DEMO", "DEMO", 1), ("mode:MANUAL", "MANUAL", 1)]) + g
    if mode == "DEMO":
        y = row(y, [("order:12", "1 then 2", 2), ("order:21", "2 then 1", 2), ("text:gap", "", 0.3),
                    ("full:-", "−", 1), ("text:full", "", 2.6), ("full:+", "+", 1)])
    else:
        y = row(y, [(f"speed:{v}", str(v), 1) for v in MANUAL_SPEEDS])
        y = row(y, [("speed:-", "−0.5", 1), ("text:speed", "", 1.8), ("speed:+", "+0.5", 1)])
    y = row(y + g, [("start", "START", 2), ("pluck", "PLUCK", 1), ("quit", "QUIT", 1)])
    return [b for b in out if b.name != "text:gap"], y


def key_action(key: str, mode: str) -> str | None:
    """The Live.press name for a key, by pygame.key.name (keypad digits come as "[5]"), or None."""
    k = key.strip("[]") if len(key) > 2 else key
    common = {"space": "start", "p": "pluck", "q": "quit", "escape": "quit", "d": "mode:DEMO",
              "m": "mode:MANUAL", "tab": "mode:" + ("MANUAL" if mode == "DEMO" else "DEMO")}
    if k in common:
        return common[k]
    if mode == "DEMO":
        return {"1": "order:12", "2": "order:21", "up": "full:+", "down": "full:-"}.get(k)
    if len(k) == 1 and k.isdigit():
        return f"speed:{10 if k == '1' else int(k)}"
    return {"up": "speed:+", "down": "speed:-"}.get(k)


def _font(pg, px: int):
    try:
        return pg.font.SysFont("helveticaneue,helvetica,arial", px)
    except Exception:            # no system fonts to scan: pygame's own
        return pg.font.Font(None, px)


def _fit(font, text: str, width: int) -> str:
    """text, with its middle elided to fit width (a long --out path)."""
    if font.size(text)[0] <= width:
        return text
    head, tail = text[:13], text[13:]
    while tail and font.size(head + "…" + tail)[0] > width:
        tail = tail[1:]
    return head + "…" + tail


def _wrap(font, text: str, width: int):
    """Lines of `text` split at " · " to fit width."""
    lines, cur = [], ""
    for part in text.split(" · "):
        nxt = f"{cur} · {part}" if cur else part
        if cur and font.size(nxt)[0] > width:
            lines.append(cur)
            nxt = part
        cur = nxt
    return lines + [cur]


class Panel:
    """The right-hand side: mode and controls, the truth against the Pi, charts, and what to type on the Pi."""

    def __init__(self, pg, x0: int, w: int, h: int):
        self.pg, self.x0, self.w, self.h = pg, x0, w, h
        self.m = max(8, round(h / 70))
        self.small, self.med, self.big = (_font(pg, max(n, round(h / d))) for n, d in ((12, 56), (15, 38), (28, 14)))
        self.mode, self._plan = None, None

    def layout(self, mode: str):
        if mode != self.mode:
            self.buttons, self.y_controls = panel_buttons(self.x0, self.w, self.h, mode)
            self.mode = mode

    def hit(self, pos) -> str | None:
        return next((b.name for b in self.buttons if b.hit(pos)), None)

    def _text(self, surface, font, text, x, y, colour=LABEL, centre_in=None):
        img = font.render(text, True, colour)
        if centre_in:
            bx, by, bw, bh = centre_in
            x, y = bx + (bw - img.get_width()) // 2, by + (bh - img.get_height()) // 2
        surface.blit(img, (x, y))
        return y + img.get_height()

    def _button(self, surface, b: Button, live: Live):
        pg = self.pg
        kind, _, arg = b.name.partition(":")
        if kind == "text":
            text = f"full {live.full:.1f} m/s" if arg == "full" else f"set {live.fan.target:.1f} m/s"
            return self._text(surface, self.med, text, 0, 0, VALUE, centre_in=b.rect)
        on = ((kind == "mode" and arg == live.mode) or (kind == "order" and arg == live.order)
              or (kind == "speed" and arg not in "+-" and float(arg) == live.fan.target)
              or (kind == "pluck" and live.plucking))
        fill = BTN_REC if kind == "start" and live.running else BTN_ON if on else BTN
        locked = live.running and kind in ("mode", "order", "full", "pluck")
        pg.draw.rect(surface, fill, b.rect)
        pg.draw.rect(surface, BTN_EDGE, b.rect, 1)
        label = ("STOP" if live.running else "START") if kind == "start" else b.label
        self._text(surface, self.med, label, 0, 0, LABEL if locked else VALUE, centre_in=b.rect)

    def _chart(self, surface, rect, x0, x1, vmax, series, cursor=None):
        """Speed against time in rect: grid at 2 and 10 m/s, each (xs, vs, colour) decimated to ~2 points/px."""
        pg = self.pg
        rx, ry, rw, rh = rect
        pg.draw.rect(surface, AXIS, rect, 1)
        for v in (2.0, 10.0):
            yy = ry + rh - round(v / vmax * rh)
            pg.draw.line(surface, GRID, (rx + 1, yy), (rx + rw - 2, yy))
            self._text(surface, self.small, f"{v:g}", rx + 3, yy - self.small.get_height(), AXIS)
        for xs, vs, colour in series:
            xs, vs = np.asarray(xs, float), np.asarray(vs, float)
            keep = (xs >= x0) & (xs <= x1)
            xs, vs = xs[keep], vs[keep]
            step = max(1, xs.size // (2 * rw))
            xs, vs = xs[::step], vs[::step]
            if xs.size > 1:
                px = rx + (xs - x0) / (x1 - x0) * (rw - 1)
                py = ry + rh - 1 - np.clip(vs, 0, vmax) / vmax * (rh - 2)
                pg.draw.lines(surface, colour, False, np.column_stack([px, py]).tolist())
        if cursor is not None and x0 <= cursor <= x1:
            cx = rx + (cursor - x0) / (x1 - x0) * (rw - 1)
            pg.draw.line(surface, VALUE, (cx, ry + 1), (cx, ry + rh - 2))

    def _diagram(self, surface, rect, live: Live, vmax):
        """The demo's plan (speed vs time, segments labelled), the cursor, and the run's truth and Pi so far."""
        key = (live.order, live.full)
        if self._plan is None or self._plan[0] != key:
            t, v = demo.profile(dt=0.05, full=live.full, order=live.order)
            segs, names = plan(live.full, live.order)
            self._plan = (key, t, v, np.cumsum([0.0] + [d for d, _, _ in segs]), names)
        _, t, v, starts, names = self._plan
        rx, ry, rw, rh = rect
        x1 = float(starts[-1])
        series = [(t, v, PLAN_C)]
        cursor = None
        if live.run_mode == "DEMO" and live.run_plan == key and live.tg:
            series.append((np.arange(len(live.vg)) * TICK, live.vg, TRUTH_C))
            st = live.rx.take(live.run_from)
            if st:
                ts, vs = np.array(st, float).T
                series.append((ts - live.tg[0], vs, PI_C))
            cursor = live.run_s if live.running else None
        self._chart(surface, rect, 0.0, x1, vmax, series, cursor)
        ends = [-1e9, -1e9]
        for s, name in zip(starts[:-1], names):
            x = rx + s / x1 * (rw - 1)
            self.pg.draw.line(surface, GRID, (x, ry + 1), (x, ry + rh - 2))
            if name:
                tw = self.small.size(name)[0]
                row = 0 if x > ends[0] + 4 else 1
                self._text(surface, self.small, name, x + 2, ry + 2 + row * self.small.get_height(), LABEL)
                ends[row] = x + 2 + tw

    def draw(self, surface, live: Live):
        pg, m, x0, w, h = self.pg, self.m, self.x0, self.w, self.h
        self.layout(live.mode)
        pg.draw.rect(surface, PANEL_BG, (x0, 0, w, h))
        pg.draw.line(surface, DIVIDER, (x0, 0), (x0, h), 2)
        for b in self.buttons:
            self._button(surface, b, live)
        inner, xl = w - 2 * m, x0 + m
        col = xl + inner // 2

        # the truth against the Pi
        y = self.y_controls + m
        self._text(surface, self.small, "Wind (truth)", xl, y)
        y = self._text(surface, self.small, "Pi estimate", col, y)
        pi_ok = math.isfinite(live.last_pi)
        self._text(surface, self.big, f"{live.v_mean:.2f}", xl, y, VALUE)
        y = self._text(surface, self.big, f"{live.last_pi:.2f}" if pi_ok else "—", col, y, VALUE if pi_ok else LABEL)
        diff = f"{live.last_pi - live.v_mean:+.2f} m/s" if pi_ok else "—"
        y = self._text(surface, self.med, f"m/s     Pi − truth {diff}", xl, y, LABEL) + 2
        y = self._text(surface, self.small, f"air at the ball {live.v:.2f} m/s (turbulent)",
                       xl, y)
        age = time.time() - live.last_arrival
        pi_line = (f"Pi {len(live.arrivals)} lines/s · last line {age:.2f} s ago" if math.isfinite(age)
                   else "no lines from the Pi yet")
        if live.rx.bad:
            pi_line += f" · {live.rx.bad} bad lines"
        y = self._text(surface, self.small, pi_line, xl, y, WARN if math.isfinite(age) and age > 1.0 else LABEL)
        if live.rx.error:
            y = self._text(surface, self.small, f"receiver stopped: {live.rx.error}", xl, y, WARN)
        if live.running:
            state = (f"DEMO {live.run_s:5.1f} / {live.demo_s:.0f} s" if live.run_mode == "DEMO"
                     else f"RECORDING {live.run_s:5.1f} s")
        elif live.plucking:
            state = f"PLUCK {live.t_phys - live.pluck_until + PLUCK_S:4.1f} / {PLUCK_S:.0f} s"
        else:
            state = "idle: press START" + (" to play the demo" if live.mode == "DEMO" else " to record")
        s = live.score
        score = (f"MSE {s['mse']:.4f} · bias {s['bias']:+.3f} · n {s['n']}" if s and s["n"]
                 else "MSE — (nothing paired in 2–10 m/s yet)")
        y = self._text(surface, self.med, state, xl, y + 2, VALUE)
        y = self._text(surface, self.small, score + "   (2–10 m/s)", xl, y) + m

        # the bottom block, laid out upwards
        rx = live.rx
        bottom = [(self.small, f"listening on {rx.where}", LABEL)]
        if rx.port:
            port = "" if rx.port == 5005 else f":{rx.port}"
            bottom[:0] = [(self.small, "on the Pi:", LABEL),
                          (self.med, _fit(self.med, f"aircam run --udp {rx.ip}{port}", inner), VALUE)]
        if live.last_name:
            bottom += [(self.small, f"last run {live.last_name}" + (" (aborted)" if live.last_aborted else ""), LABEL),
                       (self.small, live.result, LABEL),
                       (self.med, _fit(self.med, live.fit_command(), inner), VALUE)]
        bottom += [(self.small, line, LABEL) for line in _wrap(self.small, KEYS[live.mode], inner)]
        yb = h - m - sum(f.get_height() for f, _, _ in bottom)
        ytext = yb
        for f, text, colour in bottom:
            ytext = self._text(surface, f, text, xl, ytext, colour)

        # the charts in between
        vmax = max(12.0, live.full * 1.25)
        gap = yb - m - y
        title = self.small.get_height()
        if live.mode == "DEMO" and gap > 6 * title:
            dh = round(gap * 0.5)
            self._text(surface, self.small, f"the demo, protocol {live.order[0]} then {live.order[1]}: plan, "
                                            "truth and Pi (cursor: now)", xl, y)
            self._diagram(surface, (xl, y + title, inner, dh - title - m // 2), live, vmax)
            y += dh
            gap -= dh
        if gap > 4 * title:
            self._text(surface, self.small, f"last {CHART_S:.0f} s: truth (white) and Pi (amber)", xl, y)
            t1 = time.time()
            series = [(*np.array(live.chart_truth or [(0.0, 0.0)], float).T, TRUTH_C)]
            if live.chart_pi:
                series.append((*np.array(live.chart_pi, float).T, PI_C))
            self._chart(surface, (xl, y + title, inner, gap - title), t1 - CHART_S, t1, vmax, series)


# -- the app --------------------------------------------------------------------------------------------------
def _open_window(pg, a):
    """Full screen (or a window) at the desktop's size in points, with vsync when the renderer offers it."""
    dw, dh = pg.display.get_desktop_sizes()[0]
    size, flags = ((min(1280, dw), min(800, dh)), 0) if a.windowed else ((dw, dh), pg.FULLSCREEN)
    try:
        return pg.display.set_mode(size, flags | pg.SCALED, vsync=1)
    except pg.error:
        return pg.display.set_mode(size, flags)


def _args(argv):
    ap = argparse.ArgumentParser(prog="aircam screen", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rig", type=int, default=1, help="seed of the hidden rig parameters (keep it, keep the calibration)")
    ap.add_argument("--seed", type=int, help="turbulence seed of the first run, +1 per run (default: the time)")
    ap.add_argument("--mode", default="demo", choices=["demo", "manual"], help="mode to start in")
    ap.add_argument("--order", default="12", choices=["12", "21"], help="demo protocol order")
    ap.add_argument("--full", type=float, default=9.0, help="demo full speed, m/s")
    ap.add_argument("--turb", type=float, help="turbulence intensity at the ball, overriding the rig's (e.g. 0.04)")
    ap.add_argument("--udp", type=int, default=5005, help="UDP port to listen on (0.0.0.0)")
    ap.add_argument("--serial", help="read the Pi's lines from this serial device instead of UDP")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--windowed", action="store_true", help="a 1280x800 window, not full screen")
    ap.add_argument("--out", type=Path, default=OUT, help="where runs are saved (default cw2/data/sim)")
    ap.add_argument("--scale", type=float, default=1.0, help="pendulum size factor")
    ap.add_argument("--display-lag", type=float, default=0.0, help="s from drawing a frame to it being lit")
    ap.add_argument("--seconds", type=float, help=argparse.SUPPRESS)       # quit after this long (smoke tests)
    ap.add_argument("--start", choices=["run", "pluck"], help=argparse.SUPPRESS)
    return ap.parse_args(argv)


def main(argv=None):
    a = _args(argv)
    try:
        pg = _pygame()
    except ImportError as e:
        sys.exit(str(e))
    rig = Rig.from_seed(a.rig)
    if a.turb is not None:
        rig = replace(rig, turb=a.turb)
    rx = Receiver()
    try:
        rx.serial(a.serial, a.baud) if a.serial else rx.udp(a.udp)
    except (OSError, ImportError) as e:
        sys.exit(f"cannot listen for the Pi: {e}")
    pg.display.init()
    pg.font.init()
    screen = _open_window(pg, a)
    pg.display.set_caption("aircam screen")
    w, h = screen.get_size()
    sw = split(w)
    scene = Scene(sw, h, rig, scale=a.scale, seed=a.rig)
    scene.convert()
    view = screen.subsurface((0, 0, sw, h))
    panel = Panel(pg, sw, w - sw, h)
    live = Live(a, rig, rx, scene)
    print(f"aircam screen: rig {a.rig}, {w}x{h} px (pendulum {sw} px wide), ball {scene.ball_px:.0f} px, "
          f"arm {scene.arm_px:.0f} px\n  the Pi sends to {rx.where}\n  " + KEYS[live.mode], flush=True)
    if a.start:
        live.press("start" if a.start == "run" else "pluck")
    clock, running, panel_at, cursor = pg.time.Clock(), True, -1.0, None
    try:
        while running:
            acted = False
            for ev in pg.event.get():
                name = None
                if ev.type == pg.QUIT:
                    running = False
                elif ev.type == pg.KEYDOWN:
                    name = key_action(pg.key.name(ev.key), live.mode)
                elif ev.type == pg.MOUSEBUTTONDOWN and ev.button == 1:
                    name = panel.hit(ev.pos)
                if name:
                    running = live.press(name) and running
                    acted = True
            now = live.clock()
            live.advance(now)
            live.pull()
            if live.finished:
                live.finish(complete=True)
                acted = True
            live.update_score()
            scene.draw(view, live.pend.theta)
            if acted or time.perf_counter() - panel_at > 1 / 30:          # the panel at 30 Hz is plenty
                panel.draw(screen, live)
                panel_at = time.perf_counter()
            over_panel = pg.mouse.get_pos()[0] >= sw                       # no cursor over the pendulum
            if over_panel != cursor:
                pg.mouse.set_visible(over_panel)
                cursor = over_panel
            pg.display.flip()
            live.frames += 1
            clock.tick(240)                       # vsync paces it when it works; this caps it when it does not
            if a.seconds is not None and now >= a.seconds:
                running = False
    finally:
        if live.running:
            live.finish(complete=live.run_mode == "MANUAL")
        rx.close()
        pg.quit()
