"""Picamera2 at a fixed frame rate and fixed exposure, the way the week 6 lab does it (3.1, 3.2).

Runs only on the Pi (picamera2 comes from apt: python3-picamera2, in a venv made with --system-site-packages).
Choices, all from the lab: ask for the sensor mode by size *and* bit depth (size alone picks the slow 12-bit
mode); pin FrameDurationLimits to one value; lock exposure, gain and white balance so the marker's brightness
does not move with the airflow; no preview window (drawing on the desktop makes the camera drop frames).
YUV420 so the Y plane is a free grayscale image.

HQ camera (IMX477) modes, max fps at 12/10/8 bit: 2028x1080 63/75/92, 1332x990 102/120/148 (lab README,
calculated not measured). Nothing reaches 200, so the estimator upsamples.

A frame's time is SensorTimestamp + half the exposure, in seconds on CLOCK_BOOTTIME (stream.clock()). Whether
SensorTimestamp marks the first row's exposure start or its readout is not stated in the lab material; the
fixed difference goes in `offset_s`, measured once with an LED test (docs/algorithms.md §6).
"""
from __future__ import annotations


class Camera:
    def __init__(self, size=(1332, 990), bit_depth: int = 10, fps: float = 120.0, exposure_us: int = 4000,
                 gain: float = 4.0, buffer_count: int = 6, offset_s: float = 0.0):
        from picamera2 import Picamera2    # only on the Pi

        self.cam = Picamera2()
        frame_us = int(round(1e6 / fps))
        cfg = self.cam.create_video_configuration(
            main={"size": tuple(size), "format": "YUV420"},
            sensor={"output_size": tuple(size), "bit_depth": bit_depth},
            controls={"FrameDurationLimits": (frame_us, frame_us), "AeEnable": False, "AwbEnable": False,
                      "ExposureTime": int(exposure_us), "AnalogueGain": float(gain)},
            buffer_count=buffer_count)
        self.cam.configure(cfg)
        self.sensor = self.cam.camera_configuration()["sensor"]
        self.rows, self.offset_s, self.fps = int(size[1]), offset_s, fps

    def start(self):
        self.cam.start()

    def stop(self):
        self.cam.stop()

    def frames(self):
        """Yield (t, gray, metadata) for every frame: t the mid-exposure time in seconds."""
        while True:
            req = self.cam.capture_request()
            try:
                yuv = req.make_array("main")
                md = req.get_metadata()
            finally:
                req.release()
            t = md["SensorTimestamp"] * 1e-9 + md.get("ExposureTime", 0) * 0.5e-6 + self.offset_s
            yield t, yuv[:self.rows], md

    def snapshot(self, path: str):
        """Save one grayscale frame, for picking the ROI, pivot and fiducials."""
        import numpy as np
        t, gray, md = next(self.frames())
        np.save(path, gray)
        return path
