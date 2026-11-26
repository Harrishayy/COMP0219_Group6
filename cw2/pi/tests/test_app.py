"""The runner and setup end to end, with a fake camera in place of Picamera2."""
import csv
from pathlib import Path
import math
import time
import socket

import numpy as np
import pytest

from aircam import app, models, stream
from test_core import HINGE, scene

CFG = app.load(__import__("pathlib").Path(__file__).resolve().parents[1] / "config.example.json")
ARM_PX = 240.0


class FakeCamera:
    """120 fps of the synthetic scene with the ball where `theta_deg` puts it, stamped on the stream clock."""
    theta_deg = 0.0

    def __init__(self, fps=120.0, **_):
        self.fps = fps
        th = math.radians(self.theta_deg)
        self.img = scene(HINGE[0] + ARM_PX * math.sin(th), HINGE[1] + ARM_PX * math.cos(th))

    def start(self):
        self.t0 = stream.clock()

    def stop(self):
        pass

    def frames(self):
        i = 0
        while True:
            t = self.t0 + i / self.fps
            time.sleep(max(0.0, t + 0.010 - stream.clock()))     # 10 ms "readout"
            yield t, self.img, {}
            i += 1


def test_setup_then_run_streams_200_hz_and_reads_the_air(tmp_path, monkeypatch, capfdbinary):
    monkeypatch.setattr("aircam.capture.Camera", FakeCamera)
    cfg = dict(CFG)
    with pytest.raises(SystemExit):
        app.run(cfg, port="-", seconds=0.1)                       # no ball yet: setup first
    FakeCamera.theta_deg = 0.0
    app.setup(cfg, tmp_path / "aircam.json")
    assert (tmp_path / "aircam.json").exists() and cfg["ball"]["radius_px"] == pytest.approx(19, rel=0.05)

    # the air at 6 m/s, for a map whose arm is the scene's arm in the ball's mm
    mm_per_px = 40.0 / (2 * cfg["ball"]["radius_px"])
    model = models.Pendulum(k=8.6, arm_mm=ARM_PX * mm_per_px)
    cfg["map"] = {"type": "pendulum", "k": 8.6, "arm_mm": model.arm_mm}
    FakeCamera.theta_deg = math.degrees(float(model.theta(model.reading(6.0))))
    capfdbinary.readouterr()
    app.run(cfg, port="-", record=str(tmp_path), seconds=2.0)
    out = capfdbinary.readouterr().out.decode().splitlines()
    lines = [x for x in out if not x.startswith("#")]
    assert out[0].startswith("# aircam k=8.6 arm=")                # the map, for the screen simulator's fit
    session = next(p for p in tmp_path.iterdir() if p.is_dir())
    ticks = list(csv.DictReader(open(session / "ticks.csv")))
    frames = list(csv.DictReader(open(session / "frames.csv")))
    assert 380 <= len(ticks) <= 410 and len(lines) == len(ticks)  # 200 lines a second
    assert 200 <= len(frames) <= 250
    t = np.array([float(r["t"]) for r in ticks])
    assert np.allclose(np.diff(t), 0.005)                         # on the 5 ms schedule
    assert abs(float(lines[-1]) - 6.0) < 0.1                       # and reading the air


