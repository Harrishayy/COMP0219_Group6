# Calibration from the 4 Nov 2026 tunnel runs

The white tunnel, with the VDAS box as ground truth. This file covers what went wrong in those runs, how the new
default calibration was fitted, how it was checked for overfitting, the output filter (Butterworth 0.75 Hz), the
final scores, what changed in the code, and how to keep the board on this calibration.

## The data

| What | Files | Has |
| --- | --- | --- |
| Logger run 15:17 | `data/tunnel/run_20261104_151708_{aligned,vdas,student}.csv` | board speed, VDAS speed and pressure |
| Logger run 15:20 | `data/tunnel/run_20261104_152041_*` | the same, but no recording with force covers it |
| Logger run 15:25 | `data/tunnel/run_20261104_152520_*` | board speed, VDAS speed and pressure |
| App recording 15:17 | `calibration/runs/run_20261104_151732_live_drag_plate.{csv,json}` | force, ρ, board speed at 200 Hz |
| App recording 15:25 | `calibration/runs/run_20261104_152524_live_drag_plate.{csv,json}` | force, ρ, board speed at 200 Hz |

The logger runs have speeds but no force. The app recordings have the force, but no ground truth, because the
dashboard could not open the VDAS box while the logger held it. Each pair is joined by the board's speed, which
both programs saved from the same stream. Aligned that way, the two copies of the board speed agree to within
0.12–0.13 m/s rms. The join is automatic now (`calib/recal.py`).

## What went wrong

### 4 g read as 2.8 m/s: the zero drifted

Drag grows with the square of speed, `F = ½ρv²·Cd·A`. For our body (Ø86 mm open hemisphere, Cd·A ≈ 82.5 cm²),
theory gives about 2 g at 2 m/s, **4 g at 2.8 m/s**, 13 g at 5 m/s and 50 g at 10 m/s. So 4 g really is 2.8 m/s.
The catch is that near zero a few grams of error become a few m/s.

The board's fan-off force, measured where the tunnel read zero, was:

| When | Fan-off force |
| --- | --- |
| End of the 15:16 run | −1.8 g, recovering to −0.6 g |
| Start of the 15:17 run | **+0.41 g** |
| End of the 15:25 run (fan stopped, VDAS at 0) | **+3.84 g**, creeping up to 4.3 g |

Nobody re-tared between runs, so in the 15:25 run the zero sat about 4 g high. Every fast drop to still air
bottomed out at about 4 g, and the old setting turned that into 2.8 m/s.

**The fix is procedure: TARE with the fan off just before every run.** The calibration takes its zero from that
TARE.

The board's "grams" are not real grams yet. `lc_n_per_count` is still the 1e-5 placeholder: at 10 m/s the board
showed about 80 g where theory says 50 g, so its grams are about 1.6× too big. The fitted gain absorbs this. If the
cell is ever scaled with masses, keep `sqrt_k × lc_n_per_count` the same.

### Sudden changes: the board is early, not late

- The force is clean. There are no read glitches and a fresh sample every 5 ms (NAU7802).
- 92 % of the force noise is one mechanical resonance at **15.7 Hz**, the plate swinging on the bar cell. It is
  about ±14 % of the mean force at every speed.
- **The VDAS lags the board by 0.40 s (15:17 run) and 0.48 s (15:25 run).** The board estimates speed from its own
  force only; the VDAS never reaches it. The VDAS is late because of its pressure tubing and because it reports
  about five times a second. The logger compares readings at the same timestamp, so on every speed change our
  early reading counts as error against the late reference.
- Spin-up overshoots are real: the tunnel overshoots about 10 % and the VDAS sees it too. The board only looked
  spiky because it read 30 % high.

## The fit

### Model and data

The firmware's own model, `v = √(k·(F − F₀)/ρ)`, with two numbers fitted: the gain k and the zero F₀. F₀ is
measured from each run's fan-off TARE (the force above), not fitted freely.

The steady holds, VDAS against board force above that run's fan-off zero:

