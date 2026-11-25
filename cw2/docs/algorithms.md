# Coursework 2: the algorithms, and why these ones

A camera on the Raspberry Pi 5 watches a passive instrument in the airflow and the Pi streams a speed at 200 Hz.
This page is the processing chain, the reasons for each choice (numbers from the simulator, `aircam compare`),
and what is still open until the prototype exists. Code: `cw2/pi/aircam/`. Requirements: [`brief.md`](brief.md).

```
camera frame (≤148 fps)                                                       every 5 ms
  │ ROI crop, centroid of the marker (features.py)                              │
  ▼                                                                             ▼
reading y at mid-exposure time ──► estimator (estimator.py) ──predict to tick──► speed = map(u) ──► "5.123\n"
                                    Kalman on [instrument, u, u′]                 (models.py)      (stream.py)
                                    gate outliers, upsample to 200 Hz
```

## 1. Reading: from a frame to one number

The camera measures the instrument's position; `features.py` turns each frame into a reading `y`. Only a
region of interest is processed, with NumPy whole-array operations: the week 6 lab measured ~3 ms for a colour
mask on a third of the frame, 8–9 ms on the whole frame, against an 8.3 ms budget at 120 fps.

- **Centroid.** Intensity-weighted, above a threshold, so it is sub-pixel (the test puts it within 0.08 px on a
  3 px dot). `None` when the marker is lost; the estimator just skips that frame.
- **Pendulum** (a body hanging or hinged in the flow): the reading is tan θ of the deflection about the pivot. For
  a sphere on a light rod, drag ∝ v² and tan θ = drag/weight, so tan θ ∝ v² exactly. The map then has p = 2.
- **Rotor** (cups, Savonius, propeller): the reading is revolutions per second, from the slope of the marker's
  unwrapped angle over a 0.1 s window. That window adds ~50 ms of lag. It is unambiguous only below fps/2 rev/s.
- **Fiducials.** Two fixed dots on the mount. Their positions give the camera's shift, roll and scale every
  frame, and the pivot and rest angle are moved with them. A knocked camera then does not move the zero. In
  Part 1, zero and gain dominated the MSE.

A camera can resolve far more than we need. With a 150 px arm and a centroid good to 0.05 px, σθ ≈ 0.02°, which
at 2 m/s is ≈ 0.004 m/s per frame on a pendulum sized to reach 65° at 10 m/s. The real noise will be turbulence
moving the instrument, not the camera.

## 2. Map: from the reading to m/s

`models.py`. **PowerLaw** `v = v0 + k·(y − y0)^(1/p)`: p = 2 for drag on a deflecting body, p = 1 with a
start-up speed v0 for a rotor. **Table** is a monotone piecewise-linear correction for what the physics misses.
Fitting (§5) uses tunnel runs.

With p = 2 the slope dv/dy grows as 1/√y. Reading noise therefore costs most near 2 m/s, the bottom of the scored
band. Size the instrument so it is well off zero at 2 m/s and not saturated at 10 m/s.

## 3. Estimator: upsampling to 200 Hz (the brief asks us to state and justify this)

No HQ camera mode reaches 200 fps (1332x990 is 120 fps at 10 bit, 148 at 8 bit), so every tick needs a value
between frames. The logger stamps each line when it reaches the laptop, so any lag counts against us.

| Method | What it does | Cost |
| --- | --- | --- |
| Hold | last reading, through the map | ½ frame lag on average; glints go straight out |
| Linear | interpolate the last two readings, one frame in the past | a whole frame of extra lag |
| **Kalman** (chosen) | filter on [u, u′] (constant-rate model), predicted forward to each tick | needs σy and one tuning number |
| Kalman, matched | the same, plus the instrument's own dynamics (first or second order) | needs fn and ζ from a pluck test |

**The Kalman upsampler** (`estimator.Kalman`):

