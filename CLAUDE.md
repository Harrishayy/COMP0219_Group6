# Airspeed sensor: working in this repo

COMP0219 Stage 1: a contact-based airspeed sensor (drag plate on a load cell) that estimates 2–10 m/s on a
Nucleo-F411RE and streams it at 200 Hz, 115200 baud. The brief is `docs/project-brief-part1.pdf`; who the
calibration bench is for and its design principles are in `PRODUCT.md`.

## Run it

```bash
pnpm dev          # calibration dashboard on the simulator (creates .venv and installs on first run)
pnpm dev:board    # dashboard on the Nucleo; BENCH_PORT=/dev/… to choose; extra args go to `calib serve`
pnpm test         # calibration tests
cd firmware && pio run -e airspeed_drag_plate -t upload   # flash the competition build
```

`pnpm` only starts the Python server (`scripts/bench.mjs`); there is no JavaScript build. If port 8765 is
busy it picks the next free one, so check the printed URL.

## Directories

| Path | What it is | How it works |
| --- | --- | --- |
| `firmware/` | PlatformIO project, Arduino core for STM32 | One env per app in `platformio.ini`; `src/apps/*.cpp` are the entry points |
| `firmware/src/apps/airspeed.cpp` | Competition firmware | 200 Hz TIM5 tick: feature → model (uses ρ) → median-3 → lead → EMA or Butterworth. Serial commands: MODE, GET, SET, TARE, ENV, STATS. Transducer picked by `AIRSPEED_METHOD` |
| `firmware/src/apps/` (others) | Bench tools | `i2c_scan`, `env_monitor`, `loadcell_test`, `encoder_test`, `hall_test`: flash, then `pio device monitor` |
| `firmware/include/` | Configuration | `board_config.h`: pins, I²C addresses, rates. `calibration.h`: defaults for every `CAL_*`; `calibration_generated.h` (written by the toolkit) overrides them |
| `firmware/lib/airmath/` | Pure C++ maths | Density, temperature fusion, models, filters. Unit-tested natively (`pio test -e native`) |
| `firmware/lib/drivers/` | Sensor drivers | NAU7802, MCP9808, SHT40, IKS4A1 env (`env_sensors.cpp`), TMP36, encoder, Hall |
| `calibration/` | Calibration toolkit (Python) and dashboard | `python -m calib <cmd>`; protocol and file formats in `calibration/docs/SPEC.md` |
| `calibration/calib/` | Toolkit package | `sources.py` serial, pitot and simulator streams; `models.py` the firmware chain mirrored in numpy and every parameter's spec; `fit.py` curve and response fits; `metrics.py` scores and diagnosis; `recorder.py` sessions; `export.py` JSON, C header, SET script; `cli.py` commands |
| `calibration/app/server.py` | Dashboard server | FastAPI + one websocket. Owns the serial port, runs the PC preview, fits, diagnosis, recording; serves `app/static/` |
| `calibration/app/static/` | Dashboard UI | Plain ES modules, no build. One file per sensor in `js/sensors/`. Its own guide: `calibration/app/static/CLAUDE.md` |
| `calibration/tests/` | Toolkit tests | Chain mirror vs a literal port of the C++, fits, parser, sessions, websocket smoke tests |
| `host/` | Older stand-alone tools | Log, check the 200 Hz stream, live plot, score against ground truth, density. `pytest host` |
| `docs/` | Brief, wiring (`hardware.md`), calibration procedure | |
| `data/` | Runs worth keeping | Raw logs in `data/logs/` are git-ignored; dashboard recordings go to `calibration/runs/` (ignored) |
| `scripts/bench.mjs` | The `pnpm` launcher | venv and requirements, board port detection, free HTTP port |

## Recipes

**Add a sensor to the dashboard.** Copy `calibration/app/static/js/sensors/_template.js`, fill it in, add one
line to `js/sensors/index.js`. No CSS or layout code. If the firmware does not read the part yet, give it
`available: () => ({ live: false, … })` and an `info` block so its tab shows the reference and bench test.

**Change how the dashboard looks.** Colours, fonts, sizes, spacing: `calibration/app/static/css/tokens.css`
only. A component's shape: `css/app.css`, using tokens. Layout every sensor shares:
`js/core/sensor-page.js`. The component catalogue is in `calibration/app/static/CLAUDE.md`.

**Add a firmware parameter** (live, settable with SET, exported to the header):
1. `firmware/include/calibration.h`: `#define CAL_<NAME>` default.
2. `firmware/src/apps/airspeed.cpp`: a field in `Params` and a row in `kParams`; use it in the chain.
3. `calibration/calib/models.py`: a `ParamSpec` in `PARAMS`, the name in `FIRMWARE_PARAMS`, and mirror its
   effect in `apply()` so the PC preview matches the board.
4. A test in `calibration/tests/test_chain.py`; a settings field in `js/sensors/airspeed.js` if it should
   be tunable from the dashboard.

**Add a firmware bench app.** `firmware/src/apps/<name>.cpp`, an `[env:<name>]` with
`build_src_filter = +<apps/<name>.cpp>` in `platformio.ini`, and add the env to `.github/workflows/ci.yml`.

**Add a toolkit command.** A `cmd_<name>(a)` and a `sub.add_parser("<name>")` in `calibration/calib/cli.py`,
plus a row in the command table in `calibration/README.md`.

**Add a server message.** Handle it in the websocket loop in `calibration/app/server.py`, add an action in
`js/core/store.js`, document it in `calibration/docs/SPEC.md` §6, and cover it in `tests/test_server.py`.

## Rules

- The board is the truth. Everything runs on the Nucleo (brief requirement); the PC only previews, fits and
  records. A PC change reaches the board only by a push (SET) or an export into the header.
- Keep `calib/models.py` identical in behaviour to `airspeed.cpp`; `test_chain.py` guards it.
- Sensors are tested on the open bench and in the tunnel. Nothing that needs airflow or the pitot may block
  a bench step; such steps return `{ tone: 'skip' }`.
- Pins and addresses live in `board_config.h` and `docs/hardware.md`, never in app code.
- Commits: imperative sentence-case subject ("Add …", "Make …"), body explains why. No AI co-author trailers.

## Check a change

```bash
pnpm test                                   # calibration
pytest host -q                              # host tools
cd firmware && pio test -e native           # airmath
cd firmware && pio run -e airspeed_drag_plate
```

Port 8765 may already be serving the real board from another terminal; `pnpm dev` then uses the next port.
