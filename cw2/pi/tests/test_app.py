"""The runner and setup end to end, with a fake camera in place of Picamera2."""
import csv
import math
import time

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
