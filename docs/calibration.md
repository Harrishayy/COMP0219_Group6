# Calibration procedure

The estimate must run on the Nucleo, so calibration happens offline: fit constants on the PC, then compile
them in (`calibration_generated.h`) or set them live (`SET`). The board only ever applies them.

## 1. Before the tunnel

1. `i2c_scan`: every expected address responds.
2. `env_monitor`: let it run for 10 minutes. The thermometers should agree within their stated accuracy.
   An IKS4A1 sensor that reads consistently warm is being heated by the board. Raise its sigma in
   `env_sensors.cpp`, or leave it out.
3. Transducer bench test (`encoder_test`, `hall_test`, `loadcell_test`):
   - load cell: send `t` to tare, then `k <grams>` with a known mass. Copy `CAL_LC_OFFSET` and
     `CAL_LC_N_PER_COUNT` into the calibration header.
   - encoder: one hand-turned revolution should give `kEncoderCountsPerRev` counts.

## 2. Static calibration in the tunnel

Flash `airspeed` with the method selected, then switch to debug mode (`MODE D`). The `feature` column
is the raw transducer quantity that the model maps to speed.

For each fan setting from about 1.5 to 11 m/s (go a little beyond the 2–10 m/s range), in both increasing
and decreasing order to expose hysteresis:

1. wait for it to settle (rotors take a few seconds)
2. log 10 s: `python host/log_serial.py --mode d -d 10 -o data/cal/step_XX.csv`
3. record the reference speed from the tunnel's pitot logger for the same window

Fit the model to (mean feature, reference speed, mean ρ):

| Model    | Fit                                                                     |
| -------- | ----------------------------------------------------------------------- |
| linear   | least squares of `v` on `f`; `rotor_offset` ≈ stall speed               |
| sqrt     | `ρ·v²` is linear in `x`: slope = `sqrt_k`, x-intercept = `sqrt_x0`      |

Fitting `ρ·v²` instead of `v` means the constant transfers between days with different air density.
The `calibration/` toolkit does this fitting and writes `calibration_generated.h`.

## 3. Dynamic tuning

The assessment scores MSE on steps and ramps, so lag costs as much as static error.

1. Log a step sequence with the ground truth.
2. `python host/evaluate.py data/logs/run.csv --truth truth.csv --auto-lag --plot`
3. Increase `lead_tau` (`SET lead_tau 0.15`) until the rotor's spin-up lag is cancelled without overshoot.
   Lower `lpf_hz` until the noise is acceptable. Both are live, so iterate without reflashing.
4. Copy the final values into `calibration_generated.h` and re-check with `GET`.

## 4. On demo day

- `python host/get_calibration.py` saves the board's calibration JSON (the "calibration file on demand").
- `python host/check_stream.py -d 30 --debug` confirms 200 Hz with no drops, then `MODE V` before handing over.
