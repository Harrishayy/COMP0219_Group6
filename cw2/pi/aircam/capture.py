"""Picamera2 at a fixed frame rate and fixed exposure, the way the week 6 lab does it (3.1, 3.2).

Runs only on the Pi (picamera2 comes from apt: python3-picamera2, in a venv made with --system-site-packages).
Choices, from the lab and from picamera2's source (0.3.31 on Bookworm, 0.3.39 on Trixie):
  - ask for the sensor mode by size *and* bit depth (size alone picks the slow 12-bit mode); the main stream is
    the same size, so the whole sensor mode is used and nothing is cropped;
  - pin FrameDurationLimits to one value; lock exposure and gain (picamera2 then sets both to manual) and AWB,
    so the ball's brightness does not move with the airflow;
  - NoiseReductionMode off: on the Pi 5 any other mode turns on temporal denoise, which blends each frame with
    the one before and would smear a moving ball;
  - YUV420, so the Y plane is a free grayscale image. make_array returns (h·3/2, stride) with the row padding
    left in (a 1332-wide image has a 1408-byte stride on the Pi 5), so the Y plane is cut to [:h, :w];
  - no preview window (drawing on the desktop makes the camera drop frames).

HQ camera (IMX477) modes, max fps at 12/10/8 bit: 2028x1080 63/75/92, 1332x990 102/120/148 (lab README,
calculated not measured). Nothing reaches 200, so the estimator upsamples.

A frame's time is the middle of its exposure, in seconds on the clock stream.clock() reads. SensorTimestamp is
the start of the first row's readout, i.e. the end of its exposure (Picamera2 manual: "subtract the image exposure
time"), so the middle is SensorTimestamp − ExposureTime/2. Rows further down are read later (up to ~8 ms at the
bottom); that fixed difference for where the ball is goes in `offset_s`, measured once with an LED test.
"""
from __future__ import annotations

NO_CAMERA = """no camera found: the Pi does not see the camera (check: rpicam-hello --list-cameras).
  1. sudo poweroff, then unplug the power: the Pi only looks for a camera when it boots.
  2. Reseat both ends of the camera cable: straight in, metal contacts facing away from the dark flap,
     the narrow 22-pin end in the Pi's CAM/DISP 0 connector, the wide 15-pin end in the camera.
  3. Power on and run rpicam-hello --list-cameras: it should list imx477. Then aircam setup again."""
CAMERA_BUSY = ("the camera would not open, usually because another program is using it: run `aircam stop` if the "
               "service is running, and close any preview, rpicam or tutorial script that has the camera")


class Camera:
    def __init__(self, size=(1332, 990), bit_depth: int = 10, fps: float = 120.0, exposure_us: int = 3000,
                 gain: float = 4.0, buffer_count: int = 6, offset_s: float = 0.0):
        from picamera2 import Picamera2    # only on the Pi

        try:
            from libcamera import controls as lc
            denoise_off = lc.draft.NoiseReductionModeEnum.Off
        except (ImportError, AttributeError):
            denoise_off = 0
        if not Picamera2.global_camera_info():     # picamera2 0.3.31 (Bookworm) says only "IndexError" here
            raise SystemExit(NO_CAMERA)
        try:
            self.cam = Picamera2()
        except RuntimeError as e:
            raise SystemExit(f"{CAMERA_BUSY} ({e})") from e
        frame_us = int(round(1e6 / fps))
        cfg = self.cam.create_video_configuration(
            main={"size": tuple(size), "format": "YUV420"},
            sensor={"output_size": tuple(size), "bit_depth": bit_depth},
            controls={"FrameDurationLimits": (frame_us, frame_us), "AeEnable": False, "AwbEnable": False,
                      "ExposureTime": int(exposure_us), "AnalogueGain": float(gain), "NoiseReductionMode": denoise_off},
            buffer_count=buffer_count)
        self.cam.configure(cfg)
        self.sensor = self.cam.camera_configuration()["sensor"]
        self.w, self.h = (int(v) for v in self.cam.camera_configuration()["main"]["size"])
        self.offset_s, self.fps = offset_s, fps

    def start(self):
        self.cam.start()

    def stop(self):
        self.cam.stop()

    def frames(self):
        """Yield (t, gray, metadata) for every frame: t the mid-exposure time in seconds, gray the Y plane."""
        while True:
            req = self.cam.capture_request()
            try:
                yuv = req.make_array("main")
                md = req.get_metadata()
            finally:
                req.release()
            t = md["SensorTimestamp"] * 1e-9 - md.get("ExposureTime", 0) * 0.5e-6 + self.offset_s
            yield t, yuv[:self.h, :self.w], md