- **Prediction.** The state is advanced from the last frame to each tick through the exact discretisation of
  the model (Van Loan, cached per 10 µs step). This is the "interpolation method" the report must state: an
  optimal causal prediction under a constant-rate model, not an interpolation that waits for the next frame.
- **Process noise** is set in m/s terms (`q_v`, (m/s)²/s³) and converted to reading units through the map's slope
  at the current speed. One value then works across 2–10 m/s even though the map is nonlinear.
- **Gating.** A reading more than 5σ from the prediction (a glint, an occluded marker) is skipped. If more than
  5 in a row are skipped, the air really moved: the covariance is inflated and the reading accepted.
- **Matched model.** It estimates u, the reading the instrument would settle to, from where it is now. That
  takes out a pendulum's ringing and lag.
- **Output** is clamped to 0–15 m/s. The prediction horizon is capped at 0.1 s, so a camera stall holds the
  value rather than running off along a slope.

**Simulated graded demo** (`aircam compare`). Setup:

- Protocols 1 and 2 at 9 m/s full.
- Pendulum with fn 3 Hz, ζ 0.15. σy = 0.002, 1 % dropped frames, 0.2 % glints.
- 12 ms camera latency.
- Pitot at 200 Hz with 0.05 m/s noise.
- Scored as the logger does: nearest line within 150 ms, ground truth in 2–10 m/s.

| fps | Hold | Linear | Kalman | Kalman, matched |
| --- | --- | --- | --- | --- |
| 30 | 0.0222 | 0.0213 | 0.0046 | 0.0047 |
| 60 | 0.0198 | 0.0197 | 0.0040 | 0.0042 |
| 120 | 0.0238 | 0.0238 | **0.0036** | 0.0036 |
| 148 | 0.0363 | 0.0363 | 0.0035 | 0.0036 |

MSE in (m/s)², logger pairing. About 0.0025 of each Kalman figure is the pitot's own noise; against the true
speed, Kalman at 120 fps is 0.0011.

What the simulator says:

- **Kalman, by 5–10×.** Most of the gain is gating: a hold passes every glint out at full size.
- **Frame rate barely matters with the Kalman filter.** 60 → 120 fps is 0.0015 → 0.0011 against the true speed.
  Run 120 fps (10-bit 1332x990) and spend any spare budget on a larger ROI or a cleaner image, not on 148 fps.
- **The matched model matters only for a soft, lightly damped instrument.** With fn 1.2 Hz and ζ 0.08 it cuts
  the MSE by 30 % (0.0117 → 0.0082 at 120 fps). On a stiff one it ties. It needs its own tuning: `q_v` 2
  instead of 10, because it differentiates the reading.
- **Latency.** Each 10 ms of camera-to-line latency costs ~0.0003 up to 60 ms, and 0.02 at 200 ms.
- **No lead.** Predicting ahead to cancel latency made it worse (`--lead 0.05`: 0.0047 → 0.0065), because the
  extrapolation adds noise. `lead` stays 0.

These are guesses until the prototype is measured: fn and ζ, σy, and the latency all come from the tests in §6.
Rerun `aircam compare` with the measured values before choosing.

## 4. Output: 200 lines a second, never a drop

`stream.py`.

- **Line format.** One line per tick: `5.123\n`, 6–7 bytes. At 115200 baud the budget is ~57 bytes.
- **What the logger reads** (app.py:441–469): it takes the last comma field and skips lines starting with `#`.
- **The 7-field trap.** A line with exactly 7 fields is read as a GIGA pitot line: the speed comes from field 4
  and the logger writes `INFO` back. `stream.line` refuses to build one.
- **Ticker.** An absolute 5 ms schedule on CLOCK_BOOTTIME, the clock libcamera stamps frames with, so ticks and
  frames share one clock. After a hold-up it sends the missed ticks at once (up to 20) rather than skipping them,
  so the logger's 5 s rate average stays at 200. After a longer stall it skips ahead.
- **Threads.** Capture runs in one thread, output in another, and the GIL switch interval is 0.5 ms. A slow
  frame therefore never delays a line.

