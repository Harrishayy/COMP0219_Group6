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
    """Ctrl+C, systemctl stop and a closed shell tab set `stop`. Only the main thread can take signals."""
    if threading.current_thread() is not threading.main_thread():
        return {}
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


def check(cfg: dict, seconds: float = 10.0, image=None) -> dict:
    """Track every frame for `seconds`, without streaming, and report how well the ball is being followed."""
    model, ball, _ = build(cfg)
    cam = _camera(cfg)
    ts, ds, cost, last = [], [], [], None
    cam.start()
    try:
        t0 = None
        for t, gray, md in cam.frames():
            t0 = t if t0 is None else t0
            if t - t0 > seconds:
                break
            c0 = time.perf_counter()
            d = ball(gray, t)
            cost.append(time.perf_counter() - c0)
            ts.append(t)
            ds.append(np.nan if d is None else d)
            last = gray
    finally:
        cam.stop()
    r = tracking_report(np.array(ts), np.array(ds), np.array(cost), model,
                        float(cfg.get("camera", {}).get("fps", 120.0)))
    if image is not None and last is not None:
        marks = [(ball.rest[0], ball.rest[1], 3.0)] + ([(*ball.pos, ball.radius)] if np.isfinite(ds[-1]) else [])
        r["image"] = features.save_image(image, np.ascontiguousarray(last), marks)
    return r


def tracking_report(t, d, cost, model, fps_set: float = 120.0) -> dict:
    """Numbers that say how well the ball was tracked over a recording (t s, d mm or NaN, cost s per frame):
    frame rate, frames with the ball, per-frame cost, where the ball sat, and the camera's own noise in mm and
    in m/s. The noise is taken frame to frame (std of the differences / √2), so a ball that is swinging slowly
    does not count as noise; with the fan off it is the floor the camera adds to every reading."""
    ok = np.isfinite(d)
    n = int(d.size)
    r = {"frames": n, "fps": (n - 1) / (t[-1] - t[0]) if n > 1 and t[-1] > t[0] else 0.0,
         "found": float(ok.mean()) if n else 0.0, "cost_ms": 1e3 * float(np.mean(cost)) if n else 0.0,
         "cost_max_ms": 1e3 * float(np.max(cost)) if n else 0.0}
    gaps = np.diff(np.flatnonzero(np.r_[True, ok, True])) - 1
    r["longest_gap"] = int(gaps.max()) if gaps.size else n
    if ok.sum() < 3:
        return r
    dd = d[ok]
    step = np.diff(d)[np.isfinite(np.diff(d))]
    r["d_mm"] = float(np.median(dd))
    r["angle_deg"] = float(np.degrees(model.theta(r["d_mm"])))
    r["v_mps"] = float(model.speed(r["d_mm"]))
    r["swing_mm"] = float(np.std(dd))
    # robust (median absolute deviation): a glint or a lost-and-found frame must not count as noise
    mad = float(np.median(np.abs(step - np.median(step)))) if step.size > 2 else float("nan")
    r["noise_mm"] = 1.4826 * mad / np.sqrt(2.0)
    r["jumps"] = int(np.sum(np.abs(step) > max(10 * r["noise_mm"], 1.0)))
    for v in (2.0, 6.0, 10.0):                 # what that noise is in m/s, per frame, at three speeds
        r[f"noise_mps_at_{v:g}"] = r["noise_mm"] / float(model.dreading_dv(v))
    r["fps_set"] = fps_set
    return r


