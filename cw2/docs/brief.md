# Coursework 2 brief, as requirements

From [`project-brief-part2.pdf`](project-brief-part2.pdf) (COMP0219 Stage 2). The PDF is dated 9 Dec 2025, so
confirm this year's dates with the module team.

| Requirement | Detail | Where it is handled |
| --- | --- | --- |
| Contactless | The camera (or a ToF/ultrasonic sensor) senses an instrument in the air indirectly; the instrument is passive (no power) | the prototype; `pi/aircam/features.py` |
| Range | 2–10 m/s, including steps and ramps; no direction | `pi/aircam/demo.py` profile, scored on 2–10 |
| Output | A float per line at 200 Hz, 115200 baud, to a virtual COM port over USB | `pi/aircam/stream.py`; transport is an open decision ([algorithms §4](algorithms.md#4-output-200-lines-a-second-never-a-drop)) |
| Autonomous | Software runs on the Pi; the remote interface only starts and stops it | `pi/deploy/aircam.service` (systemctl start/stop from a Pi Connect shell) |
| Below 200 Hz | Upsample to the ground truth's rate and state the interpolation method and why | [algorithms §3](algorithms.md#3-estimator-upsampling-to-200-hz-the-brief-asks-us-to-state-and-justify-this) |
| Envelope | Fits in 30 × 30 × 30 cm and mounts on the 30 cm square plate | the prototype |
| Safety | Guards on rotating parts, magnets epoxied or heat-shrunk, no sharp edges | the prototype |
| Hardware | Raspberry Pi 5 (8 GB), HQ camera with a 6 mm lens; also a Global Shutter camera with a 16 mm lens, M12 HQ cameras, and optionally the Part 1 Nucleo, HC-SR04 and VL53L1/L4 ToF boards | |
| Ground truth | Dwyer 616KD-LR-B42-BD1 + pitot, sampled at 200 Hz | the logger (see below) |

## Deliverables

- **Live demo (11 Dec):** the device on the plate, a real-time 200 Hz stream, and the calibration file on demand
  (`aircam cal`).
- **Code:** the Pi code (with the HQ camera), or a buildable CubeIDE/PlatformIO project; no PC-side processing.
- **Report (16 Dec, 16:00):** 6 pages, IEEE style. It covers design rationale, modelling, calibration method,
  error budget, dynamic results (MSE, bias, σ), limitations and originality. It must also state:
  - the assumptions and external factors (lighting, camera calibration and alignment)
  - the upsampling method, if the rate is under 200 Hz
  - other methods tried, and what we learned from them

## Assessment

Practical 70 % (MSE rank, and 200 Hz at 115200 without drops) plus report 30 %, or 50/50, whichever is higher.
Originality of the transducer or the embedded processing is rewarded.

## To clarify with the module team

- The brief has leftovers from Part 1:
  - the title says "CONTACT-BASED AIRSPEED SENSOR" and the footers say "CW – Part 1"
  - the tasks say processing "needs to run entirely on the Nucleo"

  We read the Pi as the processor, per the objectives.
- **The output path to the laptop.** May the Pi drive a USB–UART adapter? May the Part 1 Nucleo bridge it?
- **Lighting.** Does a light on the camera side count against "passive"?
- **Ground truth.** Logger v1.0.1 (`cw1/tools/wind-tunnel-logger`) reads only the VDAS box and the GIGA, not a
  Dwyer. Ask whether a new version is coming.
- **The demo protocol.** Is it the same as Part 1's (`cw1/docs/demo-protocol.md`)? The simulator assumes so.
