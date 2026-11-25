"""The sensor itself, as it runs on the Pi: camera → reading → estimator → a line every 5 ms.

Two threads. The capture thread waits on the camera, turns each frame into a reading and updates the
estimator. The output thread wakes on an absolute 5 ms schedule, asks the estimator for the speed at that
instant and writes the line. So the stream stays at 200 Hz whatever the camera does, and a dropped frame only
means one tick predicts a little further ahead.

Stopped by SIGTERM (systemctl stop), SIGINT or SIGHUP (a closed Raspberry Pi Connect shell tab), after which
it closes the port and the recording cleanly. Every frame's reading and every tick's output can be recorded
(--record), which is what `aircam fit` calibrates from.
"""
from __future__ import annotations

import json
import signal
import sys
import threading
import time
from pathlib import Path

from . import estimator, features, models, stream


def load(path) -> dict:
    return json.loads(Path(path).read_text())


def build(cfg: dict):
    model = models.from_dict(cfg["model"])
    ext = features.from_config(cfg["feature"])
    e = dict(cfg.get("estimator", {"name": "kalman", "sigma_y": 0.002}))
    fps = float(cfg.get("camera", {}).get("fps", 120.0))
    est = estimator.make(e.pop("name"), model, 1.0 / fps, **e)
    return model, ext, est


def run(cfg: dict, port: str = "-", record: str | None = None, seconds: float | None = None):
    from .capture import Camera

    sys.setswitchinterval(0.0005)           # hand the GIL over quickly, so the output thread wakes on time
    model, ext, est = build(cfg)
    cam = Camera(**cfg.get("camera", {}))
    sink = stream.Sink(port, int(cfg.get("baud", 115200)))
    stop = threading.Event()
    previous = {sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}

    out_dir = None
    if record:
        out_dir = Path(record) / time.strftime("session_%Y%m%d_%H%M%S")
        out_dir.mkdir(parents=True)
        (out_dir / "config.json").write_text(json.dumps(cfg, indent=2))
    f_frames = open(out_dir / "frames.csv", "w", buffering=1 << 16) if out_dir else None
    f_ticks = open(out_dir / "ticks.csv", "w", buffering=1 << 16) if out_dir else None
    if f_frames:
        f_frames.write("t,y,received\n")
        f_ticks.write("t,v\n")

    lock = threading.Lock()
    st = {"frames": 0, "lost": 0, "latency": 0.0}

    def capture():
        cam.start()
        try:
            for t, gray, md in cam.frames():
                if stop.is_set():
                    break
                y = ext(gray, t)
                now = stream.clock()
                with lock:
                    est.update(t, y)
                st["frames"] += 1
                st["lost"] += y is None
                st["latency"] = now - t
                if f_frames:
                    f_frames.write(f"{t:.6f},{'' if y is None else f'{y:.6g}'},{now:.6f}\n")
        finally:
            cam.stop()
            stop.set()

    th = threading.Thread(target=capture, name="capture", daemon=True)
    th.start()
    ticker = stream.Ticker(float(cfg.get("rate_hz", 200.0)))
    t_end = None if seconds is None else stream.clock() + seconds
    t_report, n_report = stream.clock() + 5.0, 0
    try:
        while not stop.is_set():
            tk = ticker.wait()
            with lock:
                v = est.value(tk)
            sink.write(stream.line(v))
            if f_ticks:
                f_ticks.write(f"{tk:.6f},{v:.4f}\n")
            if tk >= t_report:
                print(f"aircam: {st['frames'] - n_report} frames/5 s, {st['lost']} without a reading, "
                      f"latency {st['latency'] * 1e3:.1f} ms, ticks late {ticker.late}, skipped {ticker.skipped}, "
                      f"write errors {sink.errors}", file=sys.stderr, flush=True)
                t_report, n_report = tk + 5.0, st["frames"]
            if t_end is not None and tk >= t_end:
                break
    finally:
        stop.set()
        th.join(timeout=2.0)
        sink.close()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        for f in (f_frames, f_ticks):
            if f:
                f.close()
