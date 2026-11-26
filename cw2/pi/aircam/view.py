"""The live view: the camera feed with what the tracker sees drawn on it, so you can watch how well it follows the
ball while the numbers update. Shown in a window on the Pi's desktop (Pi Connect screen sharing), or served to a
browser on the laptop as an MJPEG stream (`aircam preview --web`), which needs no desktop at all.

Drawn on every shown frame:
  yellow ring   where the tracker searched this frame (2 radii round where the ball was the frame before)
  green circle  the ball it found (red, at the last place it was seen, when it lost it)
  blue cross    the ball's rest position from setup; the line from it to the ball is the reading d
  trail         where the ball has been over the last half second
  top left      speed, d, angle, frame rate, tracking time per frame, % of the last second's frames with the ball
  bottom        the last 10 s of speed

OpenCV comes from apt on the Pi (python3-opencv). Drawing and JPEG encoding cost a few ms, so only every few
frames are shown, at half size; never run a view during a scored run.
"""
from __future__ import annotations

import socket
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

GREEN, RED, YELLOW, BLUE, WHITE, GREY = (60, 220, 60), (40, 40, 230), (0, 200, 230), (255, 150, 40), (240, 240, 240), (120, 120, 120)


class Overlay:
    def __init__(self, ball, model, chart_s: float = 10.0, trail_s: float = 0.5, fps: float = 120.0):
        self.ball, self.model, self.chart_s = ball, model, chart_s
        self.trail = deque(maxlen=max(2, int(trail_s * fps)))
        self.seen = deque(maxlen=max(2, int(fps)))
        self.hist: deque = deque()
        self.search = ball.rest
        self.last = (float("nan"),) * 4

    def add(self, t: float, searched_from, d, v: float):
        """Bookkeeping for every frame (cheap): where the search started, the reading, the speed."""
        self.search = searched_from
        self.seen.append(d is not None)
        if d is not None:
            self.trail.append(self.ball.pos)
        self.hist.append((t, v))
        while self.hist and t - self.hist[0][0] > self.chart_s:
            self.hist.popleft()
        deg = float(np.degrees(self.model.theta(d))) if d is not None else float("nan")
        self.last = (v, float("nan") if d is None else d, deg, d is not None)

    def draw(self, gray, fps: float = 0.0, ms: float = 0.0, scale: float = 0.5, note: str = ""):
        import cv2

        img = cv2.cvtColor(np.ascontiguousarray(gray), cv2.COLOR_GRAY2BGR)
        k = scale
        img = cv2.resize(img, (int(img.shape[1] * k), int(img.shape[0] * k)), interpolation=cv2.INTER_AREA)
        ox, oy = self.ball.outline                  # draw on the ball's outline, not its light-leaning centroid
        P = lambda p: (int(round((p[0] + ox) * k)), int(round((p[1] + oy) * k)))    # noqa: E731
        r = self.ball.radius * k
        v, d, deg, found = self.last
        rest, pos = P(self.ball.rest), P(self.ball.pos)
        cv2.circle(img, P(self.search), max(2, int(2 * r)), YELLOW, 1, cv2.LINE_AA)
        if len(self.trail) > 1:
            pts = np.array([P(p) for p in self.trail], np.int32).reshape(-1, 1, 2)
            cv2.polylines(img, [pts], False, (0, 140, 255), 1, cv2.LINE_AA)
        cv2.drawMarker(img, rest, BLUE, cv2.MARKER_CROSS, max(6, int(r)), 2)
        if found:
            cv2.line(img, rest, pos, GREEN, 1, cv2.LINE_AA)
        cv2.circle(img, pos, max(2, int(r)), GREEN if found else RED, 2, cv2.LINE_AA)

        seen = 100.0 * sum(self.seen) / max(len(self.seen), 1)
        big = f"{v:5.2f} m/s" if np.isfinite(v) else "  --  m/s"
        lines = [f"d {d:6.2f} mm   {deg:4.1f} deg" if found else "BALL LOST",
                 f"{fps:5.1f} fps   tracking {ms:.2f} ms   ball seen {seen:3.0f}%"] + ([note] if note else [])
        h = 34 + 22 * len(lines)
        cv2.rectangle(img, (0, 0), (min(img.shape[1], 430), h), (0, 0, 0), -1)
        cv2.putText(img, big, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.95, WHITE, 2, cv2.LINE_AA)
        for i, text in enumerate(lines):
            cv2.putText(img, text, (10, 54 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                        (GREEN if found else RED) if i == 0 else WHITE, 1, cv2.LINE_AA)
        self._chart(img)
        return img

    def _chart(self, img):
        """The last chart_s seconds of speed along the bottom, 0–12 m/s, with 2 and 10 m/s marked."""
        import cv2

        if len(self.hist) < 2:
            return
        hh, ww = 70, img.shape[1]
        y0 = img.shape[0] - hh
        band = img[y0:]
        band[:] = (band * 0.35).astype(np.uint8)
        y = lambda v: int(y0 + hh - 4 - (hh - 8) * min(max(v, 0.0), 12.0) / 12.0)  # noqa: E731
        for ref in (2.0, 10.0):
            cv2.line(img, (0, y(ref)), (ww, y(ref)), GREY, 1)
            cv2.putText(img, f"{ref:g}", (2, y(ref) - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.35, GREY, 1, cv2.LINE_AA)
        t, v = np.array(self.hist).T
        x = (ww - 1) * (1 - (t[-1] - t) / self.chart_s)
        pts = np.stack([x, [y(s) for s in v]], 1).astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(img, [pts], False, (0, 200, 255), 1, cv2.LINE_AA)


def lan_ip() -> str:
    """This machine's address on the network (no packet is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


PAGE = b"""<!doctype html><html><head><meta charset="utf-8"><title>aircam preview</title>
<style>body{margin:0;background:#111;color:#ccc;font:14px system-ui}img{display:block;margin:auto;max-width:100%;
max-height:96vh}p{text-align:center;margin:4px}</style></head><body><img src="/stream"><p>aircam live view:
yellow = where it searched, green = the ball it found (red = lost), blue cross = rest</p></body></html>"""


class Mjpeg:
    """The latest annotated frame, as an MJPEG stream on http://<pi>:port/ (any browser on the network)."""

    def __init__(self, port: int = 8080):
        self.jpg, self.cond, self.closed = None, threading.Condition(), False
        view = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    last = None
                    try:
                        while not view.closed:
                            with view.cond:
                                view.cond.wait(timeout=1.0)
                                jpg = view.jpg
                            if jpg is None or jpg is last:
                                continue
                            last = jpg
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                             + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(PAGE)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.url = f"http://{lan_ip()}:{self.port}/"
        threading.Thread(target=self.server.serve_forever, name="mjpeg", daemon=True).start()

    def publish(self, img, quality: int = 75):
        import cv2
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if ok:
            with self.cond:
                self.jpg = buf.tobytes()
                self.cond.notify_all()

    def close(self):
        self.closed = True
        with self.cond:
            self.cond.notify_all()
        self.server.shutdown()
        self.server.server_close()