def report_text(r: dict) -> str:
    lines = [f"{r['frames']} frames at {r['fps']:.1f} fps; ball found in {100 * r['found']:.1f}% "
             f"(longest gap {r['longest_gap']} frames); tracking {r['cost_ms']:.2f} ms a frame (max "
             f"{r['cost_max_ms']:.2f}, budget {1e3 / r.get('fps_set', 120.0):.1f})"]
    if "d_mm" not in r:
        return "\n".join(lines + ["the ball was hardly ever found: run aircam setup (fan off), and check the image"])
    lines.append(f"ball {r['d_mm']:.2f} mm from rest = {r['angle_deg']:.1f} deg = {r['v_mps']:.2f} m/s on the "
                 f"current map; it moved {r['swing_mm']:.2f} mm (std) over the recording")
    lines.append(f"camera noise {r['noise_mm']:.3f} mm per frame = {r['noise_mps_at_2']:.3f} m/s at 2 m/s, "
                 f"{r['noise_mps_at_6']:.3f} at 6, {r['noise_mps_at_10']:.3f} at 10; {r['jumps']} sudden jumps")
    tips = []
    if r["found"] < 0.99:
        tips.append("the ball was lost in some frames: more light or a darker background, or rerun aircam setup")
    if r["fps"] < 0.95 * r.get("fps_set", 120.0):
        tips.append("the camera is dropping frames: close any preview or other camera program")
    if r["d_mm"] < 3 and r["swing_mm"] < 0.5 and r["d_mm"] > 0.3:
        tips.append(f"in still air the ball reads {r['d_mm']:.2f} mm off its rest: rerun aircam setup")
    if r["jumps"]:
        tips.append("sudden jumps: a reflection or something bright near the ball; look at the image")
    return "\n".join(lines + ["-> " + tip for tip in tips] + (["-> tracking looks healthy"] if not tips else []))


def preview(cfg: dict, web: bool = False, port: int = 8080, udp: str | None = None, every: int = 4,
            seconds: float | None = None):
    """The live view (view.py): every frame tracked, every `every`-th drawn with what the tracker saw, in a window
    on the Pi's desktop or, with web, in a browser on the laptop. udp=HOST[:PORT] also sends the speed to the
    screen simulator, so its panel shows the truth next to it (one line per frame: watch with it, score with run)."""
    import os
    from . import view

    if not web and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise SystemExit("no desktop here (a remote shell): use `aircam preview --web` and open the address it "
                         "prints in the laptop's browser, or run preview in Pi Connect screen sharing")
    import cv2

    model, ball, est = build(cfg)
    cam = _camera(cfg)
    overlay = view.Overlay(ball, model, fps=float(cfg.get("camera", {}).get("fps", 120.0)))
    server = view.Mjpeg(port) if web else None
    sink = stream.Sink(f"udp:{udp if ':' in udp else udp + ':5005'}") if udp else None
    stop = threading.Event()
    previous = _stop_on_signals(stop)
    if server:
        print(f"live view: open {server.url} in a browser on the laptop (same network); Ctrl+C stops", flush=True)
    note = f"also sending to {udp}" if udp else ""
    cam.start()
    i, n, t_fps, fps, ms, t_header, t0 = 0, 0, time.monotonic(), 0.0, 0.0, 0.0, None
    try:
        for t, gray, md in cam.frames():
            t0 = t if t0 is None else t0
            searched = ball.pos
            c0 = time.perf_counter()
            d = ball(gray, t)
            ms = 0.9 * ms + 0.1 * (time.perf_counter() - c0) * 1e3
            est.update(t, d)
            v = est.value(t)
            overlay.add(t, searched, d, v)
            if sink:
                if t >= t_header:
                    sink.write(stream.comment(k=float(model.k), arm=float(model.arm_mm)))
                    t_header = t + 5.0
                sink.write(stream.line(v))
            i, n = i + 1, n + 1
            now = time.monotonic()
            if now - t_fps >= 1.0:
                fps, t_fps, n = n / (now - t_fps), now, 0
            if stop.is_set() or (seconds is not None and t - t0 > seconds):
                break
            if i % every:
                continue
            img = overlay.draw(gray, fps, ms, note=note)
            if server:
                server.publish(img)
            else:
                cv2.imshow("aircam preview (q quits)", img)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    finally:
        cam.stop()
        if server:
            server.close()
        else:
            cv2.destroyAllWindows()
        if sink:
            sink.close()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