| Run | Hold (s) | VDAS (m/s) | q (Pa) | F − zero (g) | F/q (g/Pa) |
| --- | --- | --- | --- | --- | --- |
| 15:17 | 19–41 | 10.07 | 60.8 | 79.25 | 1.30 |
| 15:17 | 46–62 | 6.96 | 29.1 | 42.98 | 1.48 |
| 15:17 | 66–102 | 6.99 | 29.3 | 42.61 | 1.45 |
| 15:17 | 128–154 | 2.55 | 3.9 | 7.60 | 1.95 |
| 15:25 | 19–23 | 10.01 | 60.0 | 80.58 | 1.34 |
| 15:25 | 33–44 | 6.57 | 25.9 | 37.32 | 1.44 |
| 15:25 | 54–59 | 2.87 | 5.0 | 9.29 | 1.88 |
| 15:25 | 76–82 | 10.05 | 60.6 | 81.42 | 1.34 |

### Checks against overfitting

1. **The same gain from each run on its own.** Each run alone gives k = 145.
2. **Zero offset: TARE alone (x0 = 0) wins.** A zero offset above the tare (+2.8 g) fits the 2.5–2.9 m/s holds
   best, and each run alone gives it. But once the VDAS lag is taken out, the force below 2 m/s is only 1–3 g:

   | VDAS q (Pa) | VDAS v (m/s) | Force (g) | Zero offset +2.8 g reads | TARE alone reads |
   | --- | --- | --- | --- | --- |
   | 0.5–1.0 | 1.19 | 1.13 | 0.00 | 1.17 |
   | 1.0–1.5 | 1.44 | 1.35 | 0.00 | 1.27 |
   | 1.5–2.0 | 1.74 | 1.42 | 0.00 | 1.30 |
   | 2.0–3.0 | 2.13 | 2.96 | 0.44 | 1.89 |
   | 3.0–4.0 | 2.55 | 7.73 | 2.51 | 3.05 |
   | 9–13 | 4.27 | 14.99 | 3.95 | 4.25 |
   | 20–35 | 6.95 | 42.40 | 7.12 | 7.14 |
   | 50–70 | 10.09 | 79.97 | 9.94 | 9.81 |

   The zero offset reads 0 m/s whenever the true speed is under about 2 m/s. About 28 % of the demo is the fan-off
   coast below 2 m/s, so the zero offset was overfit to the two low holds. Score weighted like the demo
   (72 % at 2 m/s and above, 28 % below), Butterworth 1 Hz, k refitted for each zero offset:

   | Zero offset | k | Demo-weighted MSE | Other run, k from this run |
   | --- | --- | --- | --- |
   | −0.5 g | 142.5 | 0.165 | 0.191 / 0.140 |
   | **0 (TARE alone)** | **145.1** | **0.131** | **0.177 / 0.084** |
   | +0.5 g | 147.0 | 0.152 | 0.219 / 0.084 |
   | +1.0 g | 149.0 | 0.171 | 0.264 / 0.078 |
   | +2.0 g | 152.7 | 0.232 | 0.363 / 0.102 |
   | +2.8 g | 155.8 | 0.279 | 0.418 / 0.141 |

3. **The reference's lag was measured, not assumed:** 0.40 s and 0.48 s, the same in both runs. It belongs to the
   instrument, not to a sequence.

**Result: `sqrt_k` 145.1, `sqrt_x0` 0 (TARE with the fan off).**

## The output filter: Butterworth 0.75 Hz

The output smoother has two jobs that pull in opposite directions:

- **Remove the 15.7 Hz plate ringing.** Any cutoff of 3 Hz or below does this. A 2nd-order Butterworth at
  f_c attenuates 15.7 Hz by (f_c / 15.7)².
- **Delay.** A 2nd-order Butterworth delays the output by about √2 / (2π·f_c): 0.11 s at 2 Hz, 0.30 s at 0.75 Hz,
  0.45 s at 0.5 Hz. Against the true speed any delay is error. Against the VDAS, which is itself 0.4–0.5 s late, a
  delay up to that lag *reduces* the error, and the logger scores against the VDAS.

Scores with k 145.1, TARE zero ("demo-weighted" as above; "true accuracy" scores against the VDAS shifted back by
its 0.44 s lag):