**Open decision: how the line reaches the laptop.** The course material says only "a virtual serial port
over USB". The Pi 5's USB-C port is its power input (the lab insists on the 27 W supply), so USB gadget serial
is out. Two options:

| | A. USB–UART adapter on the Pi's UART (recommended) | B. The Part 1 Nucleo as a bridge |
| --- | --- | --- |
| Wiring | Pi GPIO14 TXD → adapter RX, GND (3.3 V logic); `dtparam=uart0=on`, `/dev/ttyAMA0` | Pi TXD → Nucleo USART RX; the Nucleo re-sends on its ST-LINK port |
| Laptop sees | `/dev/tty.usbserial-…` / COMx | `/dev/tty.usbmodem…`, as in Part 1 |
| For | All processing visibly on the Pi; nothing else to build | Hardware we have; its TIM5 tick could re-clock to exactly 200 Hz |
| Against | Needs an adapter (Innovation Lab?); FTDI's 16 ms latency timer batches lines (~8 ms mean lag, ≈0.0002) | Firmware to write; ask the TA whether a second processor is allowed |

The Pi code is the same either way: it writes 200 Hz lines to a serial device (`--port`).

## 5. Calibration

The fit uses tunnel runs, the way Part 1 learned to do it (cw1/docs/calibration-2026-11-04.md).

1. **Record both sides.** Start `aircam run --record <dir>` on the Pi and the Wind Tunnel Logger on the laptop,
   within a few seconds of each other.
2. **`aircam fit <session> <run>_aligned.csv --write <config>`.** It finds the clock offset by correlating the
   speed both sides saw (Pearson per lag: plain correlation drifts on the demo's plateaus). It then fits
   k and y0 on 2–10 m/s only, while searching the pitot's lag. Pass `--y0` with a still-air reading to pin the
   zero instead of fitting it. Part 1's lesson: a fitted zero that looks better on holds can lose on the
   demo-weighted score.
3. **Score on the demo, not on holds.** Rehearse the protocol and score with the logger's pairing (`demo.score`).

The calibration file is the JSON config (`config.example.json` is the shape). `aircam cal --config <file>` shows
it on demand, which is one of the demo deliverables.

## 6. Tests to run on the prototype, in order

| Test | How | Gives |
| --- | --- | --- |
| Still air | 30 s recording, fan off | σy (estimator `sigma_y`), y0 |
| Pluck | deflect it in still air, let go, record 10 s; `aircam pluck <session>` | fn, ζ: matched model or not |
| Latency | an LED in view, toggled from a Pi GPIO at known times; compare with the frame times | `offset_s`, true camera latency |
| Frame budget | `aircam run` reports frames/5 s and frames without a reading | ROI size, threshold, fps |
| Tunnel calibration | §5 | the map |
| Demo rehearsal | the protocol in cw1/docs/demo-protocol.md, both orders | the score |

## 7. Assumptions the report has to state (the brief asks for them)

- **Lighting.** Our own constant LED light on the camera side, not PWM-dimmed (PWM bands the image on a rolling
  shutter), a dark background and a bright or retro-reflective marker.
  - Mains lighting flickers at 100 Hz. Frames at 100 fps with a 10 ms exposure would average one whole flicker
    period, an option if the room lights show.
  - Exposure, gain and white balance stay locked.
  - Ask whether a light counts as part of the "passive instrument". We think not: the light is on the camera
    side and the instrument itself stays unpowered.
- **Camera.** It is rigid on the mounting plate, with the fiducials in view. Without them, any shift moves the
  zero.
- **Time.** SensorTimestamp + half the exposure is the mid-exposure time on CLOCK_BOOTTIME, with a constant
  offset from the latency test. Rolling-shutter row timing over a small ROI is ignored: under 1 ms.
- **Air density** is not measured on the Pi. Drag instruments read ∝ 1/√ρ, so a 2 % change in ρ shifts the
  speed by 1 %. Recalibrate on the day, or add a temperature sensor.
