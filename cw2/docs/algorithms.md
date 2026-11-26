# Coursework 2: the algorithms, and why these ones

A ping pong ball hangs on a straw between two pillars. The Pi's camera watches it side-on and streams a speed at
200 Hz. This page covers:
- the processing chain;
- the reasons for each choice, with numbers from the simulators (`aircam sim`, `aircam compare`, and the filmed-screen
  test in `pi/tests/test_e2e.py`);
- what stays open until the prototype exists.

Code: `cw2/pi/aircam/`. Requirements: [`brief.md`](brief.md).

```
camera frame (120 fps, Y plane)                                                         every 5 ms
  │ mean shift in a window round the ball (features.py)                                   │
  ▼                                                                                       ▼
d = how far the ball is from rest, mm ──► Kalman (estimator.py) ──predict to tick──► v = k·√tan θ(d) ──► "5.123\n"
                                          gate outliers, upsample to 200 Hz            (models.py)       (stream.py)
```

## 1. Reading: from a frame to how far the ball has moved

**Setup** (`aircam setup`, fan off) runs once per camera position.
- **Finding the ball.** It takes 30 still frames and finds the ball as the biggest round blob brighter than its
  surroundings. It looks at several grey levels, because at a low level a light straw joins the ball into one long
  shape.
- **What it measures:**
  - the ball's grey level and its background's;
  - the ball's radius in pixels. A ping pong ball is 40 mm, so the radius also gives the mm per pixel, and readings
    do not depend on how far away the camera is;
  - the rest position, measured by the tracker itself, so the centroid's offset on a shaded ball is in the rest
    position too.
- **What it assumes about the scene:** only "a round thing brighter than what is around it". `--dark` flips that.

**Every frame** (`features.Ball`):
- **Locating the ball.** A shrinking mean shift from where the ball was last: the soft-thresholded centroid inside
  a disc of 2, then 1.5, then 1.25 radii.
  - The disc keeps the straw, the pillars and glints out.
  - The soft threshold (0 at the midpoint grey, 1 half the contrast above it) gives sub-pixel positions from the
    ball's edge.
- **Cost.** It touches only a window around the ball, sub-sampled so the ball spans ~32 px. That is 0.05 ms a frame
  on a laptop; budget a few times that on the Pi, against 8.3 ms at 120 fps.
- **Lost frames.** If the ball's weight in the disc is not 0.5–1.5 × its weight at rest (occluded, a glint, a
  half-drawn screen frame), a coarse search over the whole frame finds it again. That frame gives no reading.

**The reading** is the chord d, the distance from the rest position in mm.
- It needs neither the hinge's position in the image nor a level camera.
- It stays sensitive at large angles: at 55° the chord still grows at 0.88 of the arc, where the horizontal shift
  alone would grow at 0.57.

**Accuracy.**
- Synthetic frames with a straw and pillars: within ±0.6 px of the true chord across 2–50° (`test_core.py`).
- Frames drawn by the screen simulator and filmed by the camera model: the tracker's chord is one fixed scale times
  the true chord, to within 1 %.

## 2. Map: from mm to m/s

`models.Pendulum`. In steady air, drag and weight balance about the hinge: tan θ = drag/weight = C·v², so
**v = k·√tan θ**, where θ = 2·asin(d / 2·arm).

Two numbers:
- **k (m/s).** A bare 2.7 g ball has k = √(2mg/ρC_dA) ≈ 8.6 m/s, about 53° at 10 m/s. The straw's drag and weight
  lower it: the simulated rig fits ~6.7.
- **arm (mm).** Hinge to ball centre. It also absorbs the straw and the camera's perspective.

Both are fitted from a recorded run (§5). The ball deflects ~3° at 2 m/s with a 15 cm straw. Readings are clamped
at 80°.

## 3. Estimator: upsampling to 200 Hz (the brief asks us to state and justify this)

No HQ camera mode reaches 200 fps: 1332x990 runs at 120 fps at 10 bit and 148 at 8 bit. So every tick needs a value
between frames. The logger stamps each line when it reaches the laptop, so any lag counts against us.

**The Kalman upsampler** (`estimator.Kalman`, the default) is a constant-rate filter on [d, d′]:
- **Prediction.** The state is advanced from the last frame to each 5 ms tick through the exact discretisation of
  the model. This is the "interpolation method" for the report: an optimal causal prediction, not an interpolation
  that waits for the next frame.
- **Process noise** is set in m/s terms (`q_v` = 30 (m/s)²/s³) and converted through the map's slope.
- **Gating.** A reading more than 5σ from the prediction is skipped. If more than 5 are skipped in a row, the
  filter accepts the new level.
