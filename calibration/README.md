# Calibration toolkit

PC-side tools for calibrating our drag-plate airspeed sensor (force on the NAU7802 load cell) against the
Dwyer 616KD pitot (ground truth, GT):

`v = √(2·(F − F₀) / (ρ·Cd·A))`, with `ρ = P / (287.05·T)` from the baro and temperature sensors.

The board then filters it: median-3 → lag compensation → smoother → clamp ≥ 0. The smoother is either an EMA
`V = α·V_new + (1 − α)·V_prev` (default) or a 2nd-order Butterworth low-pass, which damps the plate's ringing
better for the same lag.

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
2. **Steps**: tare with the fan off, load-cell scale with known masses, zero-flow force F₀, sweep 2 → 10 m/s,
   fit Cd·A and F₀, choose the smoother and fit it with lag compensation, push and verify, export.
3. **Tune / Fit**: adjust any setting by hand or fit it, and see the effect against the pitot straight away.
   The diagnosis lists what to change when the sensor disagrees with the pitot (below), with the suggested values.
4. **Record**: saves every sample with the settings in force (see below).

The same work without the dashboard:

| Command | What it does |
|---|---|
| `ports` | List serial ports. |
| `check --port P [--debug]` | Check the marking requirement: 200 Hz, no dropped samples, no bad lines. Exits 1 on failure. |
| `get --port P` | Save the board's live calibration (`GET`) as JSON: the calibration file on demand. |
| `weights --port P` | Load-cell scale from hanging known masses (drag plate). |
| `record --port P --gt-cmd …` | Record a session without the dashboard. |
| `fit run.csv` | Fit settings to a recording (`--method robust_lsq` for the curve, `dynamics` for the smoother and lag) and export. `--install` also writes the firmware header. |
| `report run.csv` | Scores for a recording (MSE, bias, σ, lag, step response, error per speed band) and the diagnosis. |
| `session run.json` | One row per settings change in a recording, with its score against the pitot. `--png` plots it. |
| `export cal.json` / `push cal.json --port P` | Rewrite the header and `SET` file from a calibration JSON / send it to the board. |
| `sim` | Write a simulated run, for trying things without hardware. |

## Ground truth

Point the toolkit at the lab's pitot script in whichever form we are given (in Setup, or as a flag):

- `--gt-cmd "python3 their_script.py"`: the last number on each printed line is taken as m/s.
- `--gt-module gt.py:read_sample`: a function (or generator) returning m/s.
- `--gt-port /dev/tty.xxx`: a serial device printing one number per line.

Add `--gt-pressure` if the script prints the pitot's differential pressure in Pa rather than m/s: each sample
becomes `v = √(2·Δp/ρ)` with the board's live ρ.

## When the sensor disagrees with the pitot

The dashboard and `report` check these on steady speeds (so filter lag is not mistaken for a calibration error)
and suggest the change:

| Symptom | Likely cause | What to change |
|---|---|---|
| Off at every steady speed | Cd·A or F₀ wrong | Refit the curve (`sqrt_k = 2/(Cd·A)`, `sqrt_x0 = F₀`) |
| Same % error at every speed | Cd·A or load-cell scale wrong | Scale `sqrt_k` by 1/r², or redo the hanging weights |
| Error grows as speed drops | F₀ wrong | Adjust `sqrt_x0`, or re-measure F₀ with the fan off |
| Reads 0 with the fan on | F₀ too high, or TARE taken with load/wind | Fan off, TARE, re-measure F₀ |
| Bias creeps during a run | Load-cell zero drifting with temperature | Warm up 10 min, TARE and F₀ just before the run |
| Lags the pitot (> 40 ms) | Smoother too slow | Raise α / the Butterworth cutoff, or add lag compensation |
| Noisy at steady speed | Smoother too fast | Lower α / the cutoff |
| Overshoots steps | Too much lag compensation, or plate ringing | Halve `lead_tau`, or switch to Butterworth ~4 Hz |
| Error varies with speed after the above | Cd not constant (Reynolds, plate bending) | Fit on the competition range, or stiffen the plate |
| Every speed off by the same factor and ρ odd | Baro or temperature reading wrong | Check `ENV`: ρ should be 1.10–1.30 kg/m³ |
| Pitot wanders below 3 m/s | Tiny dynamic pressure (≈5 Pa) | Robust fit, hold low speeds longer |

## Recordings

**Record** writes two files to `runs/` (ignored by git):

- `run_<time>_<label>.csv`: one row per 200 Hz sample, with ground truth, board output, PC preview, both errors,
  the drag force, ρ, temperature, pressure, the settings-set number `pset`, and every
  setting as its own column.
- `run_<time>_<label>.json`: every settings set with its time span, a timeline of changes (from → to), fits,
  pushes, tares and notes, and each segment's score against the pitot.

Any change mid-run starts a new segment, so `python3 -m calib session runs/<run>.json` shows what each change did.

## Files

```
calib/models.py    firmware maths mirror (drag plate, filter chain, air density, pitot)
calib/fit.py       curve fit, smoother/lag fit, load-cell scale from masses
calib/metrics.py   MSE, bias, σ, lag, step response, diagnosis
calib/sources.py   serial board, ground-truth adapters, replay, simulator
calib/recorder.py  session recording and the list of past runs
calib/data.py      run CSV loading
calib/export.py    calibration JSON, firmware header, SET commands
calib/cli.py       the commands above
app/server.py      local server behind the dashboard
app/static/index.html  the dashboard
docs/SPEC.md       serial and dashboard protocol
tests/             firmware match, fits and diagnosis, serial parsing, recording, server
```
