"""The two-thread runner end to end, with a fake camera in place of Picamera2."""
import csv
import math
import time

import numpy as np

from aircam import app, stream

CFG = app.load(__import__("pathlib").Path(__file__).resolve().parents[1] / "config.example.json")


class FakeCamera:
    """120 fps of a white ball on a pendulum deflected for 6 m/s, stamped on the stream clock."""

    def __init__(self, fps=120.0, **_):
        self.fps = fps
        k = CFG["model"]["k"]
        theta = math.atan((6.0 / k) ** 2)
        px, py = CFG["feature"]["pivot"]
        bx, by = px + 330 * math.sin(theta), py + 330 * math.cos(theta)
        yy, xx = np.mgrid[:990, :1332]
        self.img = (10 + 240 * np.exp(-((xx - bx) ** 2 + (yy - by) ** 2) / 18.0)).astype(np.uint8)

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


def test_runner_streams_200_hz_and_records(tmp_path, monkeypatch, capfdbinary):
    monkeypatch.setattr("aircam.capture.Camera", FakeCamera)
    app.run(CFG, port="-", record=str(tmp_path), seconds=2.0)
    out = capfdbinary.readouterr().out.decode().split()
    session = next(tmp_path.iterdir())
    ticks = list(csv.DictReader(open(session / "ticks.csv")))
    frames = list(csv.DictReader(open(session / "frames.csv")))
    assert 380 <= len(ticks) <= 410 and len(out) == len(ticks)          # 200 lines a second
    assert 200 <= len(frames) <= 250
    t = np.array([float(r["t"]) for r in ticks])
    assert np.allclose(np.diff(t), 0.005)                                 # on the 5 ms schedule
    assert abs(float(out[-1]) - 6.0) < 0.05                               # and reading the air
