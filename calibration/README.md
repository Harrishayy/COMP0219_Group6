# Calibration toolkit

PC-side tools for calibrating our two airspeed sensors against the Dwyer 616KD pitot (ground truth, GT):

- **Cup anemometer** (model `rotor`): `v = air per rev × spin rate + start-up speed`
- **Drag plate** on the NAU7802 load cell (model `sqrt`): `v = √(2·(F − F₀) / (ρ·Cd·A))`, with
  `ρ = P / (287.05·T)` from the baro and temperature sensors

Both feed the board's output filter: median-3 → lag compensation → EMA `V = α·V_new + (1 − α)·V_prev` → clamp ≥ 0.

The board does the estimating, as the brief requires. This toolkit records the board and the pitot, previews the
firmware maths with new settings, fits them, and writes them back to the board (`SET`) or into the firmware build
(`firmware/include/calibration_generated.h`). `calib/models.py` mirrors `firmware/lib/airmath` and
`airspeed.cpp` line for line, and the tests check it against a literal port of the C++.

## Install and test

```bash
cd calibration
python3 -m pip install -r requirements.txt
python3 -m pytest -q
```

## Use

```bash
python3 -m calib serve            # dashboard at http://localhost:8765 (starts on the simulator)
```

In the dashboard:

1. **Setup**: pick the Nucleo port and how to read the ground truth, then press **Connect hardware**.
2. **Steps**: the guided calibration for the chosen sensor.
   - Drag plate: tare with the fan off, load-cell scale with known masses, zero-flow force F₀, sweep 2 → 10 m/s,
     fit Cd·A and F₀, fit α and lag compensation, push and verify, export.
   - Cup anemometer: sweep, fit air per rev and start-up speed, stall cut-off, rate window, fit α and lag
     compensation, push and verify, export.
3. **Tune / Fit**: adjust any setting by hand or fit it, and see the effect against the pitot straight away.
4. **Record**: saves every sample with the settings in force (see below).

The same work without the dashboard:

| Command | What it does |
|---|---|
| `ports` | List serial ports. |
| `check --port P [--debug]` | Check the marking requirement: 200 Hz, no dropped samples, no bad lines. Exits 1 on failure. |
| `get --port P` | Save the board's live calibration (`GET`) as JSON: the calibration file on demand. |
| `weights --port P` | Load-cell scale from hanging known masses (drag plate). |
| `record --port P --gt-cmd …` | Record a session without the dashboard. |
| `fit run.csv` | Fit settings to a recording (`--method robust_lsq` for the curve, `dynamics` for α and lag) and export. `--install` also writes the firmware header. |
| `report run.csv` | Scores for a recording: MSE, bias, σ, lag, step response, error per speed band. |
| `session run.json` | One row per settings change in a recording, with its score against the pitot. `--png` plots it. |
| `export cal.json` / `push cal.json --port P` | Rewrite the header and `SET` file from a calibration JSON / send it to the board. |
| `sim` | Write a simulated run, for trying things without hardware. |

## Ground truth

Point the toolkit at the lab's pitot script in whichever form we are given (in Setup, or as a flag):

- `--gt-cmd "python3 their_script.py"`: the last number on each printed line is taken as m/s.
- `--gt-module gt.py:read_sample`: a function (or generator) returning m/s.
- `--gt-port /dev/tty.xxx`: a serial device printing one number per line.

## Recordings

**Record** writes two files to `runs/` (ignored by git):

- `run_<time>_<label>.csv`: one row per 200 Hz sample, with ground truth, board output, PC preview, both errors,
  the raw reading (spin rate or force), ρ, temperature, pressure, the settings-set number `pset`, and every
  setting as its own column.
- `run_<time>_<label>.json`: every settings set with its time span, a timeline of changes (from → to), fits,
  pushes, tares and notes, and each segment's score against the pitot.

Any change mid-run starts a new segment, so `python3 -m calib session runs/<run>.json` shows what each change did.

## Files

```
calib/models.py    firmware maths mirror (both sensors, filter chain, air density)
calib/fit.py       curve fit, α/lag fit, load-cell scale from masses
calib/metrics.py   MSE, bias, σ, lag, step response
calib/sources.py   serial board, ground-truth adapters, replay, simulator
calib/recorder.py  session recording and the list of past runs
calib/data.py      run CSV loading
calib/export.py    calibration JSON, firmware header, SET commands
calib/cli.py       the commands above
app/server.py      local server behind the dashboard
app/static/index.html  the dashboard
docs/SPEC.md       serial and dashboard protocol
tests/             firmware match, fits, serial parsing, recording, server
```
