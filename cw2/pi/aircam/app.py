"""The sensor on the Pi. The steps, in order (the commands are `aircam real …` or `aircam sim …`):

detect   Fan off: find the ping pong ball, show it in colour with a circle on it, and save it once you confirm
         it is the ball (Y; or click the ball if the circle is on something else). Nothing else runs until a
         detection has been confirmed.
view     The live colour feed with the tracking drawn on it, in a window on the Pi's desktop or in the laptop's
         browser (--web). For checking only: drawing makes the camera drop frames.
run      Two threads. The capture thread finds the ball in each frame and updates the estimator; the output
         thread wakes on an absolute 5 ms schedule and writes the speed. The stream stays at 200 Hz whatever the
         camera does. Once a second it prints what it is doing.
check    10 s of tracking, then a report. pluck: the ball's free decay in still air.

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


def require_ball(cfg: dict):
    """Tracking starts only from a detection someone has looked at and confirmed."""
    b = cfg.get("ball") or {}
    if not b.get("confirmed"):
        raise SystemExit(f"no confirmed ball yet: run `aircam {cfg.get('target', 'real')} detect` first (fan off)")


def _camera(cfg):
    from .capture import Camera
    return Camera(**cfg.get("camera", {}))


def _stop_on_signals(stop: threading.Event):
    """Ctrl+C, systemctl stop and a closed shell tab set `stop`. Only the main thread can take signals."""
    if threading.current_thread() is not threading.main_thread():
        return {}
    return {sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}


def run(cfg: dict, port: str | None = None, record: str | None = None, seconds: float | None = None, quiet=False):
    require_ball(cfg)
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


def grab(cam, n: int = 15, skip: int = 10):
    """The median of n frames (after skip), so sensor noise does not move the detection; and the frames."""
    cam.start()
    try:
        it = cam.frames()
        for _ in range(skip):
            next(it)
        frames = [next(it)[1].copy() for _ in range(n)]
    finally:
        cam.stop()
    return np.median(np.stack(frames), axis=0).astype(np.uint8), frames


def detect(cfg: dict, config_path, near=None, web: bool = False, port: int = 8080, yes: bool = False):
    """Step 1, fan off: find the ball, show it in colour, save it once confirmed. Returns the updated config."""
    cam = _camera(cfg)
    _, frames = grab(cam)
    image = Path(config_path).expanduser().with_name(f"aircam-{cfg.get('target', 'real')}-detect.png")
    while True:
        b, still, jitter = features.setup(frames, near=near)
        if b is None:
            path = features.save_image(image, still)
            raise SystemExit(f"no ball found. The camera's view is in {path}: the ball should be the biggest round "
                             "thing that stands out (fan off, light on, a plain background behind it). If it is "
                             "there, point at it: aircam … detect --near X,Y (pixels)")
        path = features.save_image(image, still, [(b.rest[0] + b.outline[0], b.rest[1] + b.outline[1], b.radius)])
        if yes:
            break
        answer = confirm(still, b, web, port)
        if answer == "y":
            break
        if answer == "n":
            raise SystemExit("not saved. Move the ball, the camera or the light and run detect again, or point at "
                             "the ball: detect --near X,Y (pixels; the grid on the image is every 100 px)")
        near = answer                                       # clicked or typed: look for the ball there
    cfg["ball"] = {**features.to_config(b), "confirmed": True}
    save(config_path, cfg)
    print(f"confirmed and saved in {config_path}: ball at ({b.rest[0]:.0f}, {b.rest[1]:.0f}) px, radius "
          f"{b.radius:.1f} px ({b.mm_per_px:.3f} mm/px), contrast {b.contrast:.0f}, still-air jitter "
          f"{jitter:.3f} mm; image {path}")
    if max(np.atleast_1d(b.fg)) > 250:
        print("warning: the ball is saturated: lower the exposure (aircam … set exposure=1500) and detect again")
    if b.contrast < 40:
        print("warning: the ball barely stands out: a darker or plainer background, or more light on the ball")
    if b.radius < 8:
        print("warning: the ball is small in the image: move the camera closer for finer positions")
    print(f"next: aircam {cfg.get('target', 'real')} view   (watch it track), then run")
    return cfg


def detect_image(still, b):
    """The detection to confirm: the colour frame, a pixel grid every 100 px, and the ball circled."""
    import cv2
    img = still.copy() if still.ndim == 3 else cv2.cvtColor(still, cv2.COLOR_GRAY2BGR)
    h, w = img.shape[:2]
    def label(text, at):
        for colour, thick in (((0, 0, 0), 3), ((255, 255, 255), 1)):     # readable on light and dark
            cv2.putText(img, text, at, cv2.FONT_HERSHEY_SIMPLEX, 0.45, colour, thick, cv2.LINE_AA)
    for x in range(100, w, 100):
        cv2.line(img, (x, 0), (x, h), (90, 90, 90), 1)
        label(str(x), (x + 3, 15))
    for y in range(100, h, 100):
        cv2.line(img, (0, y), (w, y), (90, 90, 90), 1)
        label(str(y), (3, y - 3))
    c = (int(round(b.rest[0] + b.outline[0])), int(round(b.rest[1] + b.outline[1])))
    cv2.circle(img, c, int(round(b.radius)), (0, 255, 0), 3, cv2.LINE_AA)
    cv2.drawMarker(img, c, (0, 255, 0), cv2.MARKER_CROSS, 12, 2)
    text = "Green circle on the ping pong ball?   Y = yes   N = no   or click the ball"
    size = 0.6
    while size > 0.3 and cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, size, 1)[0][0] > w - 20:
        size -= 0.05
    cv2.rectangle(img, (0, h - 34), (w, h), (0, 0, 0), -1)
    cv2.putText(img, text, (10, h - 11), cv2.FONT_HERSHEY_SIMPLEX, size, (255, 255, 255), 1, cv2.LINE_AA)
    return img


def confirm(still, b, web: bool = False, port: int = 8080):
    """Ask whether the circle is on the ball: "y", "n", or (x, y) px where the ball really is. A window with
    keys and clicks on the Pi's desktop; otherwise the image in the laptop's browser and the question here."""
    import os
    from . import view

    img = detect_image(still, b)
    if not web and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        import cv2
        scale = min(1.0, 1280 / img.shape[1])
        shown = cv2.resize(img, (int(img.shape[1] * scale), int(img.shape[0] * scale))) if scale < 1 else img
        clicked = []
        name = "aircam detect: Y yes, N no, or click the ball"
        cv2.namedWindow(name)
        cv2.setMouseCallback(name, lambda ev, x, y, *_: clicked.append((x / scale, y / scale))
                             if ev == cv2.EVENT_LBUTTONDOWN else None)
        try:
            while True:
                cv2.imshow(name, shown)
                k = cv2.waitKey(50) & 0xFF
                if clicked:
                    return clicked[-1]
                if k in (ord("y"), ord("Y")):
                    return "y"
                if k in (ord("n"), ord("N"), ord("q"), 27):
                    return "n"
        finally:
            cv2.destroyWindow(name)
    server = view.Mjpeg(port)
    try:
        server.publish(img)
        print(f"look at the detection: open {server.url} in the laptop's browser")
        while True:
            a = input("Is the green circle on the ping pong ball? [y = yes / n = no / X,Y = the ball is here]: ")
            a = a.strip().lower()
            if a in ("y", "yes", "n", "no"):
                return a[0]
            try:
                x, y = (float(v) for v in a.split(","))
                return (x, y)
            except ValueError:
                print("answer y, n, or the ball's pixel position as X,Y (the grid lines are every 100 px)")
    finally:
        server.close()


def record(cfg: dict, seconds: float = 12.0):
    """(t, x) for every frame over `seconds`: the ball's sideways offset from rest in mm, signed (d is a distance,
    so it would fold a swing through rest into two peaks a cycle). For the pluck test's free decay."""
    require_ball(cfg)
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
    require_ball(cfg)
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


def view(cfg: dict, web: bool = False, port: int = 8080, udp: str | None = None, every: int = 4,
         seconds: float | None = None):
    """The live view (view.py): every frame tracked, every `every`-th drawn with what the tracker saw, in a window
    on the Pi's desktop or, with web, in a browser on the laptop. udp=HOST[:PORT] also sends the speed to the
    screen simulator, so its panel shows the truth next to it (one line per frame: watch with it, score with run)."""
    import os
    from . import view

    require_ball(cfg)
    if not web and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise SystemExit("no desktop here (a remote shell): add --web and open the address it prints in the "
                         "laptop's browser, or run view in a terminal in Pi Connect screen sharing")
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