- **Limits.** Output is clamped to 0–15 m/s, and the prediction horizon is capped at 0.1 s.

**Simulated graded demo** (`aircam compare`, rig 1, ~1.8 % turbulence at the ball):
- Each estimator is calibrated on a protocol-order-12 run, then scored on a 21 run with fresh turbulence.
- MSE in (m/s)² against a 200 Hz pitot with 0.05 m/s noise, logger pairing, 2–10 m/s. About 0.0025 of each figure is
  the pitot's own noise.

| fps | Hold | Linear | Kalman (q_v 10) | Kalman, matched |
| --- | --- | --- | --- | --- |
| 30 | 0.0371 | 0.0373 | 0.0620 | 0.262 |
| 60 | 0.0359 | 0.0359 | 0.0462 | 0.554 |
| 120 | 0.0355 | 0.0355 | 0.0424 | 0.233 |

With q_v 30 (now the default), the Kalman scores 0.0398. Without turbulence, the Kalman wins (0.0062 against hold's
0.0073), because its prediction removes the half-frame lag.

### Why the numbers are what they are: the ball flutters

A ball on a straw is a very lightly damped pendulum. Rig 1's pluck test gives fn 1.29 Hz and ζ 0.05. Turbulence at the
ball, which the pitot elsewhere in the section does not see, makes it flutter at its own frequency.
- **The floor is physical.** A perfect camera reading the true ball angle, through the best map, with no estimator,
  scores 0.0034 in still turbulence-free air, 0.037 at 1.5 % turbulence and 0.12 at 3 %.
- **Smoothing does not help.** 71 % of that error is at 1–2.5 Hz. Filtering it out costs more lag on the demo's steps
  and pulses than it removes: a 0.3 s low-pass makes 0.037 into 0.094.
