"""The sensor on the Pi: camera → ball → estimator → a line every 5 ms, plus the setup and checking tools.

run      Two threads. The capture thread waits on the camera, finds the ball in each frame and updates the
         estimator. The output thread wakes on an absolute 5 ms schedule, asks the estimator for the speed at that
         instant and writes the line. So the stream stays at 200 Hz whatever the camera does, and a dropped frame
         only means one tick predicts a little further ahead. Once a second it prints what it is doing.
setup    Fan off: find the ball, measure where it rests and how it looks, and save that in the config.
preview  A window on the Pi's desktop (or Pi Connect screen sharing) with the tracked ball and the live speed.
         For setting up and checking only: drawing makes the camera drop frames, so never during a scored run.
record   Frames only, for a pluck test (the ball's free decay in still air).

Stopped by SIGTERM (systemctl stop), SIGINT (Ctrl+C) or SIGHUP (a closed Pi Connect shell tab), after which it
closes the port and the recording cleanly.
"""
from __future__ import annotations

import json
import signal
import sys
import threading
import time
from pathlib import Path

import numpy as np

from . import estimator, features, models, stream

HERE = Path(__file__).resolve().parent
EXAMPLE = HERE.parent / "config.example.json"


def load(path) -> dict:
    return json.loads(Path(path).read_text())


def save(path, cfg: dict):
    Path(path).write_text(json.dumps(cfg, indent=2) + "\n")


def build(cfg: dict):
    """(map, tracker, estimator) from a config."""
    model = models.from_dict(cfg["map"])
    ball = features.from_config(cfg["ball"])
    e = dict(cfg.get("estimator", {"name": "kalman", "sigma_y": 0.3}))
    fps = float(cfg.get("camera", {}).get("fps", 120.0))
    est = estimator.make(e.pop("name"), model, 1.0 / fps, **e)
    return model, ball, est


def _camera(cfg):
    from .capture import Camera
    return Camera(**cfg.get("camera", {}))


def _stop_on_signals(stop: threading.Event):
    return {sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}


def run(cfg: dict, port: str | None = None, record: str | None = None, seconds: float | None = None, quiet=False):
    if not cfg.get("ball"):
        raise SystemExit("no ball in the config yet: run `aircam setup` first (fan off)")
    sys.setswitchinterval(0.0005)           # hand the GIL over quickly, so the output thread wakes on time
    model, ball, est = build(cfg)
    out = cfg.get("output", {})
    port = port or out.get("port", "-")
    cam = _camera(cfg)
    sink = stream.Sink(port, int(out.get("baud", 115200)))
    stop = threading.Event()
    previous = _stop_on_signals(stop)
    header = stream.comment(k=float(model.k), arm=float(model.arm_mm))
    log = (lambda *a: None) if quiet else (lambda msg: print(msg, file=sys.stderr, flush=True))
    log(f"aircam: streaming to {port} at {out.get('rate_hz', 200):g} Hz; map k {model.k:.4g} m/s, arm "
        f"{model.arm_mm:.4g} mm; Ctrl+C stops")

    out_dir = None
    if record:
        out_dir = Path(record).expanduser() / time.strftime("session_%Y%m%d_%H%M%S")
        out_dir.mkdir(parents=True)
        save(out_dir / "config.json", cfg)
    f_frames = open(out_dir / "frames.csv", "w", buffering=1 << 16) if out_dir else None
    f_ticks = open(out_dir / "ticks.csv", "w", buffering=1 << 16) if out_dir else None
    if f_frames:
        f_frames.write("t,d,x,y,received\n")
        f_ticks.write("t,v\n")

    lock = threading.Lock()
    st = {"frames": 0, "lost": 0, "latency": 0.0}

    def capture():
        cam.start()
        try:
            for t, gray, md in cam.frames():
                if stop.is_set():
                    break
                d = ball(gray, t)
                now = stream.clock()
                with lock:
                    est.update(t, d)
                st["frames"] += 1
                st["lost"] += d is None
                st["latency"] = now - t
                if f_frames:
                    x, y = ball.pos
                    f_frames.write(f"{t:.6f},{'' if d is None else f'{d:.4f}'},{x:.2f},{y:.2f},{now:.6f}\n")
        finally:
            cam.stop()
            stop.set()

    th = threading.Thread(target=capture, name="capture", daemon=True)
    th.start()
    ticker = stream.Ticker(float(out.get("rate_hz", 200.0)))
    t_end = None if seconds is None else stream.clock() + seconds
    t_report, n_report, lost_report, t_header = stream.clock() + 1.0, 0, 0, 0.0
    try:
        while not stop.is_set():
            tk = ticker.wait()
            with lock:
                v = est.value(tk)
            if tk >= t_header:
                sink.write(header)
                t_header = tk + 5.0
            sink.write(stream.line(v))
            if f_ticks:
                f_ticks.write(f"{tk:.6f},{v:.4f}\n")
            if tk >= t_report:
                n = st["frames"] - n_report
                seen = 100.0 * (1 - (st["lost"] - lost_report) / n) if n else 0.0
                log(f"{v:6.2f} m/s | ball seen {seen:3.0f}% | {n:3d} fps | latency {st['latency'] * 1e3:4.1f} ms"
                    + (f" | late {ticker.late}, skipped {ticker.skipped}" if ticker.late or ticker.skipped else "")
                    + (f" | write errors {sink.errors}" if sink.errors else ""))
                t_report, n_report, lost_report = tk + 1.0, st["frames"], st["lost"]
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
    return out_dir