| Filter | Demo-weighted, as scored | 15:17 | 15:25 | True accuracy 15:17 / 15:25 |
| --- | --- | --- | --- | --- |
| EMA α 0.2 (flown on 4 Nov) | 0.277 | 0.143 | 0.389 | 0.118 / 0.130 |
| Butterworth 10 Hz | 0.272 | 0.139 | 0.383 | 0.114 / 0.126 |
| Butterworth 5 Hz | 0.230 | 0.108 | 0.324 | 0.086 / 0.094 |
| Butterworth 3 Hz | 0.207 | 0.101 | 0.288 | 0.082 / 0.093 |
| **Butterworth 2 Hz** (fast option) | 0.184 | 0.097 | 0.250 | **0.082 / 0.099** |
| Butterworth 1.5 Hz | 0.164 | 0.093 | 0.216 | 0.083 / 0.110 |
| Butterworth 1 Hz | 0.131 | 0.087 | 0.160 | 0.086 / 0.144 |
| **Butterworth 0.75 Hz** (default) | **0.106** | 0.082 | 0.118 | 0.091 / 0.194 |
| Butterworth 0.5 Hz | 0.083 | 0.079 | 0.084 | 0.105 / 0.339 |

**Default: 0.75 Hz.** Its 0.3 s delay stays under the VDAS lag measured in both runs, so it is not over-tuned. If
the reference were faster on the day, it would lose about 0.1 against 2 Hz on a run with many pulses. 0.5 Hz scores
a little better but sits exactly on the measured lag, which is more fragile.

Be clear about what 0.75 Hz does: **it does not make the sensor more accurate.** It slows the output to line up
with a late reference, which improves the logger's score. For the true speed as fast as possible, use **Fast 2 Hz**
(below).

## Final results: the runs replayed through each calibration

Each recording's force is run through the firmware chain (`calib/models.py`, kept identical to `airspeed.cpp`) with
each calibration. It is re-zeroed as a fan-off TARE at that recording's stillest low-force 2 s, then scored as the
logger scores: every VDAS sample against the nearest board sample within 150 ms. MSE in m²/s², bias in m/s, "still
air" is the reading in m/s in that TARE window. The Results page shows the same numbers live.

**15:17 run** (`run_20261104_151708`, 167 s covered, re-zeroed at +0.40 g):

