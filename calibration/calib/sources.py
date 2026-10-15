"""Data sources: Nucleo serial, ground-truth adapters, CSV replay and a simulated wind tunnel.

Every source runs its own daemon thread and delivers samples through callbacks:

    on_sensor(t_pc, row: dict)   # row has n, v, feature, rho, temp_c, pressure_pa, ... (whatever exists)
    on_gt(t_pc, v)
    on_info(line: str)           # '#' lines from the board, adapter status messages

`stop()` is idempotent and joins the thread.
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import math
import re
import shlex
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
from scipy.signal import cont2discrete, lfilter

from . import models
from .data import Run, load_csv

DEFAULT_COLS = ["n", "v", "feature", "rho", "temp_c", "pressure_pa"]
_FLOAT_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


# --------------------------------------------------------------------------------------------- parsing
@dataclass
class LineParser:
    """Stateful parser for the Nucleo stream (SPEC §1). Header-driven; never raises."""

    cols: list = field(default_factory=lambda: list(DEFAULT_COLS))
    bad_lines: int = 0

    def parse(self, line) -> tuple | None:
        """Returns one of
        ("v", value)            velocity-mode float
        ("d", {col: value})     debug row
        ("cols", [names])       header
        ("cal", dict)           GET reply
        ("ok", name, value)     SET acknowledgement
        ("info", text)          any other '#' line (#err, #env, #stats, ...)
        or None for blank/garbage lines.
        """
        if isinstance(line, (bytes, bytearray)):
            line = line.decode("ascii", errors="replace")
        s = line.strip().strip("\x00")
        if not s:
            return None
        if s.startswith("#"):
            return self._info(s)
        parts = s.split(",")
        try:
            vals = [float(p) for p in parts]
        except ValueError:
            self.bad_lines += 1
            return None
        if len(vals) == 1:
            return ("v", vals[0]) if math.isfinite(vals[0]) else None
        if len(vals) != len(self.cols):
            # A truncated/merged line (common after a USB hiccup) would silently shift columns: drop it.
            self.bad_lines += 1
            return None
        return ("d", dict(zip(self.cols, vals)))

    def _info(self, s: str):
        body = s[1:]
        if body.startswith("cols,"):
            names = [c.strip() for c in body[5:].split(",") if c.strip()]
            if names:
                self.cols = names
            return ("cols", list(self.cols))
        if body.startswith("cal"):
            try:
                return ("cal", json.loads(body[3:].strip()))
            except json.JSONDecodeError:
                return ("info", s)
        if body.startswith("ok"):
            m = re.match(r"ok\s+([A-Za-z0-9_]+)\s*=\s*(\S+)", body)
            if m:
                try:
                    return ("ok", m.group(1), float(m.group(2)))
                except ValueError:
                    pass
        return ("info", s)


# ---------------------------------------------------------------------------------------------- base
class Source:
    name = "source"

    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.connected = False
        self.status = "idle"

    def start(self):
        self._thread = threading.Thread(target=self._run_safe, name=self.name, daemon=True)
        self._thread.start()
        return self

    def _run_safe(self):
        try:
            self._run()
        except Exception as e:  # never let a source thread die silently
            self.status = f"crashed: {e!r}"
            self.connected = False

    def _run(self):
        raise NotImplementedError

    def stop(self, timeout: float = 2.0):
        self._stop.set()
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout)


def _noop(*_a, **_k):
    pass


# -------------------------------------------------------------------------------------------- serial
class SerialSensorSource(Source):
    """Reads the Nucleo over USB serial with an automatic reconnect loop."""

    name = "sensor-serial"

    def __init__(self, port: str, baud: int = 115200, on_sensor=_noop, on_info=_noop,
                 on_cal=_noop, debug_mode: bool = True):
        super().__init__()
        self.port, self.baud = port, baud
        self.on_sensor, self.on_info, self.on_cal = on_sensor, on_info, on_cal
        self.debug_mode = debug_mode
        self.parser = LineParser()
        self._ser = None
        self._tx_lock = threading.Lock()
        self.lines = 0

    def _run(self):
        import serial  # pyserial; imported lazily so sim mode works without it

        backoff = 0.5
        while not self._stop.is_set():
            try:
                self.status = f"opening {self.port}"
                # write_timeout: a board that stops reading must not hang this thread on a full buffer
                with serial.Serial(self.port, self.baud, timeout=0.2, write_timeout=0.5) as ser:
                    self._ser = ser
                    self.connected, self.status, backoff = True, f"connected {self.port}", 0.5
                    ser.reset_input_buffer()
                    if self.debug_mode:
                        self.send_command("MODE D")
                    self.send_command("HDR")  # resync column names after any reconnect
                    self.send_command("GET")
                    buf = b""
                    while not self._stop.is_set():
                        chunk = ser.read(ser.in_waiting or 1)
                        if not chunk:
                            continue
                        t = time.perf_counter()
                        buf += chunk
                        *lines, buf = buf.split(b"\n")
                        if len(buf) > 4096:  # no newline for a long time: garbage, resync
                            buf = b""
                        for ln in lines:
                            self._handle(t, ln)
            except Exception as e:
                self.status = f"disconnected: {e}"
            finally:
                self._ser, self.connected = None, False
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 5.0)

    def _handle(self, t, ln):
        self.lines += 1
        r = self.parser.parse(ln)
        if r is None:
            return
        kind = r[0]
        if kind == "v":
            self.on_sensor(t, {"v": r[1]})
        elif kind == "d":
            self.on_sensor(t, r[1])
        elif kind == "cal":
            self.on_cal(r[1])
        elif kind == "ok":
            self.on_info(f"#ok {r[1]}={r[2]:g}")
        else:
            self.on_info(ln.decode("ascii", "replace").strip() if isinstance(ln, bytes) else str(ln))

    def send_command(self, line: str) -> bool:
        ser = self._ser
        if ser is None:
            return False
        try:
            with self._tx_lock:
                ser.write((line.strip() + "\n").encode("ascii"))
            return True
        except Exception as e:
            self.status = f"write failed: {e}"
            return False


# -------------------------------------------------------------------------------------- ground truth
class GroundTruthSource(Source):
    """Wraps whatever script reads the Dwyer pitot. Exactly one of module/cmd/port.

    module: "path/to/gt.py:func". `func` may be a plain function returning v or (t, v) or None,
            or a generator function yielding those. Plain functions are paced at `rate_hz` if they
            return faster than that (so a non-blocking 'latest value' getter does not spin).
    cmd:    a shell command; the last float on each stdout line is a sample. Restarted on exit.
    port:   a serial port streaming one float per line.
    """

    name = "gt"

    def __init__(self, module: str | None = None, cmd: str | None = None, port: str | None = None,
                 baud: int = 115200, rate_hz: float = 200.0, on_gt=_noop, on_info=_noop):
        super().__init__()
        if sum(x is not None for x in (module, cmd, port)) != 1:
            raise ValueError("GroundTruthSource needs exactly one of module / cmd / port")
        self.module, self.cmd, self.port, self.baud, self.rate_hz = module, cmd, port, baud, rate_hz
        self.on_gt, self.on_info = on_gt, on_info
        self._proc: subprocess.Popen | None = None
        self.label = module or cmd or port

    def _emit(self, value, t=None):
        try:
            v = float(value)
        except (TypeError, ValueError):
            return
        if math.isfinite(v):
            self.on_gt(time.perf_counter() if t is None else t, v)

    def _run(self):
        if self.module:
            self._run_module()
        elif self.cmd:
            self._run_cmd()
        else:
            self._run_serial()

    # -- module adapter
    def _load_callable(self) -> Callable:
        path, _, func = self.module.rpartition(":")
        if not path:
            path, func = self.module, "read_sample"
        spec = importlib.util.spec_from_file_location("gt_adapter", Path(path).expanduser())
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot import {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, func)

    def _run_module(self):
        while not self._stop.is_set():
            try:
                fn = self._load_callable()
                self.connected, self.status = True, f"module {self.module}"
                if inspect.isgeneratorfunction(fn):
                    for item in fn():
                        if self._stop.is_set():
                            return
                        self._emit_item(item)
                    self.connected, self.status = False, "gt generator finished; restarting"
                    self.on_info(f"#gt {self.status}")
                    self._stop.wait(2.0)
                else:
                    period = 1.0 / self.rate_hz if self.rate_hz > 0 else 0.0
                    nxt = time.perf_counter()
                    while not self._stop.is_set():
                        self._emit_item(fn())
                        nxt += period
                        lag = nxt - time.perf_counter()
                        if lag > 0:
                            time.sleep(lag)
                        else:
                            nxt = time.perf_counter()
            except Exception as e:
                self.connected, self.status = False, f"gt module error: {e!r}"
                self.on_info(f"#gt {self.status}")
                self._stop.wait(2.0)

    def _emit_item(self, item):
        if item is None:
            return
        if isinstance(item, (tuple, list)) and len(item) == 2:
            self._emit(item[1])  # their t is in their clock; we timestamp on arrival
        else:
            self._emit(item)

    # -- subprocess adapter
    def _run_cmd(self):
        while not self._stop.is_set():
            try:
                self._proc = subprocess.Popen(shlex.split(self.cmd), stdout=subprocess.PIPE,
                                              stderr=subprocess.STDOUT, bufsize=1, text=True)
                self.connected, self.status = True, f"cmd pid {self._proc.pid}"
                for line in self._proc.stdout:
                    if self._stop.is_set():
                        break
                    nums = _FLOAT_RE.findall(line)
                    if nums:
                        self._emit(nums[-1])
                    elif line.strip():
                        self.on_info(f"#gt {line.strip()}")
            except Exception as e:
                self.status = f"gt cmd error: {e!r}"
            finally:
                self._kill()
                self.connected = False
            if not self._stop.is_set():
                self.on_info(f"#gt command exited; restarting ({self.status})")
                self._stop.wait(2.0)

    def _kill(self):
        p, self._proc = self._proc, None
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(1.0)
            except subprocess.TimeoutExpired:
                p.kill()

    # -- serial adapter
    def _run_serial(self):
        import serial

        while not self._stop.is_set():
            try:
                # write_timeout: a board that stops reading must not hang this thread on a full buffer
                with serial.Serial(self.port, self.baud, timeout=0.2, write_timeout=0.5) as ser:
                    self.connected, self.status = True, f"connected {self.port}"
                    buf = b""
                    while not self._stop.is_set():
                        chunk = ser.read(ser.in_waiting or 1)
                        if not chunk:
                            continue
                        t = time.perf_counter()
                        buf += chunk
                        *lines, buf = buf.split(b"\n")
                        buf = buf[-4096:]
                        for ln in lines:
                            nums = _FLOAT_RE.findall(ln.decode("ascii", "replace"))
                            if nums:
                                self._emit(nums[-1], t)
            except Exception as e:
                self.status = f"gt serial: {e}"
            self.connected = False
            self._stop.wait(1.0)

    def stop(self, timeout: float = 2.0):
        self._stop.set()
        self._kill()
        super().stop(timeout)


# -------------------------------------------------------------------------------------------- replay
class CSVReplaySource(Source):
    """Plays a recorded run back in (scaled) real time, looping, through the same callbacks."""

    name = "replay"

    def __init__(self, path: str, speed: float = 1.0, loop: bool = True, on_sensor=_noop, on_gt=_noop):
        super().__init__()
        self.run = load_csv(path)
        self.path, self.speed, self.loop = path, speed, loop
        self.on_sensor, self.on_gt = on_sensor, on_gt

    def _run(self):
        r = self.run
        ev = [(t, 0, i) for i, t in enumerate(r.t_s)] + [(t, 1, i) for i, t in enumerate(r.t_g)]
        ev.sort()
        if not ev:
            self.status = "empty file"
            return
        self.connected, self.status = True, f"replay {Path(self.path).name}"
        chans = r.channels
        while not self._stop.is_set():
            t0_rec, t0 = ev[0][0], time.perf_counter()
            for t_rec, kind, i in ev:
                due = t0 + (t_rec - t0_rec) / self.speed
                d = due - time.perf_counter()
                if d > 0.002:
                    if self._stop.wait(d):
                        return
                now = time.perf_counter()
                if kind == 0:
                    self.on_sensor(now, {c: float(r.sensor[c][i]) for c in chans})
                else:
                    self.on_gt(now, float(r.gt[i]))
            if not self.loop:
                break
        self.connected = False


# ----------------------------------------------------------------------------------------- simulator
PROFILES = ("steps", "ramps", "sweep", "gusts")


def setpoint(profile: str, t: np.ndarray) -> np.ndarray:
    t = np.asarray(t, dtype=float)
    if profile == "steps":
        levels = np.array([0, 2, 4, 6, 8, 10, 7, 3, 9, 5, 2.5, 6.5])
        return levels[(t // 6.0).astype(int) % len(levels)]
    if profile == "ramps":
        ph = t % 36.0  # hold 2 | ramp up 10 s | hold 10 | ramp down 10 s | hold 2 | hold 4 at 6
        return np.select(
            [ph < 4, ph < 14, ph < 18, ph < 28, ph < 32],
            [2.0, 2 + 0.8 * (ph - 4), 10.0, 10 - 0.8 * (ph - 18), 2.0],
            6.0,
        )
    if profile == "sweep":
        # static calibration staircase, up then down (shows hysteresis), 8 s per level
        levels = np.array([0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 8, 6, 4, 2])
        return levels[(t // 8.0).astype(int) % len(levels)].astype(float)
    if profile == "gusts":
        v = 6 + 2.5 * np.sin(2 * np.pi * 0.13 * t) + 1.2 * np.sin(2 * np.pi * 0.71 * t + 1) \
            + 0.6 * np.sign(np.sin(2 * np.pi * 0.05 * t))
        return v.clip(2, 10)
    raise ValueError(f"unknown profile {profile!r}; choose from {PROFILES}")


@dataclass
class SimTruth:
    """Hidden 'true' physics of the simulated rig. Fits should recover these."""

    # rotor: steady-state rev/s from wind speed; slightly nonlinear so a line is not perfect
    rotor_slope: float = 2.35        # m/s per rev/s
    rotor_offset: float = 0.45       # m/s starting speed
    rotor_curv: float = 0.004        # fractional curvature
    rotor_dist_const_m: float = 1.6  # rotor lag tau = L / v
    rotor_ppr: int = 96              # EM14, 24 cycles/rev decoded in quadrature (x4)
    rotor_spindown_s: float = 0.8    # aerodynamic decay once the wind can no longer drive the cups
    rotor_friction: float = 1.5      # rev/s^2 bearing friction: brings the rotor to a full stop
    # drag plate
    cda: float = 0.0118              # -> sqrt_k = 2/cda = 169.5
    preload_n: float = 0.0           # mechanical preload
    plate_fn_hz: float = 6.0
    plate_zeta: float = 0.3
    lc_offset: float = 84213.0       # counts at zero load
    lc_n_per_count: float = 2.07e-5  # true scale (board starts at 2.0e-5: 3.5 % error)
    lc_noise_counts: float = 60.0
    # tunnel / references
    fan_tau: float = 0.7
    turb_intensity: float = 0.012
    turb_corr_s: float = 0.08
    gt_tau: float = 0.015
    gt_noise: float = 0.03
    drop_prob: float = 0.0003

    @property
    def sqrt_k(self) -> float:
        return 2.0 / self.cda


class SimulatedTunnel:
    """Stateful wind-tunnel + Nucleo simulator.

    Produces the GT stream and the board's debug columns. Also acts as a fake board: `command()`
    understands SET / GET / MODE / TARE, so the dashboard push path can be exercised end to end.
    The board method (which feature goes in the `feature` column) follows `model`.
    """

    def __init__(self, model: str = "rotor", profile: str = "steps", seed: int = 0, fs: float = 200.0,
                 truth: SimTruth | None = None):
        self.fs, self.dt = fs, 1.0 / fs
        self.truth = truth or SimTruth()
        self.rng = np.random.default_rng(seed)
        self.profile, self.model = profile, model
        self.t, self.n = 0.0, 0
        tr = self.truth
        # board parameters: defaults, but tared with a small error (gives a non-zero sqrt_x0 to find)
        self.board = {**models.default_params("rotor"), **models.default_params("sqrt")}
        self.board["lc_offset"] = tr.lc_offset + 900.0
        self.board_state = models.DynState()
        # filter states
        a = math.exp(-self.dt / tr.fan_tau)
        self._fan = (np.array([1 - a]), np.array([1, -a]), np.zeros(1))
        a = math.exp(-self.dt / tr.turb_corr_s)
        self._turb = (np.array([math.sqrt(1 - a * a)]), np.array([1, -a]), np.zeros(1))
        a = math.exp(-self.dt / tr.gt_tau)
        self._gtf = (np.array([1 - a]), np.array([1, -a]), np.zeros(1))
        wn = 2 * math.pi * tr.plate_fn_hz
        num, den, _ = cont2discrete(([wn * wn], [1, 2 * tr.plate_zeta * wn, wn * wn]), self.dt, method="bilinear")
        self._plate = (np.ravel(num), np.ravel(den), np.zeros(2))
        self._rev = 0.0
        self._f_rotor = 0.0
        self._hist: deque = deque()  # (t, counts) for the rotor rate estimator
        self._lc_recent: deque = deque(maxlen=100)
        self.temp0, self.p0 = 22.3, 100870.0
        # operator controls used by the dashboard's guided steps
        self.fan_on = True      # False: fan stopped, airspeed decays to 0 through the fan lag
        self.load_n = 0.0       # static weight hung on the drag plate (weights routine), N

    # ---- fake firmware command interface
    def command(self, line: str) -> list[str]:
        parts = line.strip().split()
        if not parts:
            return []
        cmd = parts[0].upper()
        if cmd == "SET" and len(parts) == 3:
            name = parts[1]
            if name not in models.FIRMWARE_PARAMS:
                return [f"#err unknown param {name}"]
            try:
                self.board[name] = float(parts[2])
            except ValueError:
                return [f"#err bad value {parts[2]}"]
            return [f"#ok {name}={self.board[name]:g}"]
        if cmd == "GET":
            cal = {k: self.board[k] for k in models.FIRMWARE_PARAMS}
            cal.update(method=self.method, build="sim", rho=self._rho_now(), temp_c=self.temp0)
            return ["#cal " + json.dumps(cal)]
        if cmd == "TARE":
            if self._lc_recent:
                self.board["lc_offset"] = float(np.mean(self._lc_recent))
            return [f"#ok lc_offset={self.board['lc_offset']:g}"]
        if cmd in ("MODE", "HDR"):
            return ["#cols," + ",".join(DEFAULT_COLS)]
        return [f"#err unknown command {cmd}"]

    @property
    def method(self) -> str:
        """Board method string as the firmware prints it in `#cal`."""
        return "cup_encoder" if self.model == "rotor" else "drag_plate"

    def _rho_now(self) -> float:
        return float(models.air_density(self.p0, self.temp0))

    # ---- physics
    def step(self, n: int) -> dict:
        """Advance n samples. Returns arrays: t, v_true, gt, and debug columns + feature_rotor/_sqrt."""
        tr, rng, dt = self.truth, self.rng, self.dt
        t = self.t + dt * np.arange(1, n + 1)
        sp = setpoint(self.profile, t) if self.fan_on else np.zeros(n)
        b, a, zi = self._fan
        fan, self._fan = self._lf(b, a, sp, zi)
        b, a, zi = self._turb
        turb, self._turb = self._lf(b, a, rng.standard_normal(n), zi)
        v_true = np.maximum(fan * (1 + tr.turb_intensity * turb), 0.0)
        b, a, zi = self._gtf
        gt_l, self._gtf = self._lf(b, a, v_true, zi)
        gt = gt_l + tr.gt_noise * rng.standard_normal(n)

        temp = self.temp0 + 0.25 * np.sin(2 * np.pi * t / 600.0) + 0.02 * rng.standard_normal(n)
        press = self.p0 + 3.0 * rng.standard_normal(n)
        rho_true = models.air_density(press, temp)
        rho_meas = models.air_density(press + 2.0 * rng.standard_normal(n), temp + 0.05)

        # drag plate: q*CdA through the plate's mechanical 2nd-order response, then the load cell
        force = 0.5 * rho_true * tr.cda * v_true ** 2 + tr.preload_n + self.load_n
        b, a, zi = self._plate
        f_mech, self._plate = self._lf(b, a, force, zi)
        counts = np.round(tr.lc_offset + f_mech / tr.lc_n_per_count + tr.lc_noise_counts * rng.standard_normal(n))
        self._lc_recent.extend(counts.tolist())
        feat_sqrt = (counts - self.board["lc_offset"]) * self.board["lc_n_per_count"]

        # rotor: speed-dependent first-order lag, encoder quantisation, sliding-window rate estimate
        feat_rot = np.empty(n)
        win = max(self.board["rotor_window_ms"], 5.0) / 1000.0
        for i in range(n):
            v = v_true[i]
            f_ss = 0.0 if v < tr.rotor_offset * 1.6 else (v - tr.rotor_offset) / tr.rotor_slope * (1 - tr.rotor_curv * (v - 6))
            if f_ss > 0.0:
                tau = tr.rotor_dist_const_m / max(v, 0.5)
                self._f_rotor += (f_ss - self._f_rotor) * min(dt / tau, 1.0)
            else:  # below stall: drag decay plus bearing friction, so the cups actually stop
                self._f_rotor = max(0.0, self._f_rotor * (1.0 - min(dt / tr.rotor_spindown_s, 1.0))
                                    - tr.rotor_friction * dt)
            self._rev += self._f_rotor * dt
            c = math.floor(self._rev * tr.rotor_ppr)
            self._hist.append((t[i], c))
            while len(self._hist) > 2 and t[i] - self._hist[1][0] >= win:
                self._hist.popleft()
            t0, c0 = self._hist[0]
            feat_rot[i] = (c - c0) / tr.rotor_ppr / (t[i] - t0) if t[i] > t0 else 0.0

        feature = feat_rot if self.model == "rotor" else feat_sqrt
        raw = {"feature": feature, "rho": rho_meas}
        v_board = models.apply(self.model, self.board, raw, self.fs, self.board_state)
        n_col = self.n + np.arange(1, n + 1)
        keep = rng.random(n) >= tr.drop_prob  # simulated dropped ticks: n jumps by 2
        self.t, self.n = float(t[-1]), int(n_col[-1])
        out = {
            "t": t, "v_true": v_true, "gt": gt, "n": n_col.astype(float), "v": v_board, "feature": feature,
            "rho": rho_meas, "temp_c": temp, "pressure_pa": press,
            "feature_rotor": feat_rot, "feature_sqrt": feat_sqrt,
        }
        return {k: v[keep] for k, v in out.items()}

    @staticmethod
    def _lf(b, a, x, zi):
        y, zf = lfilter(b, a, x, zi=zi)
        return y, (b, a, zf)

    def generate(self, seconds: float) -> Run:
        """Offline run: perfectly aligned streams (t_g == t_s), handy for tests and demos."""
        chunks = [self.step(200) for _ in range(int(math.ceil(seconds)))]
        cat = {k: np.concatenate([c[k] for c in chunks]) for k in chunks[0]}
        sensor_cols = ["n", "v", "feature", "rho", "temp_c", "pressure_pa", "feature_rotor", "feature_sqrt"]
        return Run(cat["t"], {c: cat[c] for c in sensor_cols}, cat["t"].copy(), cat["gt"],
                   {"source": "sim", "model": self.model, "profile": self.profile, "v_true": cat["v_true"]})


class SimSource(Source):
    """Drives SimulatedTunnel in real time (50 ms chunks) and adds a small, jittery GT latency."""

    name = "sim"

    def __init__(self, sim: SimulatedTunnel, on_sensor=_noop, on_gt=_noop, on_info=_noop, gt_latency_s=0.004):
        super().__init__()
        self.sim, self.on_sensor, self.on_gt, self.on_info = sim, on_sensor, on_gt, on_info
        self.gt_latency_s = gt_latency_s
        self.lock = threading.Lock()

    def command(self, line: str) -> bool:
        with self.lock:
            replies = self.sim.command(line)
        for r in replies:
            self.on_info(r)
        return True

    send_command = command

    def _run(self):
        self.connected, self.status = True, "simulator"
        chunk = 10
        t_wall0 = time.perf_counter()
        t_sim0 = self.sim.t
        while not self._stop.is_set():
            with self.lock:
                d = self.sim.step(chunk)
            for i in range(len(d["t"])):
                ts = t_wall0 + (d["t"][i] - t_sim0)
                self.on_sensor(ts, {k: float(d[k][i]) for k in
                                    ("n", "v", "feature", "rho", "temp_c", "pressure_pa", "feature_rotor", "feature_sqrt")})
                self.on_gt(ts + self.gt_latency_s, float(d["gt"][i]))
            due = t_wall0 + (self.sim.t - t_sim0)
            wait = due - time.perf_counter()
            if wait > 0:
                self._stop.wait(wait)
            elif wait < -1.0:  # fell far behind (laptop slept): resync instead of bursting
                t_wall0, t_sim0 = time.perf_counter(), self.sim.t
        self.connected = False


def list_ports() -> list[dict]:
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return []
    return [{"device": p.device, "description": p.description, "hwid": p.hwid} for p in lp.comports()]