def grab(cam, n: int = 30, skip: int = 10):
    """The median of n frames' Y planes (after skip), so sensor noise does not move the setup."""
    cam.start()
    try:
        it = cam.frames()
        for _ in range(skip):
            next(it)
        frames = [next(it)[1].copy() for _ in range(n)]
    finally:
        cam.stop()
    return np.median(np.stack(frames), axis=0).astype(np.uint8), frames


def setup(cfg: dict, config_path, dark: bool = False, near=None, ball_mm: float = features.BALL_MM):
    """Fan off: find the ball, save where it rests and how it looks. Returns the updated config."""
    cam = _camera(cfg)
    _, frames = grab(cam, 30)
    b, still, jitter = features.setup(frames, dark=dark, near=near, ball_mm=ball_mm)
    image = Path(config_path).expanduser().with_name("aircam-setup.png")
    if b is None:
        path = features.save_image(image, still)
        raise SystemExit(f"no ball found. Look at {path}: the ball should be the brightest round thing in view "
                         "(fan off, light on, dark background). Try --near X,Y, or --dark for a dark ball.")
    cfg["ball"] = features.to_config(b)
    save(config_path, cfg)
    path = features.save_image(image, still, [(b.rest[0], b.rest[1], b.radius)])
    print(f"ball at ({b.rest[0]:.1f}, {b.rest[1]:.1f}) px, radius {b.radius:.1f} px ({b.mm_per_px:.3f} mm/px), "
          f"ball grey {b.fg:.0f} vs {b.bg:.0f} around it; still-air jitter {jitter:.3f} mm")
    print(f"saved in {config_path}; check the circle in {path}")
    if b.fg > 245:
        print("warning: the ball is saturated; lower the exposure (aircam set exposure=2000) and run setup again")
    if abs(b.fg - b.bg) < 40:
        print("warning: little contrast between the ball and its background; darken the background or add light")
    if b.radius < 8:
        print("warning: the ball is small in the image; move the camera closer for finer positions")
    return cfg


def record(cfg: dict, seconds: float = 12.0):
    """(t, x) for every frame over `seconds`: the ball's sideways offset from rest in mm, signed (d is a distance,
    so it would fold a swing through rest into two peaks a cycle). For the pluck test's free decay."""
    _, ball, _ = build(cfg)
    cam = _camera(cfg)
    ts, ds = [], []
    cam.start()
    try:
        t0 = None
        for t, gray, md in cam.frames():
            t0 = t if t0 is None else t0
            if t - t0 > seconds:
                break
            c = ball.locate(gray)
            ts.append(t)
            ds.append(np.nan if c is None else (c[0] - ball.rest[0]) * ball.mm_per_px)
    finally:
        cam.stop()
    return np.array(ts), np.array(ds)


def preview(cfg: dict, every: int = 4):
    """Every frame tracked, every `every`-th shown at half size with the ball, its rest position and the speed."""
    import os
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise SystemExit("preview needs the Pi's desktop: run it in a terminal on the Pi's screen or in Pi Connect "
                         "screen sharing, not in the remote shell (there `aircam run` prints the speed instead)")
    import cv2

    model, ball, est = build(cfg)
    cam = _camera(cfg)
    stop = threading.Event()
    previous = _stop_on_signals(stop)
    rx, ry = ball.rest
    r = ball.radius
    cam.start()
    i, n, t_fps, fps = 0, 0, time.monotonic(), 0.0
    try:
        for t, gray, md in cam.frames():
            d = ball(gray, t)
            est.update(t, d)
            i, n = i + 1, n + 1
            if i % every:
                continue
            v = est.value(t)
            now = time.monotonic()
            if now - t_fps >= 1.0:
                fps, t_fps, n = n / (now - t_fps), now, 0
            img = cv2.cvtColor(np.ascontiguousarray(gray), cv2.COLOR_GRAY2BGR)
            cv2.drawMarker(img, (int(rx), int(ry)), (255, 160, 0), cv2.MARKER_CROSS, int(2 * r), 2)
            if d is not None:
                x, y = ball.pos
                cv2.circle(img, (int(x), int(y)), int(r), (0, 255, 0), 2)
                cv2.line(img, (int(rx), int(ry)), (int(x), int(y)), (0, 255, 0), 1)
            text = f"{v:5.2f} m/s   d {d if d is not None else float('nan'):6.2f} mm   {fps:3.0f} fps"
            cv2.putText(img, text if d is not None else "ball lost   " + text, (20, 40), cv2.FONT_HERSHEY_SIMPLEX,
                        1.0, (0, 255, 255) if d is not None else (0, 0, 255), 2)
            cv2.imshow("aircam preview (q quits)", cv2.resize(img, (img.shape[1] // 2, img.shape[0] // 2)))
            if cv2.waitKey(1) & 0xFF == ord("q") or stop.is_set():
                break
    finally:
        cam.stop()
        cv2.destroyAllWindows()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