def test_capture_cuts_the_row_padding_and_stamps_mid_exposure(monkeypatch):
    """Picamera2's YUV420 make_array is (h·3/2, stride) with the padding left in (1408 for 1332 on a Pi 5), and
    SensorTimestamp is the end of the first row's exposure: a stub with those shapes stands in for the camera."""
    import sys
    import types

    class Request:
        def make_array(self, name):
            return np.zeros((990 * 3 // 2, 1408), np.uint8)

        def get_metadata(self):
            return {"SensorTimestamp": 5_000_000_000, "ExposureTime": 3000}

        def release(self):
            pass

    class Picamera2:
        cameras = [{"Model": "imx477", "Num": 0}]

        @staticmethod
        def global_camera_info():
            return Picamera2.cameras

        def create_video_configuration(self, **kw):
            self.kw = kw
            return kw

        def configure(self, cfg):
            pass

        def camera_configuration(self):
            return {"sensor": self.kw["sensor"], "main": {"size": self.kw["main"]["size"], "stride": 1408}}

        def capture_request(self):
            return Request()

    monkeypatch.setitem(sys.modules, "picamera2", types.SimpleNamespace(Picamera2=Picamera2))
    from aircam.capture import Camera
    cam = Camera(size=(1332, 990), exposure_us=3000)
    assert cam.cam.kw["controls"]["NoiseReductionMode"] == 0       # temporal denoise off (no libcamera here)
    t, gray, md = next(cam.frames())
    assert gray.shape == (990, 1332)
    assert t == pytest.approx(5.0 - 0.0015)
    Picamera2.cameras = []                        # no camera plugged in: say so, not "list index out of range"
    with pytest.raises(SystemExit, match="no camera found"):
        Camera()


def test_check_reports_how_well_the_ball_is_tracked(tmp_path, monkeypatch):
    monkeypatch.setattr("aircam.capture.Camera", FakeCamera)
    cfg = dict(CFG)
    FakeCamera.theta_deg = 0.0
    app.setup(cfg, tmp_path / "aircam.json")
    FakeCamera.theta_deg = 20.0
    r = app.check(cfg, seconds=1.0, image=tmp_path / "check.png")
    assert r["found"] == 1.0 and 100 < r["fps"] < 140 and r["noise_mm"] < 0.01
    mm_per_px = 40.0 / (2 * cfg["ball"]["radius_px"])
    assert r["d_mm"] == pytest.approx(2 * ARM_PX * math.sin(math.radians(10)) * mm_per_px, rel=0.01)
    assert "tracking looks healthy" in app.report_text(r) and Path(r["image"]).exists()


def test_report_flags_lost_frames_and_jumps():
    t = np.arange(240) / 120
    d = 30 + 0.05 * np.random.default_rng(0).standard_normal(240)
    d[50:60] = np.nan                                   # ten frames without the ball
    d[100] += 8.0                                       # a glint
    r = app.tracking_report(t, d, np.full(240, 2e-4), models.Pendulum())
    assert r["found"] == pytest.approx(230 / 240) and r["longest_gap"] == 10 and r["jumps"] == 2
    assert r["noise_mm"] == pytest.approx(0.05, rel=0.3) and r["noise_mps_at_2"] > r["noise_mps_at_10"] * 0.5
    text = app.report_text(r)
    assert "lost in some frames" in text and "sudden jumps" in text


def test_live_view_draws_the_tracking_and_serves_it_to_a_browser(tmp_path, monkeypatch):
    """preview --web: the annotated feed reaches a browser as MJPEG, and --udp feeds the simulator."""
    cv2 = pytest.importorskip("cv2")
    import socket
    import threading
    import urllib.request
    monkeypatch.setattr("aircam.capture.Camera", FakeCamera)
    cfg = dict(CFG)
    FakeCamera.theta_deg = 0.0
    app.setup(cfg, tmp_path / "aircam.json")
    FakeCamera.theta_deg = 25.0
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(2.0)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    th = threading.Thread(target=app.preview, args=(cfg,),
                          kwargs=dict(web=True, port=port, udp=f"127.0.0.1:{rx.getsockname()[1]}", seconds=2.5))
    th.start()
    time.sleep(1.0)
    page = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2).read()
    assert b"/stream" in page
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/stream", timeout=2) as resp:
        chunk = resp.read(200_000)
    start = chunk.index(b"\xff\xd8")
    jpg = np.frombuffer(chunk[start:chunk.index(b"\xff\xd9", start) + 2], np.uint8)
    img = cv2.imdecode(jpg, cv2.IMREAD_COLOR)
    assert img.shape[:2] == (240, 320)                               # half size
    assert rx.recv(100).startswith(b"# aircam") and float(rx.recv(100)) > 2.0   # the simulator gets the speed
    th.join(timeout=5)
    assert not th.is_alive()


def test_overlay_marks_a_lost_ball_in_red():
    cv2 = pytest.importorskip("cv2")
    from aircam import features, view
    img = scene(*__import__("test_core").at(0))
    ball, _, _ = features.setup([img])
    ov = view.Overlay(ball, models.Pendulum())
    ov.add(0.0, ball.pos, None, 0.0)
    out = ov.draw(np.full_like(img, 30), fps=120.0, ms=0.1)
    x, y = (int(c * 0.5) for c in ball.pos)
    r = int(ball.radius * 0.5)
    ring = out[y - r - 2:y + r + 3, x - r - 2:x + r + 3].reshape(-1, 3)
    assert np.any((ring[:, 2] > 200) & (ring[:, 1] < 80))           # a red circle where the ball was
