"""Live calibration server: sources -> ring buffers -> PC model preview -> websocket (SPEC §6).

Threads: each source pushes samples into the Engine under a lock. One asyncio loop broadcasts
frames (20 Hz) and metrics (2 Hz) and handles client messages; fits run in a worker thread.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from calib import export, fit, metrics, models
from calib import recorder as recmod
from calib import tunnel
from calib.metrics import resample
from calib.data import Run
from calib import relay
from calib.recorder import Session
from calib.relay import BoardRelay
from calib.sources import (AUTO_PORT, PROFILES, CSVReplaySource, GroundTruthSource, SerialSensorSource, SimSource,
                           SimulatedTunnel, list_ports)

STATIC = Path(__file__).resolve().parent / "static"
SERVER_ID = uuid.uuid4().hex[:12]  # a new id after every restart: a page that sees it change reloads (live reload)
LIVE_RELOAD = {"on": False}        # set by serve(live_reload=True), i.e. `pnpm dev`
DATASHEETS = Path(__file__).resolve().parents[2] / "docs" / "datasheets"  # served at /datasheets for the UI
FRAME_HZ, METRICS_HZ = 20.0, 2.0
RAW_SEND = ("feature", "rho", "temp_c", "pressure_pa", "lab_raw", "lab_weight")
REPLACE_WINDOW_S, REPLACE_DECIM = 60.0, 1  # full rate so the dashboard can re-run the exact 200 Hz chain
ENV_POLL_S = 1.0  # `ENV` request period: per-thermometer, humidity and pressure readings for the dashboard
TELEMETRY_IDLE_S = 0.5  # with no frames (no board, silent board) telemetry is still sent this often
SILENT_AFTER_S = 2.0  # an open port with no line for this long is a silent board
STALE_DATA_S = 2.0  # streaming = a reading within this long (the LAB2 firmware sends only 2 per second)


class FileWatcher:
    """Live reload (dev): polls the mtimes of the dashboard's files; `changes()` lists what changed since the
    last call. Polling, not OS events: no extra dependency, and ~100 files cost nothing at 2 Hz."""

    EXTS = (".js", ".css", ".html", ".svg", ".pdf", ".woff2")

    def __init__(self, *roots: Path):
        self.roots = [r for r in roots if r.is_dir()]
        self._seen = self._scan()

    def _scan(self) -> dict:
        out = {}
        for root in self.roots:
            for p in root.rglob("*"):
                if p.suffix in self.EXTS:
                    try:
                        out[p] = p.stat().st_mtime_ns
                    except OSError:
                        pass
        return out

    def changes(self) -> list[str]:
        now = self._scan()
        changed = [str(p) for p in now.keys() | self._seen.keys() if now.get(p) != self._seen.get(p)]
        self._seen = now
        return sorted(changed)


def parse_env(line: str) -> dict:
    """`#env t_air=22.31 mcp9808=22.30 ... rho=1.1876` -> {name: float | None}."""
    out = {}
    for tok in line[len("#env"):].split():
        k, _, v = tok.partition("=")
        if not k or not _:
            continue
        try:
            f = float(v)
        except ValueError:
            continue
        out[k] = f if math.isfinite(f) else None
    return out


class Ring:
    """Append-only column store trimmed to `cap` rows; `seq` is a monotonic row id."""

    def __init__(self, cap: int):
        self.cap, self.t, self.cols, self.seq0 = cap, [], {}, 0

    def append(self, t: float, row: dict):
        n = len(self.t)
        for k in row:
            if k not in self.cols:
                self.cols[k] = [math.nan] * n
        for k, col in self.cols.items():
            v = row.get(k)
            col.append(math.nan if v is None else v)
        self.t.append(t)
        if len(self.t) > self.cap * 1.25:
            cut = len(self.t) - self.cap
            self.t = self.t[cut:]
            for k in self.cols:
                self.cols[k] = self.cols[k][cut:]
            self.seq0 += cut

    @property
    def seq_end(self) -> int:
        return self.seq0 + len(self.t)

    def arrays(self, since_seq: int | None = None, cols=None):
        i = 0 if since_seq is None else max(since_seq - self.seq0, 0)
        names = self.cols.keys() if cols is None else [c for c in cols if c in self.cols]
        return np.asarray(self.t[i:], dtype=float), {k: np.asarray(self.cols[k][i:], dtype=float) for k in names}


def _clean(a, nd=4):
    if a is None:
        return None
    a = np.round(np.asarray(a, dtype=float), nd)
    return [None if not math.isfinite(x) else x for x in a.tolist()]


def _jsonable(o):
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (float, np.floating)):
        return float(o) if math.isfinite(o) else None
    if isinstance(o, np.integer):
        return int(o)
    return o


class Engine:
    def __init__(self, mode: str, window_s: float = 30.0, buffer_s: float = 120.0,
                 sensor_port: str | None = None, baud: int = 115200, gt_cmd=None, gt_module=None, gt_port=None,
                 gt_baud: int = 115200, gt_pressure: bool = False, replay: str | None = None,
                 replay_speed: float = 1.0, profile: str = "steps", seed: int | None = None):
        self.mode, self.window_s = mode, window_s
        self.lock = threading.Lock()
        self.sensor = Ring(int(buffer_s * 200))
        self.gt = Ring(int(buffer_s * 250))
        self.params = models.default_params()
        self.board_cal: dict = {}
        self.env: dict = {}  # last `#env` reply, plus its arrival time `t`
        self.info: deque = deque(maxlen=500)
        self.recorder: Session | None = None
        self._rec_seq = self._rec_gt_seq = 0
        self._rec_dyn = models.DynState()
        self._rec_log: dict | None = None  # raw tunnel and board samples while recording, for the logger's files
        self.last_tunnel_run: dict | None = None  # {name, dir} the last stopped recording was also saved as
        self.relay: BoardRelay | None = None  # the board's stream for the Wind Tunnel Logger (calib/relay.py)
        self._relay_state = "off"  # "wanted" in live mode until the board's first bytes create it, then "on"/"failed"
        self.auto_push = False
        self.last_fit: dict | None = None
        self.sim: SimulatedTunnel | None = None
        self.sources = []
        self.sensor_src = None
        self.gt_src = None
        self._est_seq = 0  # rows of `sensor` whose PC preview has been computed
        self._dyn = models.DynState()
        self._sent_seq = 0
        self._replace_pending = True
        self._labels = {"sensor": "-", "gt": "-"}
        self._gen = 0  # bumps on every source switch; callbacks from stopped sources are ignored
        source = {"sim": "sim", "replay": "replay"}.get(mode, "serial")
        gt_kind, gt_value = "none", None
        for kind, val in (("cmd", gt_cmd), ("module", gt_module), ("port", gt_port)):
            if val:
                gt_kind, gt_value = kind, val
        self._cfg = {"source": source, "sensor_port": sensor_port, "baud": baud, "gt_kind": gt_kind,
                     "gt_value": gt_value, "gt_baud": gt_baud, "gt_pressure": gt_pressure, "replay_file": replay,
                     "speed": replay_speed, "profile": profile, "seed": seed}
        self._install(self._build_sources(self._cfg, validate=False))

    # ------------------------------------------------------------------- source construction / switching
    @staticmethod
    def _check_port(port: str, baud: int):
        try:
            import serial
        except ImportError:
            raise ValueError("pyserial is not installed: pip install pyserial") from None
        try:
            serial.Serial(port, baud, timeout=0.1).close()
        except Exception as e:
            raise ValueError(f"cannot open {port}: {e}") from None

    @staticmethod
    def _resolve_replay(f: str | None) -> Path:
        if not f:
            raise ValueError("replay needs replay_file")
        p = Path(f).expanduser()
        if not p.is_file():
            cand = Path(recmod.RUNS_DIR) / Path(f).name
            p = cand if cand.is_file() else (cand.with_suffix(".csv") if cand.with_suffix(".csv").is_file() else p)
        if not p.is_file():
            raise ValueError(f"replay file not found: {f}")
        return p

    def _build_sources(self, cfg: dict, validate: bool = True) -> dict:
        """Create (not start) the sources for `cfg`. With validate, problems that would otherwise only show
        up later in a source thread (port busy, missing file, bad module path) raise ValueError now."""
        gen = self._gen + 1  # becomes current only in _install, so a failed build never mutes the old source
        on_sensor = lambda t, row: self._on_sensor(t, row, gen)  # noqa: E731
        on_gt = lambda t, v: self._on_gt(t, v, gen)  # noqa: E731
        on_info = lambda line: self._on_info(line, gen)  # noqa: E731
        on_cal = lambda cal: self._on_cal(cal, gen)  # noqa: E731
        src = cfg.get("source", "sim")
        out = {"gen": gen, "cfg": dict(cfg), "sim": None, "sensor_src": None, "gt_src": None, "sources": [],
               "labels": {"sensor": "-", "gt": "-"}}
        if src == "sim":
            profile = cfg.get("profile") or "steps"
            if profile not in PROFILES:
                raise ValueError(f"unknown profile {profile!r}; choose from {', '.join(PROFILES)}")
            seed = cfg.get("seed")
            sim = SimulatedTunnel(profile=profile, seed=int(time.time()) if seed is None else int(seed))
            s_ = SimSource(sim, on_sensor, on_gt, on_info)
            out.update(mode="sim", sim=sim, sensor_src=s_, gt_src=s_, sources=[s_],
                       labels={"sensor": "simulator", "gt": "simulator"})
        elif src == "replay":
            path = self._resolve_replay(cfg.get("replay_file"))
            try:
                s_ = CSVReplaySource(str(path), float(cfg.get("speed") or 1.0), True, on_sensor, on_gt)
            except Exception as e:
                raise ValueError(f"cannot read {path.name}: {e}") from None
            out["cfg"]["replay_file"] = str(path)
            out.update(mode="replay", sensor_src=s_, gt_src=s_, sources=[s_],
                       labels={"sensor": path.name, "gt": path.name})
        elif src == "serial":
            port, baud = cfg.get("sensor_port") or None, int(cfg.get("baud") or 115200)
            kind, val = cfg.get("gt_kind") or "none", cfg.get("gt_value") or None
            if kind not in ("none", "cmd", "module", "port"):
                raise ValueError(f"gt_kind must be cmd, module, port or none (got {kind!r})")
            if kind != "none" and not val:
                raise ValueError(f"gt_kind {kind} needs gt_value")
            if not port and kind == "none":
                raise ValueError("serial source needs a sensor port and/or a ground-truth source")
            if validate:
                held = self._held_ports()  # ports the running source already has open: they are fine
                if port and port != AUTO_PORT and port not in held:
                    self._check_port(port, baud)
                if kind == "port" and val not in held:
                    self._check_port(val, int(cfg.get("gt_baud") or 115200))
                elif kind == "module":
                    path, _, func = str(val).rpartition(":")
                    path, func = (path, func) if path else (val, "read_sample")
                    f = Path(path).expanduser()
                    if not f.is_file():
                        raise ValueError(f"ground-truth module not found: {path}")
                    if func not in f.read_text(errors="replace"):
                        raise ValueError(f"{f.name} has no function {func!r}")
                elif kind == "cmd":
                    try:
                        exe = shlex.split(str(val))[0]
                    except (ValueError, IndexError):
                        raise ValueError(f"bad ground-truth command: {val!r}") from None
                    if not (shutil.which(exe) or Path(exe).expanduser().is_file()):
                        raise ValueError(f"ground-truth command not found: {exe}")
            out["mode"] = "live"
            if port:
                s_ = SerialSensorSource(port, baud, on_sensor, on_info, on_cal)
                s_.tap = self._relay_write
                out["sensor_src"] = s_
                out["sources"].append(s_)
                out["labels"]["sensor"] = "auto-detect" if port == AUTO_PORT else port
            if kind != "none":
                g = GroundTruthSource(val if kind == "module" else None, val if kind == "cmd" else None,
                                      val if kind == "port" else None, int(cfg.get("gt_baud") or 115200),
                                      on_gt=on_gt, on_info=on_info)
                out["gt_src"] = g
                out["sources"].append(g)
                out["labels"]["gt"] = g.label
        else:
            raise ValueError(f"source must be sim, serial or replay (got {src!r})")
        return out

    def _held_ports(self) -> set:
        c = self._cfg if hasattr(self, "_cfg") else {}
        if c.get("source") != "serial":
            return set()
        return {p for p in (c.get("sensor_port"), c.get("gt_value") if c.get("gt_kind") == "port" else None) if p}

    def _install(self, built: dict):
        """Swap in freshly built sources: empty buffers, reset preview/filter state, keep params."""
        with self.lock:
            self._gen = built["gen"]
            self.sensor = Ring(self.sensor.cap)
            self.gt = Ring(self.gt.cap)
            self.mode, self.sim = built["mode"], built["sim"]
            self.sensor_src, self.gt_src, self.sources = built["sensor_src"], built["gt_src"], built["sources"]
            self._labels, self._cfg = built["labels"], built["cfg"]
            self.board_cal = {}
            self._dyn, self._rec_dyn = models.DynState(), models.DynState()
            self._sent_seq = self._est_seq = self._rec_seq = self._rec_gt_seq = 0
            self._gt_only_sent = 0
            self._replace_pending = True
        if self.sim:  # the serial source sends GET on every (re)connect; the simulated board needs asking too
            self.sensor_src.send_command("GET")

    def connect(self, cfg: dict) -> tuple[list[str], dict | None]:
        """Switch data sources at runtime (SPEC §6 `connect`). The new configuration is built and checked
        first; ValueError carries the reason and leaves the running source and any recording untouched.
        Only then is a recording finalised, the old sources stopped and the new ones started.
        Returns (log messages, final session status of a recording that was stopped, or None)."""
        # Build and validate first: on any problem nothing has been touched (recording, sources, buffers).
        built = self._build_sources(cfg)
        msgs, closed = [], None
        if self.recorder:
            rec = self.recorder
            msgs.append(self.toggle_record(False))
            closed = rec.status()
            closed["recording"] = False
        for s_ in self.sources:
            s_.stop()
        self._install(built)
        self.start()
        msgs.append(f"connected: {self.mode} (sensor {self._labels['sensor']}, ground truth {self._labels['gt']})")
        return msgs, closed

    def ports_info(self) -> dict:
        c = self._cfg
        return _jsonable({
            "type": "ports", "ports": list_ports(),
            "sensor": {"port": c.get("sensor_port") if c.get("source") == "serial" else None,
                       "baud": c.get("baud"), "connected": bool(getattr(self.sensor_src, "connected", False))},
            "gt": {"kind": c.get("gt_kind") if c.get("source") == "serial" else c.get("source"),
                   "value": c.get("gt_value") if c.get("source") == "serial" else self._labels["gt"],
                   "connected": bool(getattr(self.gt_src, "connected", False))},
        })

    def sim_control(self, msg: dict) -> list[str]:
        """Operator controls on the simulated rig: fan profile, fan on/off, pitot on/off, static load on the plate."""
        if not self.sim:
            raise ValueError("sim controls only work with the simulator source")
        out = []
        with self.sensor_src.lock:
            if "profile" in msg and msg["profile"] is not None:
                if msg["profile"] not in PROFILES:
                    raise ValueError(f"unknown profile {msg['profile']!r}; choose from {', '.join(PROFILES)}")
                self.sim.profile = msg["profile"]
                self._cfg["profile"] = msg["profile"]
                out.append(f"sim profile {msg['profile']}")
            if msg.get("fan") in ("on", "off"):
                self.sim.fan_on = msg["fan"] == "on"
                out.append(f"sim fan {msg['fan']}")
            if msg.get("pitot") in ("on", "off"):
                self.sim.pitot_on = msg["pitot"] == "on"
                out.append(f"sim pitot {msg['pitot']}")
            if msg.get("load_g") is not None:
                g = float(msg["load_g"])
                if not math.isfinite(g) or g < 0 or g > 5000:
                    raise ValueError("load_g must be between 0 and 5000 g")
                self.sim.load_n = g / 1000.0 * 9.80665
                out.append(f"sim load {g:g} g on the plate ({self.sim.load_n:.4f} N)")
        if self.recorder:
            self.recorder.event("sim", detail={k: msg[k] for k in ("profile", "fan", "pitot", "load_g") if k in msg})
        return out

    # ------------------------------------------------------------------ source callbacks (threads)
    def _on_sensor(self, t, row, gen=None):
        with self.lock:
            if gen is None or gen == self._gen:
                self.sensor.append(t, row)

    def _on_gt(self, t, v, gen=None):
        with self.lock:
            if gen is not None and gen != self._gen:
                return
            if self._cfg.get("gt_pressure"):  # GT script prints pitot dynamic pressure (Pa): v = √(2·dp/ρ)
                rho = self.sensor.cols.get("rho", [])
                pa = v
                v = float(models.pitot_speed(pa, rho[-1] if rho and rho[-1] > 0.5 else models.RHO_REF))
                self.gt.append(t, {"gt": v, "pa": pa})
            else:
                self.gt.append(t, {"gt": v})

    def _on_info(self, line: str, gen=None):
        if gen is not None and gen != self._gen:
            return
        if line.startswith("#env"):  # polled every second: goes to telemetry, not the log
            self.env = {**parse_env(line), "t": time.perf_counter()}
            return
        self.info.append(line)
        if line.startswith("#cal "):
            try:
                self._on_cal(json.loads(line[5:]))
            except json.JSONDecodeError:
                pass

    def _on_cal(self, cal: dict, gen=None):
        """Board's GET reply. Board-side settings (tare, load-cell scale) are mirrored into the PC params
        so the preview and exports describe what the board is really doing."""
        if gen is not None and gen != self._gen:
            return
        self.board_cal = cal
        before = dict(self.params)
        for spec in models.PARAMS:
            if spec.group == "board" and isinstance(cal.get(spec.name), (int, float)):
                self.params[spec.name] = float(cal[spec.name])
        method = str(cal.get("method", "")).lower()
        if method and method != models.BOARD_METHOD:
            self.info.append(f"#err board runs {cal['method']}, not {models.BOARD_METHOD}: "
                             "flash the airspeed_drag_plate build")
        self._mark("board", before, detail="board #cal sync")
        if not self.info or not self.info[-1].startswith("#cal"):
            self.info.append("#cal " + json.dumps(cal))

    # ---------------------------------------------------------------------------------- lifecycle
    def start(self):
        # The relay is made when the board's first bytes arrive, not here: a second dashboard (or a test) that cannot
        # open the board never takes the relay's name from the one that has it. Never relay the relay to itself.
        if self.mode == "live" and relay.available() and str(self._cfg.get("sensor_port")) != str(relay.LINK):
            self._relay_state = "wanted"
        for s in self.sources:
            s.start()

    def stop(self):
        for s in self.sources:
            s.stop()
        if self.recorder:
            self.recorder.close()
        self._relay_state = "off"
        if self.relay:
            self.relay.close()
            self.relay = None

    def _relay_write(self, chunk: bytes):
        r = self.relay
        if r is None:
            if self._relay_state != "wanted":
                return
            try:
                r = self.relay = BoardRelay()
                self._relay_state = "on"
                print(f"Wind Tunnel Logger: the board is also on {relay.LINK} (pnpm logger, port "
                      f"\"{relay.LABEL}\"); give the logger the tunnel's ground truth, not this dashboard", flush=True)
            except OSError as e:
                self._relay_state = "failed"
                self.info.append(f"#relay for the Wind Tunnel Logger not available: {e}")
                return
        r.write(chunk)

    # ------------------------------------------------------------------------------------ params
    def _mark(self, kind: str, before: dict, detail=None):
        """Tell the recording session that params may have changed (opens a new pset if so)."""
        if self.recorder and before != self.params:
            self.recorder.change(self.params, kind=kind, detail=detail)

    def set_params(self, values: dict, kind: str = "param", detail=None) -> list[str]:
        """Several params as one change (one pset)."""
        before = dict(self.params)
        errs = [e for k, v in values.items() if (e := self.set_param(k, float(v), mark=False))]
        self._mark(kind, before, detail)
        return errs

    def set_param(self, name: str, value: float, mark: bool = True) -> str | None:
        b = models.bounds()
        if name not in b:
            return f"unknown parameter {name!r}"
        lo, hi = b[name]
        before = dict(self.params)
        self.params[name] = float(min(max(value, lo), hi))
        self._replace_pending = True
        if mark:
            self._mark("param", before)
        if self.auto_push and name in models.FIRMWARE_PARAMS:
            self.command(f"SET {name} {self.params[name]:.9g}")
        return None

    def command(self, line: str, log_event: bool = True) -> bool:
        rec = self.recorder
        if rec and log_event:
            word = line.strip().split(" ")[0].upper()
            rec.event("tare" if word == "TARE" else "command", name=word, detail=line.strip())
        src = self.sensor_src
        if src is None or not hasattr(src, "send_command"):
            self.info.append(f"#err no board connected; not sent: {line}")
            return False
        ok = src.send_command(line)
        self.info.append(f"> {line}" if ok else f"#err command failed: {line}")
        if ok and line.strip().upper() == "TARE":
            # TARE is non-blocking on the board: read the new zero back once it has settled
            threading.Timer(0.6, lambda: src.send_command("GET")).start()
        return ok

    def poll_env(self):
        """Ask the board (or simulator) for its environment readings, without logging the request."""
        src = self.sensor_src
        if src is not None and hasattr(src, "send_command") and getattr(src, "connected", False):
            src.send_command("ENV")

    def push_params(self) -> int:
        lines = export.push_lines(self.params)
        if self.recorder:
            self.recorder.event("push", detail={"lines": lines})
        for ln in lines:
            self.command(ln, log_event=False)
        self.command("GET", log_event=False)
        return len(lines)

    # ----------------------------------------------------------------------------------- preview
    def _snapshot_run(self, window_s: float | None) -> Run:
        with self.lock:
            ts, cols = self.sensor.arrays()
            tg, g = self.gt.arrays()
        run = Run(ts, cols, tg, g.get("gt", np.zeros(0)))
        return run.window(window_s)

    def _compute_est(self, raw: dict, state: models.DynState) -> np.ndarray | None:
        try:
            return models.apply(self.params, raw, state=state)
        except KeyError:
            return None  # no feature column (velocity-only stream)

    def build_frame(self) -> dict | None:
        """New rows since the last frame, or a full replacement after a param change."""
        replace = self._replace_pending
        with self.lock:
            if replace:
                self._replace_pending = False
                start = max(self.sensor.seq_end - int(REPLACE_WINDOW_S * 200), self.sensor.seq0)
            else:
                start = self._sent_seq
            ts, cols = self.sensor.arrays(start)
            tg, g = self.gt.arrays()
            gt_last = tg[-1] if len(tg) else None
        now = time.perf_counter()
        gtv = g.get("gt", np.zeros(0))
        if len(ts) == 0:
            # GT only (no sensor yet): stream the GT itself so the operator sees it is alive
            with self.lock:
                g_end = self.gt.seq_end
                g0 = self.gt.seq0
            if len(tg) == 0 or (not replace and self._gt_only_sent >= g_end):
                return None
            i0 = 0 if replace else max(self._gt_only_sent - g0, 0)
            i0 = min(i0, len(tg))
            self._gt_only_sent = g_end
            k = len(tg) - i0
            return {"type": "frame", "replace": replace, "decimated": 1, "t": _clean(tg[i0:]),
                    "gt": _clean(gtv[i0:]), "est": [None] * k, "dev": [None] * k, "raw": {},
                    "telemetry": self.telemetry()}
        # hold back rows newer than the last GT sample (so each row gets its GT), unless GT is absent/stale
        gt_live = gt_last is not None and now - gt_last < 0.5
        if gt_live and not replace:
            keep = ts <= gt_last
            ts = ts[keep]
            cols = {k: v[keep] for k, v in cols.items()}
        if len(ts) == 0:
            return None
        if replace:
            state = models.DynState()
            est = self._compute_est(cols, state)
            self._dyn = state
        else:
            est = self._compute_est(cols, self._dyn)
        self._sent_seq = start + len(ts)
        gt_on = resample(tg, gtv, ts) if len(tg) >= 2 else np.full(len(ts), np.nan)
        sl = slice(None, None, REPLACE_DECIM) if replace else slice(None)
        raw = {k: _clean(cols[k][sl], 6) for k in RAW_SEND if k in cols}
        return {
            "type": "frame", "replace": replace, "decimated": REPLACE_DECIM if replace else 1,
            "t": _clean(ts[sl], 4), "gt": _clean(gt_on[sl]),
            "est": _clean(est[sl]) if est is not None else [None] * len(ts[sl]),
            "dev": _clean(cols["v"][sl]) if "v" in cols else [None] * len(ts[sl]),
            "raw": raw, "telemetry": self.telemetry(),
        }

    _gt_only_sent = 0

    def telemetry(self) -> dict:
        with self.lock:
            ts, cols = self.sensor.arrays(max(self.sensor.seq_end - 1000, self.sensor.seq0), ["n", "rho", "temp_c", "pressure_pa"])
            tg, _ = self.gt.arrays(max(self.gt.seq_end - 1000, self.gt.seq0), [])
            rec = self.recorder
            n_all = np.asarray(self.sensor.cols.get("n", []), dtype=float)
        now = time.perf_counter()
        s = metrics.stream_stats(ts, None, now=now)
        drops = metrics.stream_stats(n_all, n_all)["drops"] if len(n_all) > 1 else 0
        g = metrics.stream_stats(tg, now=now)

        def last(k):
            a = cols.get(k)
            if a is None or not len(a):
                return None
            f = a[np.isfinite(a)]
            return float(f[-1]) if len(f) else None

        bad = getattr(getattr(self.sensor_src, "parser", None), "bad_lines", 0)
        state, detail = self.board_state(s["hz"], now)
        return _jsonable({
            "board_state": state, "board_detail": detail,
            "sensor_hz": s["hz"], "gt_hz": g["hz"], "drops": drops, "bad_lines": bad,
            "rho": last("rho"), "temp_c": last("temp_c"), "pressure_pa": last("pressure_pa"),
            "port_sensor": self._sensor_label(), "port_gt": self._labels["gt"],
            "relay": str(self.relay.link) if self.relay else None,
            "sensor_status": getattr(self.sensor_src, "status", "not configured"),
            "gt_status": getattr(self.gt_src, "status", "not configured"),
            "sensor_connected": bool(getattr(self.sensor_src, "connected", False)),
            "gt_connected": bool(getattr(self.gt_src, "connected", False)),
            "recording": rec.path.name if rec else None,
            "method": self._method(),
            "profile": self.sim.profile if self.sim else None,
            "auto_push": self.auto_push,
            "board": self.board_cal,
            "loadcell_lab": getattr(self.sensor_src, "loadcell_lab", None),
            "env": ({**{k: v for k, v in self.env.items() if k != "t"},
                     "age_s": time.perf_counter() - self.env["t"]} if self.env else None),
        })

    def _sensor_label(self) -> str:
        src = self.sensor_src
        if getattr(src, "auto", False):
            return src.port if src.port != AUTO_PORT else "auto-detect"
        return self._labels["sensor"]

    def board_state(self, hz: float | None, now: float) -> tuple[str, str]:
        """Where the real board stands, so the dashboard never passes off silence or the simulator as data:
        sim | replay | none | absent | closed | opening | silent | no_data | streaming."""
        if self.mode == "sim":
            return "sim", "Simulator: every reading is synthetic, not from hardware"
        if self.mode == "replay":
            return "replay", f"Replaying {self._labels['sensor']}: recorded data, not live"
        src = self.sensor_src
        if src is None:
            return "none", "No board port configured (pitot only)"
        if not src.connected:
            if getattr(src, "auto", False) and src.port == AUTO_PORT:
                return "absent", "No Nucleo found on USB"
            return "closed", f"Cannot open {src.port}: {src.status}"
        last = src.last_line_t if src.last_line_t is not None else src.opened_t
        if last is None or now - last > SILENT_AFTER_S:
            return "silent", f"{src.port} is open but the board sends nothing"
        if src.last_line_t is None:
            return "opening", f"Opened {src.port}, waiting for the board"
        last_data = getattr(src, "last_data_t", None)
        if not src.data_rows or last_data is None or now - last_data > STALE_DATA_S:
            return "no_data", f"The board on {src.port} answers but streams no readings"
        return "streaming", f"Streaming from {src.port}"

    def build_metrics(self) -> dict | None:
        run = self._snapshot_run(self.window_s)
        if len(run.t_s) < 50 or len(run.t_g) < 2:
            return None
        gt = run.gt_on_sensor()
        est = self._compute_est(run.sensor, models.DynState())
        out = {"type": "metrics", "window_s": self.window_s, "est": None, "dev": None, "diagnosis": []}
        if est is not None:
            out["est"] = metrics.compute(est, gt)
            out["diagnosis"] = metrics.diagnose(est, gt, run.sensor.get("feature"), run.sensor.get("rho"), self.params)
        if "v" in run.sensor:
            out["dev"] = metrics.compute(run.sensor["v"], gt)
        return _jsonable(out)

    def run_fit(self, method: str, window_s: float | None, free) -> dict:
        run = self._snapshot_run(window_s or self.window_s)
        prev = dict(self.params)
        r = fit.fit_run(prev, run, method, free or None)
        if r.ok:
            before = dict(self.params)
            self.params.update(r.params)
            self._replace_pending = True
            self._mark("fit", before, detail={"method": r.method, "mse_before": r.before.get("mse"),
                                              "mse_after": r.after.get("mse")})
            self.last_fit = r.to_dict()
            if self.auto_push:
                self.push_params()
        d = r.to_dict()
        d.update(type="fit_result", previous=prev, window_s=window_s or self.window_s)
        return _jsonable(d)

    def params_msg(self) -> dict:
        """PC params, plus what the board last reported via GET (its live values)."""
        return _jsonable({"type": "params", "model": models.MODEL_ID, "params": self.params,
                          "board": {k: v for k, v in self.board_cal.items() if k in models.FIRMWARE_PARAMS}})

    def hello(self) -> dict:
        with self.lock:
            chans = list(self.sensor.cols)
        return _jsonable({
            "type": "hello", "mode": self.mode, "model": models.MODEL_ID, "models": models.catalog(),
            "params": self.params, "channels": chans,
            "board": {k: v for k, v in self.board_cal.items() if k in models.FIRMWARE_PARAMS}, "window_s": self.window_s,
            "profiles": list(PROFILES) if self.sim else [], "firmware_params": list(models.FIRMWARE_PARAMS),
            "fit_methods": list(fit.METHODS), "telemetry": self.telemetry(),
            "labels": {models.MODEL_ID: "Drag plate"}, "method": self._method(),
            "gt_pressure": bool(self._cfg.get("gt_pressure")),
            "source": self._cfg.get("source"), "sources": dict(self._labels),
            "runs_dir": str(recmod.RUNS_DIR),
            "server_id": SERVER_ID, "live_reload": LIVE_RELOAD["on"],
            "sim": ({"profile": self.sim.profile, "fan": "on" if self.sim.fan_on else "off",
                     "pitot": "on" if self.sim.pitot_on else "off",
                     "load_g": self.sim.load_n / 9.80665 * 1000.0} if self.sim else None),
        })

    def _method(self) -> str | None:
        return self.board_cal.get("method") or (self.sim.method if self.sim else None)

    def toggle_record(self, on: bool, label: str | None = None) -> str:
        if on and not self.recorder:
            meta = {"mode": self.mode, "method": self._method(),
                    "port_sensor": self._labels["sensor"], "port_gt": self._labels["gt"],
                    "profile": self.sim.profile if self.sim else None}
            with self.lock:
                self._rec_seq = self.sensor.seq_end  # only data from now on
                self._rec_gt_seq = self.gt.seq_end
            self._rec_dyn = models.DynState()
            self._rec_log = {"epoch": time.time() - time.perf_counter(), "gt_t": [], "gt_v": [], "gt_pa": [],
                             "st_t": [], "st_v": []}
            self.recorder = Session(self.params, meta, label=label or f"{self.mode}_drag_plate")
            return f"recording to {self.recorder.path}"
        if not on and self.recorder:
            self.record_tick(final=True)
            rec, self.recorder = self.recorder, None
            p = rec.close()
            msg = f"saved {rec.rows} rows to {p} and {rec.json_path.name}"
            self.last_tunnel_run = None
            try:
                files = self._save_tunnel_run(rec.path.stem)
            except OSError as e:
                return f"{msg}; the Wind Tunnel Logger files could not be written: {e}"
            if files:
                self.last_tunnel_run = {"name": tunnel._base(files[0]), "dir": str(files[0].parent)}
            return f"{msg}; as a Wind Tunnel Logger run: {', '.join(f.name for f in files)} in {files[0].parent}" if files else msg
        return "recording unchanged"

    def record_tick(self, final: bool = False):
        """Append newly aligned rows to the session: each sensor sample gets GT interpolated onto it
        (held back until GT has caught up; on stop, `final`, every row goes in) and the PC preview computed with the
        params in force now."""
        rec = self.recorder
        if not rec:
            return
        with self.lock:
            start = max(self._rec_seq, self.sensor.seq0)
            ts, cols = self.sensor.arrays(start)
            tg, g = self.gt.arrays(max(self.gt.seq_end - 2000, self.gt.seq0))
            tg_new, g_new = self.gt.arrays(max(self._rec_gt_seq, self.gt.seq0))
            self._rec_gt_seq = self.gt.seq_end
        log = self._rec_log
        if log is not None and len(tg_new):
            log["gt_t"].append(tg_new)
            log["gt_v"].append(g_new["gt"])
            if "pa" in g_new:
                log["gt_pa"].append(g_new["pa"])
        if len(ts) == 0:
            return
        gt_last = tg[-1] if len(tg) else None
        if not final and gt_last is not None and time.perf_counter() - gt_last < 0.5:
            n = int(np.searchsorted(ts, gt_last, side="right"))
            ts, cols = ts[:n], {k: v[:n] for k, v in cols.items()}
            if n == 0:
                return
        est = self._compute_est(cols, self._rec_dyn)
        gt_on = resample(tg, g.get("gt", np.zeros(0)), ts) if len(tg) >= 2 else np.full(len(ts), np.nan)
        self._rec_seq = start + len(ts)
        if log is not None and "v" in cols:
            log["st_t"].append(ts)
            log["st_v"].append(cols["v"])
        rec.add_rows(ts, gt_on, cols.get("v"), est, cols.get("feature"), cols.get("rho"), cols.get("temp_c"),
                     cols.get("pressure_pa"))

    def _save_tunnel_run(self, stem: str) -> list[Path]:
        """The recording's raw tunnel and board samples as a Wind Tunnel Logger run (calib/tunnel.py write_run): from
        the board in data/tunnel/, from the simulator in calibration/runs/ with a "_simulated" name, as the logger
        names its own. Nothing without ground truth, and nothing for a replay (that run is already on disk)."""
        log, self._rec_log = self._rec_log, None
        if not log or not log["gt_t"] or self.mode == "replay":
            return []
        cat = lambda k: np.concatenate(log[k]) if log[k] else None  # noqa: E731
        adapter = str(self._cfg.get("gt_value") or "")
        if self.sim:
            device, port, suffix, folder = "VDAS", "SIMULATED-VDAS", "_simulated", recmod.RUNS_DIR
        elif "giga_gt" in adapter:
            device, port, suffix, folder = "GIGA", os.environ.get("CALIB_GIGA_PORT") or "GIGA", "_giga", tunnel.RUN_DIRS[0]
        elif "vdas_gt" in adapter:
            device, port, suffix, folder = "VDAS", "VDAS-HID", "", tunnel.RUN_DIRS[0]
        else:
            device, port, suffix, folder = "pitot", self._labels.get("gt", "-"), "_pitot", tunnel.RUN_DIRS[0]
        pa = cat("gt_pa")
        gt_t = cat("gt_t")
        return tunnel.write_run(
            Path(folder) / f"{stem}{suffix}",
            {"t": gt_t + log["epoch"], "v": cat("gt_v"), "pa": pa if pa is not None and len(pa) == len(gt_t) else None,
             "device": device, "port": port},
            {"t": (cat("st_t") if log["st_t"] else np.zeros(0)) + log["epoch"],
             "v": cat("st_v") if log["st_v"] else np.zeros(0), "name": "Student", "port": self._labels.get("sensor", "-")})

    def session_status(self) -> dict | None:
        rec = self.recorder
        return rec.status() if rec else None

    def do_export(self, install: bool = False) -> dict:
        lf = self.last_fit
        cur = self.build_metrics() or {}
        return export.write(
            self.params, fit_info={k: lf[k] for k in ("method", "free", "n", "message")} if lf else {"method": "manual"},
            metrics_before=lf["before"] if lf else None, metrics_after=(cur.get("est") or {}), install=install)


# ============================================================================================ app
def create_app(engine: Engine) -> FastAPI:
    clients: set = set()
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fit")

    async def broadcast(msg: dict):
        if not clients:
            return
        data = json.dumps(msg, allow_nan=False, default=str)
        for q in list(clients):
            if q.qsize() > 200:  # slow client: drop frames rather than grow memory
                continue
            q.put_nowait(data)

    async def pump():
        watcher = FileWatcher(STATIC, DATASHEETS) if LIVE_RELOAD["on"] else None
        k = 0
        log_every = 0
        last_tele = 0.0
        while True:
            await asyncio.sleep(1.0 / FRAME_HZ)
            k += 1
            try:
                fr = engine.build_frame()
                now = time.perf_counter()
                if fr:
                    await broadcast(fr)
                    last_tele = now
                elif now - last_tele > TELEMETRY_IDLE_S:  # no data flowing: still say why (SPEC §6 telemetry)
                    await broadcast({"type": "telemetry", "telemetry": engine.telemetry()})
                    last_tele = now
                engine.record_tick()
                if k % int(FRAME_HZ * ENV_POLL_S) == 0:
                    engine.poll_env()
                if watcher and k % int(FRAME_HZ / 2) == 0:
                    changed = await asyncio.get_running_loop().run_in_executor(None, watcher.changes)
                    if changed:  # SPEC §6 `reload`: CSS is swapped in place, anything else reloads the page
                        await broadcast({"type": "reload", "files": [Path(f).name for f in changed],
                                         "css_only": all(f.endswith(".css") for f in changed)})
                if k % int(FRAME_HZ) == 0:
                    st = engine.session_status()
                    if st:
                        await broadcast(st)
                if k % int(FRAME_HZ / METRICS_HZ) == 0:
                    m = await asyncio.get_running_loop().run_in_executor(None, engine.build_metrics)
                    if m:
                        await broadcast(m)
                while engine.info:
                    line = engine.info.popleft()
                    lvl = "error" if line.startswith(("#err", "ERROR:")) else "info"
                    await broadcast({"type": "log", "level": lvl, "msg": line})
                    if line.startswith("#cal"):  # board values changed (push, TARE, reconnect): resync the dashboard
                        await broadcast(engine.params_msg())
            except Exception as e:  # keep streaming whatever happens
                log_every += 1
                if log_every % 20 == 1:
                    await broadcast({"type": "log", "level": "error", "msg": f"server: {e!r}"})

    @asynccontextmanager
    async def lifespan(app):
        engine.start()
        task = asyncio.create_task(pump())
        yield
        task.cancel()
        engine.stop()
        pool.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="Airspeed calibration", lifespan=lifespan)

    async def handle(msg: dict, ws_send):
        t = msg.get("type")
        loop = asyncio.get_running_loop()
        if t == "set_param":
            err = engine.set_param(str(msg.get("name")), float(msg.get("value")))
            await broadcast(engine.params_msg() if not err
                            else {"type": "log", "level": "error", "msg": err})
        elif t == "set_params":
            vals = {k: v for k, v in (msg.get("params") or {}).items() if k in models.bounds()}
            engine.set_params(vals)
            await broadcast(engine.params_msg())
        elif t == "reset_params":
            engine.set_params(models.default_params(), kind="param", detail="reset to defaults")
            await broadcast(engine.params_msg())
        elif t == "set_window":
            engine.window_s = float(min(max(float(msg.get("window_s", 30)), 5), 120))
            await broadcast({"type": "log", "level": "info", "msg": f"metrics window {engine.window_s:g} s"})
        elif t == "fit":
            await broadcast({"type": "log", "level": "info", "msg": f"fitting {msg.get('method', 'robust_lsq')}…"})
            res = await loop.run_in_executor(pool, engine.run_fit, msg.get("method", "robust_lsq"),
                                             msg.get("window_s"), msg.get("free"))
            await broadcast(res)
            await broadcast(engine.params_msg())
        elif t == "push_params":
            n = engine.push_params()
            await broadcast({"type": "log", "level": "info", "msg": f"pushed {n} parameters, requested GET"})
        elif t == "auto_push":
            engine.auto_push = bool(msg.get("on"))
            await broadcast({"type": "log", "level": "info", "msg": f"auto-push {'on' if engine.auto_push else 'off'}"})
        elif t == "command":
            engine.command(str(msg.get("line", "")))
        elif t == "record":
            was = engine.recorder
            await broadcast({"type": "log", "level": "info",
                             "msg": engine.toggle_record(bool(msg.get("on")), msg.get("label"))})
            if was and not engine.recorder:
                st = was.status()
                st["recording"] = False
                st["tunnel_run"] = engine.last_tunnel_run  # SPEC §6: also saved as a Wind Tunnel Logger run
                await broadcast(st)
        elif t == "note":
            if engine.recorder:
                engine.recorder.note(str(msg.get("text", "")))
                await broadcast({"type": "log", "level": "info", "msg": f"note: {msg.get('text', '')}"})
            else:
                await ws_send({"type": "log", "level": "warning", "msg": "not recording; note not saved"})
        elif t == "export":
            install = bool(msg.get("install"))
            try:
                paths = await loop.run_in_executor(None, engine.do_export, install)
            except OSError as e:
                await ws_send({"type": "log", "level": "error", "msg": f"export failed: {e}"})
                return
            files = list(dict.fromkeys(paths.values()))
            await broadcast({"type": "log", "level": "info", "msg": "exported " + ", ".join(files), "paths": paths})
            await broadcast({"type": "exported", "files": files, "installed": "installed" in paths})
        elif t == "sim":
            try:
                for line in engine.sim_control(msg):
                    await broadcast({"type": "log", "level": "info", "msg": line})
            except (ValueError, TypeError) as e:
                await ws_send({"type": "log", "level": "error", "msg": str(e)})
        elif t == "ports":
            await ws_send(await loop.run_in_executor(None, engine.ports_info))
        elif t == "connect":
            cfg = {k: msg.get(k) for k in ("source", "sensor_port", "baud", "gt_kind", "gt_value", "gt_baud",
                                           "gt_pressure", "replay_file", "speed", "profile", "seed")}
            try:
                msgs, closed = await loop.run_in_executor(None, engine.connect, cfg)
            except ValueError as e:
                await broadcast({"type": "log", "level": "error", "msg": f"connect failed: {e}"})
                return
            if closed:
                await broadcast(closed)
            for line in msgs:
                await broadcast({"type": "log", "level": "info", "msg": line})
            await broadcast(engine.hello())
        elif t == "list_runs":
            await ws_send({"type": "runs", "runs": await loop.run_in_executor(None, recmod.list_runs)})
        elif t == "run_summary":
            try:
                summ = await loop.run_in_executor(None, recmod.run_summary, str(msg.get("name", "")))
            except (ValueError, OSError) as e:
                await ws_send({"type": "log", "level": "error", "msg": str(e)})
                return
            await ws_send(_jsonable({"type": "run_summary", **summ}))
        elif t == "hello":
            await ws_send(engine.hello())
        else:
            await ws_send({"type": "log", "level": "error", "msg": f"unknown message type {t!r}"})

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        q: asyncio.Queue = asyncio.Queue()
        clients.add(q)
        engine._replace_pending = True  # newcomer gets the recent history

        async def sender():
            while True:
                await ws.send_text(await q.get())

        async def ws_send(m):
            await q.put(json.dumps(_jsonable(m), allow_nan=False, default=str))

        stask = asyncio.create_task(sender())
        await ws_send(engine.hello())
        try:
            while True:
                text = await ws.receive_text()
                try:
                    msg = json.loads(text)
                    await handle(msg, ws_send)
                except (ValueError, TypeError) as e:
                    await ws_send({"type": "log", "level": "error", "msg": f"bad message: {e}"})
        except WebSocketDisconnect:
            pass
        finally:
            clients.discard(q)
            stask.cancel()

    @app.get("/api/state")
    async def state():
        return JSONResponse(engine.hello())

    # Wind Tunnel Logger runs (calib/tunnel.py): read-only, so plain HTTP rather than the websocket
    @app.get("/api/tunnel/runs")
    async def tunnel_runs():
        return JSONResponse({"runs": tunnel.list_runs(), "dirs": [str(d) for d in tunnel.RUN_DIRS]})

    @app.get("/api/tunnel/run")
    async def tunnel_run(name: str = "", dir: str = "", series: int = 1):
        try:
            # only the logger's own folders: never an arbitrary path from the page
            dirs = [d for d in tunnel.RUN_DIRS if not dir or str(d) == dir] or tunnel.RUN_DIRS
            r = await asyncio.get_running_loop().run_in_executor(None, lambda: tunnel.load(Path(name).name or None, dirs, series=bool(series)))
        except (FileNotFoundError, ValueError) as e:
            return JSONResponse({"error": str(e)}, status_code=404)
        return JSONResponse(r)

    @app.get("/api/tunnel/window")
    async def tunnel_window(name: str, a: float, b: float, dir: str = ""):
        """Results page: a slice of a run at full resolution (calib/tunnel.py window)."""
        try:
            dirs = [d for d in tunnel.RUN_DIRS if not dir or str(d) == dir] or tunnel.RUN_DIRS
            if not (b > a) or b - a > 600:
                raise ValueError("ask for 0 < b - a <= 600 s")
            r = await asyncio.get_running_loop().run_in_executor(None, lambda: tunnel.window(Path(name).name, a, b, dirs))
            return JSONResponse(r)
        except (FileNotFoundError, ValueError) as e:
            return JSONResponse({"error": str(e)}, status_code=404)

    @app.post("/api/reveal")
    async def reveal(body: dict):
        """Show a run's file in Finder (Explorer, the file manager). Only files under the run folders."""
        try:
            p = Path(str(body.get("path", ""))).resolve(strict=True)
        except (OSError, RuntimeError):
            return JSONResponse({"error": "no such file"}, status_code=404)
        roots = [Path(d).resolve() for d in (*tunnel.RUN_DIRS, recmod.RUNS_DIR) if Path(d).exists()]
        if not any(p == r or r in p.parents for r in roots):
            return JSONResponse({"error": "only files in the run folders"}, status_code=403)
        cmd = (["open", "-R", str(p)] if sys.platform == "darwin" else ["explorer", f"/select,{p}"] if os.name == "nt"
               else ["xdg-open", str(p.parent if p.is_file() else p)])
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        return JSONResponse({"ok": True, "path": str(p)})

    @app.get("/")
    async def index():
        page = STATIC / "index.html"
        if page.exists():
            # index.html is an artifact fragment (no doctype/head/body): wrap it so it renders in
            # standards mode. Read fresh on every request so edits show on reload.
            body = page.read_text(encoding="utf-8")
            if body.lstrip()[:9].lower() == "<!doctype":
                return HTMLResponse(body, headers={"Cache-Control": "no-store"})
            html = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                    '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
                    "</head><body>" + body + "</body></html>")
            return HTMLResponse(html, headers={"Cache-Control": "no-store"})
        return HTMLResponse("<h1>Calibration server running</h1><p>app/static/index.html is missing; "
                            "connect a client to <code>ws://localhost:8765/ws</code>.</p>")

    @app.middleware("http")
    async def no_cache(request, call_next):
        # the dashboard is ES modules loaded straight from disk: always serve the current file
        resp = await call_next(request)
        resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    if DATASHEETS.is_dir():
        app.mount("/datasheets", StaticFiles(directory=DATASHEETS), name="datasheets")
    STATIC.mkdir(parents=True, exist_ok=True)
    app.mount("/", StaticFiles(directory=STATIC), name="static")

    return app


def serve(engine: Engine, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True,
          live_reload: bool = False):
    import uvicorn

    LIVE_RELOAD["on"] = live_reload

    app = create_app(engine)
    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}"
    print(f"calibration dashboard: {url}  (mode={engine.mode})")
    if open_browser:
        threading.Timer(1.0, lambda: __import__("webbrowser").open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
