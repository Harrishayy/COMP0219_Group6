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

From the repo root, `pnpm dev` sets everything up (see the root README). By hand:

```bash
cd calibration
python3 -m pip install -r requirements.txt
python3 -m pytest -q
```

## Use

```bash
pnpm dev                          # from the repo root: simulator
pnpm dev:board                    # from the repo root: the Nucleo
python3 -m calib serve            # by hand, from calibration/: dashboard at http://localhost:8765
```

In the dashboard, open **Connection** (the chip at top right: dots for board, pitot and environment) and pick the
Nucleo port and the pitot source, or start the simulator. The simulator's **Rig** switch plays the tunnel (fan and
pitot) or the open bench (no airflow, no pitot). Every sensor works on either: steps that need airflow or the pitot
are skipped on the bench, never blocking.

There is one tab per part in the project brief, all with the same layout: the stat strip, a chart,
**Recommendations** (what is wrong and the fix, often one click), **Calibrate** (the steps, in order), **Settings**
where the sensor has firmware settings, and **Reference** (parts, wiring, what the brief asks, the bench test and the
commands to run it). Tab badges show steps done.

1. **Load cell**: tare, scale with known masses (the 1 kg cell takes 0–1000 g), check with one more mass.
2. **Environment**: the five thermometers (MCP9808, SHT40, STTS22H, LPS22DF, TMP36), pressure and humidity behind
   the air density ρ; let them settle, compare with a reference.
3. **Pitot**: the ground truth; connect it, check it reads zero with no airflow and is quiet at a held speed.
4. **Airspeed**: zero-flow force F₀ and a known force on the plate (anywhere); in the tunnel, sweep 2 → 10 m/s, fit
   the curve (Cd·A, F₀), fit the smoothing and lag, push and verify against the pitot.
5. **Encoder**, **Hall**, **IMU**: in the brief's kit but not read by the drag-plate build. Their tabs are dimmed and
   show the reference and how to bench-test each part with its own firmware.
6. **Runs**: record sessions, export or install the calibration, replay past runs, activity log.

### Adding a sensor or changing the look

The dashboard is plain ES modules in `app/static/` (no build step). A sensor is one file in
`app/static/js/sensors/`: copy `_template.js`, fill it in and add one line to `js/sensors/index.js` (the contract is
documented there); it gets its own tab with the shared layout and no new CSS. All colours, fonts and spacing are
tokens in `css/tokens.css` (direction "Notebook"; fonts bundled in `fonts/` so it works offline).
`app/static/CLAUDE.md` has the map of files, the rules and the component catalogue, so changes made with Claude stay
consistent.

The same work without the dashboard:

| Command | What it does |
|---|---|
| `ports` | List serial ports. |
| `check --port P [--debug]` | Check the marking requirement: 200 Hz, no dropped samples, no bad lines. Exits 1 on failure. |
| `get --port P` | Save the board's live calibration (`GET`) as JSON: the calibration file on demand. |
| `weights --port P` | Load-cell scale from hanging known masses (drag plate). |
| `stream [--port P]` | Live load-cell counts and force in the terminal, from the running dashboard (or straight off the board with `--port`). |
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
app/static/         the dashboard: index.html, css/, js/core/, js/sensors/ (one file per sensor), js/pages/
docs/SPEC.md       serial and dashboard protocol
tests/             firmware match, fits and diagnosis, serial parsing, recording, server
```