- **So:**
  - Below ~1 % turbulence, the Kalman's prediction is worth ~15 %. Above ~1.5 %, sending the latest reading (hold)
    is ~10 % better, because the flutter makes the filter's rate estimate noisy.
  - Which one to use is one setting: `aircam set estimator.name=hold`. Decide it with a screen-simulator or tunnel
    rehearsal once the prototype's flutter is known.
  - **The matched model** (the filter knows the pendulum's fn and ζ) fails here. The real damping grows with speed,
    from aerodynamic drag on the swinging ball, so a fixed still-air ζ over-corrects. Do not use it on this
    instrument.
- **Damping is what helps.** Adding pivot damping to ζ ≈ 0.3–0.5 (pivot 2.5–5 × 10⁻⁴ N·m·s on rig 1) cut the
  simulated MSE from 0.040–0.048 to 0.026 at 1.5 % turbulence. Twice that was too slow (0.041–0.046). See
  `prototype/README.md` for passive ways to add it.

**Through pixels.** The screen simulator's own drawing was filmed by a camera model with:
- a 60 Hz screen and 25 ms display lag;
- a 1.5° tilt, defocus, shot and read noise.

The tracker found the ball in 100 % of frames. Calibrated on one run and scored on the other, it reached **0.032** at
1.5 % turbulence (Kalman, q_v 30), against 0.029 for the pixel-free simulator. The camera path costs little; the ball's motion sets
the score.

## 4. Output: 200 lines a second, never a drop

`stream.py`.
- **Line format.** One line per tick, `5.123\n`, 6–7 bytes. At 115200 baud the budget is ~57 bytes.
- **Map line.** Every 5 s the Pi also sends `# aircam k=… arm=…`. The logger skips lines that start with `#`; the
  screen simulator keeps them, so its runs say which map produced them.
- **The 7-field trap.** A line with exactly 7 fields is read as a GIGA pitot line, so `stream.line` refuses to build
  one.
- **Ticker.** It keeps an absolute 5 ms schedule.
  - After a hold-up, it sends the missed ticks at once (up to 20) rather than skipping them, so the logger's rate
    stays at 200.
  - After a longer stall it skips ahead.
- **Threads.** Capture runs in one thread and output in another, with a 0.5 ms GIL switch interval. A slow frame
  never delays a line.
- **To the laptop:** a 3.3 V USB–UART adapter on the Pi's own serial port. The Pi 5's USB-C port is its power input.
  - Wiring: GPIO14 TXD (pin 8) to the adapter's RX, GND to GND.
  - Setup: `dtparam=uart0=on` (`aircam install`) gives `/dev/ttyAMA0`. Not `/dev/serial0`: on a Pi 5 that is the debug
    connector.
  - No console runs on GPIO14/15 by default.
  - The FTDI 16 ms latency timer batches lines; that is ~8 ms of mean lag, worth ~0.0002.
- **For the screen simulator:** `aircam run --udp <laptop>` sends the same lines as UDP datagrams instead.

## 5. Calibration

The fit uses only what the laptop recorded: the logger's `<run>_aligned.csv` and `_student.csv`, or the screen
simulator's.

1. **Recover the readings.** The Pi streamed v = map_old(d). The map is monotone, so its inverse recovers d at every
   line. Nothing needs copying off the Pi; it only needs the old k and arm. The Pi prints them when `run` starts,
   and the screen simulator saves them from the `# aircam` lines.
2. **Fit.** `fit.fit_pendulum` fits k (least squares) and arm (a 0.6–1.6 × search) on ground truth in 2–10 m/s only,
   while searching the time shift between the two (camera latency against the pitot's lag, ±0.6 s).
   `aircam fit` prints:
   - the logger's MSE before and after;
   - the line to type on the Pi: `aircam set k=… arm=…`.
3. **Score on the demo, not on holds.** Rehearse the protocol in both orders. Part 1's lesson: zero and gain dominate
   the MSE. Here the zero is the rest position, measured by `aircam setup`, so rerun setup whenever the camera or the
   rig is touched.

## 6. Simulators

- **`aircam sim` / `compare`.** No pixels: it simulates the pendulum's physics (`rig.py`), camera sampling with
  centroid noise, dropped frames and glints, and the estimator, calibration and scoring.
- **`aircam screen`.** The laptop draws the rig and animates it with the same physics, and the Pi's real camera films
  it. It has:
  - DEMO mode (Part 1's protocol) and MANUAL mode (speed buttons);
  - a side panel with the truth against the Pi.

  It saves runs in the logger's format.
- **Avoiding a sensor tuned to the simulator.**
  - The rig is hidden, drawn from a seed: straw length, masses, drag coefficients with a Reynolds-number trend, pivot
    friction and turbulence.
  - The physics is the full nonlinear pendulum with drag on the ball's relative velocity, straw drag, added mass and
    buoyancy.
  - The picture is shaded on a textured background.
  - The Pi calibrates against recorded truth, as in the tunnel.
  - Ground truth is the mean flow: the turbulence shakes the ball but is not in the "pitot".
- **What the screen adds that the tunnel does not:**
  - display lag, tens of ms (`--display-lag` takes it off once measured);
  - 60 Hz refresh, so the camera sees each drawn frame twice and catches some mid-update.

## 7. Tests to run on the prototype, in order

| Test | How | Gives |
| --- | --- | --- |
| Setup | `aircam setup`, fan off; check `~/aircam-setup.png`; `aircam preview` on the desktop | Rest position, mm/px, contrast, still-air jitter |
| Pluck | `aircam pluck --write`: pull the ball ~20° and let go | fn, ζ: is the ball too lightly damped? |
| Latency | An LED in view, toggled from a Pi GPIO at known times; compare with the frame times | `camera.offset_s`. `run` also prints the capture latency |
| Frame budget | `aircam run` prints fps and how often the ball was seen | ROI size, exposure |
| Calibration | §5, with the logger | k, arm |
| Demo rehearsal | The protocol in `cw1/docs/demo-protocol.md`, both orders; then `estimator.name=hold` against `kalman` | The score, and the estimator |

## 8. Assumptions the report has to state (the brief asks for them)

- **Lighting.** Our own constant LED light on the camera side, not PWM-dimmed (PWM bands the image on a rolling
  shutter), a matte dark background, and a white ball.
  - Mains lighting flickers at 100 Hz.
  - Exposure (3 ms), gain and white balance are locked, and temporal denoise is off: on the Pi 5 it blends each
    frame with the one before.
  - Ask whether a light counts as part of the "passive instrument". We think not: the light is on the camera side,
    and the ball stays unpowered.
- **Camera.**
  - It is rigid on the mounting plate. Any shift moves the zero, so rerun `aircam setup` after touching it.
  - The ball's 40 mm diameter sets the scale, so the camera's distance does not need measuring.
- **Time.**
  - SensorTimestamp is the end of the first row's exposure, so mid-exposure is SensorTimestamp − ExposureTime/2.
  - The ball's rows are read a few ms later; that constant goes in `offset_s`, from the latency test.
- **Air density.** It is not measured on the Pi. Drag reads ∝ ρv², so a 2 % change in ρ shifts the speed by 1 %.
  Recalibrate on the day.
- **Turbulence.** The ball reads the air where it is, the pitot reads the air where it is, and the two differ by
  the local turbulence. That difference is a floor on the MSE (§3).