| Calibration | MSE | Bias | σ | MSE below 2 m/s | Still air |
| --- | --- | --- | --- | --- | --- |
| As flown (the board's own output) | 4.151 | +1.95 | 0.59 | 0.53 | 0.87 |
| **Calibrated, lag-matched 0.75 Hz (default)** | **0.083** | +0.09 | 0.28 | 0.06 | 0.19 |
| Calibrated, fast 2 Hz | 0.097 | +0.09 | 0.30 | 0.09 | 0.18 |
| Uncalibrated (theory Cd·A) | 3.945 | +1.87 | 0.66 | 0.29 | 0.24 |
| `giga_20261029` (wood tunnel, zero fitted) | 2.088 | +1.37 | 0.46 | 6.95 | 3.03 |
| `giga_20261029_tared` | 4.262 | +1.96 | 0.66 | 0.23 | 0.24 |

**15:25 run** (`run_20261104_152520`, 113 s covered, re-zeroed at +3.81 g):

| Calibration | MSE | Bias | σ | MSE below 2 m/s | Still air |
| --- | --- | --- | --- | --- | --- |
| As flown (the board's own output) | 7.251 | +2.59 | 0.74 | 6.94 | 2.74 |
| **Calibrated, lag-matched 0.75 Hz (default)** | **0.156** | +0.11 | 0.38 | 0.63 | 0.19 |
| Calibrated, fast 2 Hz | 0.296 | +0.11 | 0.53 | 0.85 | 0.18 |
| Uncalibrated (theory Cd·A) | 5.015 | +2.02 | 0.98 | 2.01 | 0.22 |
| `giga_20261029` (wood tunnel, zero fitted) | 2.386 | +1.40 | 0.66 | 7.14 | 3.02 |
| `giga_20261029_tared` | 5.268 | +2.09 | 0.94 | 1.87 | 0.23 |

Without a re-TARE (the 15:17 zero kept for the 15:25 run), the default scores 0.41 and still air reads 2.0 m/s.
**The TARE before each run is part of the calibration.**

The 15:25 run's remaining error below 2 m/s comes from its fast on/off pulses, where the VDAS lag dominates.

The 15:20 logger run can't be replayed: no recording with force covers it. Press Record in the dashboard during
logger runs so every run can be replayed.

These scores are on the data the calibration was fitted to; only the leave-one-run-out numbers above are on data
each fit had not seen. Check the default on a new rehearsal with the fan off at both ends.

## What changed

| Where | Change |
| --- | --- |
| `firmware/include/calibration.h` | Defaults: `CAL_SQRT_K` 145.1 (was 242.5), `CAL_SQRT_X0` 0, `CAL_FILTER_KIND` 0 (Butterworth), `CAL_LPF_HZ` 0.75 (was 10), `CAL_EMA_ALPHA` 0.2 |
| `calibration/calib/models.py` | The same defaults; `SQRT_K_THEORY` 242.5, `SQRT_K_TUNNEL` 145.1, `UNCALIBRATED` (the fallback values) |
| `calibration/calib/tunnel_fit.py` | Built-in calibrations: `default` (lag-matched 0.75 Hz), `calibrated_fast` (2 Hz), `uncalibrated` (theory) |
| `firmware/platformio.ini`, `.github/workflows/ci.yml` | New env `airspeed_drag_plate_uncalibrated` (the theory fallback as a build) |
| `calibration/calib/recal.py` (new) | Replays a logger run through every calibration from the overlapping recording's force |
| `calibration/calib/tunnel.py`, `app/server.py` | `/api/tunnel/run` and `/api/tunnel/window` take `cal=` and return the replay (`calibration/docs/SPEC.md`) |
| `app/static/js/pages/results.js` | Results: green calibrated line, Calibrated MSE readout, Calibrations card; the What to do card is gone (hover the verdict pill for the checks) |
| `app/static/js/core/calibration-controls.js` (new), `js/pages/connection.js` | The Connection card: Calibration picker and Output response (Lag-matched 0.75 Hz, Fast 2 Hz, or any Hz); both left the Wind speed page |
| `calibration/calib/vdas_gt.py`, `js/pages/connection.js` | The "VDAS box busy" message; "no tunnel" in the connection chip |
| `calibration/tests/` | `test_recal.py` (new); the default-parity test now also checks the filter defaults |

Removed: the `vdas_20261104` profile made earlier that day. Its zero was absolute to that day's tare and it used
the overfit zero offset.

## Switching on the dashboard

Click the connection chip at the top right (the three dots): the Connection card has both controls.

- **Calibration**: pick and press Use on board.
  - *Calibrated, lag-matched 0.75 Hz (default)*
  - *Calibrated, fast 2 Hz*
  - *Uncalibrated: theory Cd·A (fallback)*
  - the saved wood-tunnel profiles

  This sets k, F₀ and the filter; the tare and thermometers stay as they are.
- **Output response**: changes only the filter, on the board at once.
  - **Lag-matched 0.75 Hz**: lined up with the VDAS, the best logger score.
  - **Fast 2 Hz**: the true speed, about 0.1 s behind.
  - Or type any cutoff in Hz and press Set.

On the Results page, the Calibrations card scores every option on the chosen run, and its picker chooses which one
is drawn in green on the charts.

## Keeping the board on this calibration

1. **Flash the default build once:** `cd firmware && pio run -e airspeed_drag_plate -t upload`. The calibration is
   compiled in, so it survives power cycles and resets. Rebuild after pulling changes to `calibration.h`. No
   `calibration_generated.h` may exist in `firmware/include/`, because it would override these defaults.
2. **The dashboard never changes it on its own.** Auto-push ("Push every change") is off by default. Values only
   reach the board when you press Push, Use on board or an Output response button. Those last until the next reset.
   After one, the board is back on the flashed default.
3. **Check it:** in the Connection card, Calibration says "Board: Calibrated, lag-matched 0.75 Hz (default)", and Output response says
   "Board: Butterworth 0.75 Hz". Or send `GET`: `sqrt_k=145.1`, `sqrt_x0=0`, `filter_kind=0`, `lpf_hz=0.75`.
4. **Zero it with the fan off before every run: press the blue USER button** (since 5 Nov; README, "Tare without a
   laptop"), or send TARE. The board boots with no tare (`CAL_LC_OFFSET` 0) and does not zero itself, and the zero
   drifts by several grams between runs, so press USER with the fan off just before each one.
5. **Fallback:** pick Uncalibrated in the Connection card, or flash
   `pio run -e airspeed_drag_plate_uncalibrated -t upload`.
