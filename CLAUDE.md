# Airspeed sensor: working in this repo

COMP0219 Stage 1: a contact-based airspeed sensor (drag plate on a load cell) that estimates 2–10 m/s on a
Nucleo-F411RE and streams it at 200 Hz, 115200 baud. The brief is `docs/project-brief-part1.pdf`; who the
calibration bench is for and its design principles are in `PRODUCT.md`.

## Run it

```bash
pnpm dev          # dashboard on the Nucleo, white tunnel's VDAS box as ground truth (fan off at start); each waits until plugged in (creates .venv)
pnpm dev:sim      # dashboard on the simulator; every page says the readings are synthetic
pnpm test         # calibration tests + wiring circuit check (pnpm check:wiring alone)
pnpm logger       # the course's Wind Tunnel Logger (assessed tunnel runs), saving to data/tunnel/; runs beside pnpm dev
pnpm dev --gt-giga   # the wood tunnel's GIGA pitot tube as ground truth instead; --no-gt for none
cd firmware && pio run -e airspeed_drag_plate -t upload   # flash the competition build
```

`pnpm` only starts the Python server (`scripts/bench.mjs`); there is no JavaScript build. `BENCH_PORT=/dev/…`
picks the port; extra args go to `calib serve`. The board is never replaced by the simulator silently: no board
means a "No Nucleo found" banner and dashes, not data. If port 8765 is busy it picks the next free one, so
check the printed URL.

Live reload: with `pnpm dev`, saving a dashboard file updates the open page (CSS in place, JS/HTML by reloading
on the same tab and scroll position) and saving a toolkit `.py` restarts the server, after which the page reloads.
Step progress kept in a sensor module resets on a reload. Firmware still needs `pio run … -t upload`.
`BENCH_RELOAD=0 pnpm dev` turns it off.

## Directories

| Path | What it is | How it works |
| --- | --- | --- |
| `firmware/` | PlatformIO project, Arduino core for STM32 | One env per app in `platformio.ini`; `src/apps/*.cpp` are the entry points |
| `firmware/src/apps/airspeed.cpp` | Competition firmware | 200 Hz TIM5 tick: feature → model (uses ρ) → median-3 → lead → EMA or Butterworth. Serial commands: MODE, GET, SET, TARE, ENV, STATS. Transducer picked by `AIRSPEED_METHOD` |
| `firmware/src/apps/` (others) | Bench tools | `i2c_scan`, `env_monitor`, `loadcell_test`, `encoder_test`, `hall_test`: flash, then `pio device monitor` |
| `firmware/include/` | Configuration | `board_config.h`: pins, I²C addresses, rates. `calibration.h`: defaults for every `CAL_*`; `calibration_generated.h` (written by the toolkit) overrides them |
| `firmware/lib/airmath/` | Pure C++ maths | Density, temperature fusion, models, filters. Unit-tested natively (`pio test -e native`) |
| `firmware/lib/drivers/` | Sensor drivers | Load cell (`load_cell.cpp`: M5Stack Weight I2C or NAU7802, found at runtime), MCP9808, SHT40, IKS4A1 env (`env_sensors.cpp`), TMP36, LM35, encoder, Hall |
| `calibration/` | Calibration toolkit (Python) and dashboard | `python -m calib <cmd>`; protocol and file formats in `calibration/docs/SPEC.md` |
| `calibration/calib/` | Toolkit package | `sources.py` serial, pitot and simulator streams; `models.py` the firmware chain mirrored in numpy and every parameter's spec; `fit.py` curve and response fits; `metrics.py` scores and diagnosis; `recorder.py` sessions; `export.py` JSON, C header, SET script; `cli.py` commands |
| `calibration/app/server.py` | Dashboard server | FastAPI + one websocket. Owns the serial port, runs the PC preview, fits, diagnosis, recording; serves `app/static/` |
| `calibration/app/static/` | Dashboard UI | Plain ES modules, no build. A one-screen dashboard (a tile per sensor), a calibration page per sensor (`js/sensors/`: temperature, load cell, wind speed) under a strip of every reading, Runs, Results (any saved tunnel run, any moment of it, with its files). Keys 1–6 switch; Record is always in the bar. Its own guide: `calibration/app/static/CLAUDE.md` |
| `calibration/calib/tunnel_fit.py`, `calibration/profiles/` | Calibration profiles | Fitted to recordings with noisy ground truth; switchable from the Wind speed page or by env |
| `calibration/calib/tunnel.py` | Wind Tunnel Logger runs | Reads `data/tunnel/*_aligned.csv`, scores the board against the tunnel, measures drift; `calib tunnel`, `/api/tunnel/*` |
| `tools/wind-tunnel-logger/` | The TA's Wind Tunnel Logger v1.0.1, unmodified | Live board vs tunnel plot and the CSVs that are assessed. `run.py` redirects its logs to `data/tunnel/` and lists the dashboard's board relay as a port. Do not edit the package |
| `calibration/tests/` | Toolkit tests | Chain mirror vs a literal port of the C++, fits, parser, sessions, websocket smoke tests |
| `host/` | Older stand-alone tools | Log, check the 200 Hz stream, live plot, score against ground truth, density. `pytest host` |
| `docs/` | Brief, wiring (`hardware.md`), calibration procedure, every formula (`formulas.md`: drag, C_d by shape, density, filters, error budgets, what to redo when something changes), the graded demo protocol (`demo-protocol.md`, with the original email screenshot) | `docs/datasheets/` PDFs, served at `/datasheets/` for the Datasheet tabs |
| `calibration/app/static/js/wiring/` | Breadboard wiring diagrams | `nucleo.js` morpho pin map, `breadboard.js` netlist + circuit check, `render.js` the Wiring tab, `parts/*.js` one spec per part |
| `scripts/check-wiring.mjs` | Wiring circuit check | Every spec traced as a circuit and its pins checked against `board_config.h` |
| `data/` | Runs worth keeping | Raw logs in `data/logs/` are git-ignored; dashboard recordings go to `calibration/runs/` (ignored) |
| `scripts/bench.mjs` | The `pnpm` launcher | venv and requirements, board port detection, free HTTP port |

## Recipes

**Add a sensor to the dashboard.** Copy `calibration/app/static/js/sensors/_template.js`, fill it in, add one
line to `js/sensors/index.js`: it gets a dashboard tile and its own calibration page. No CSS or layout code. If the
firmware does not read the part yet, give it `available: () => ({ live: false, … })` and an `info` block so its
page shows the reference and bench test.

**Add a wiring diagram or datasheet to a sensor tab.** Use the `wiring-diagram` skill
(`.claude/skills/wiring-diagram/SKILL.md`): a spec in `calibration/app/static/js/wiring/parts/`, the PDF in
`docs/datasheets/`, then `pnpm check:wiring`.

**Change how the dashboard looks.** Colours, fonts, sizes, spacing: `calibration/app/static/css/tokens.css`
only. A component's shape: `css/app.css`, using tokens. Layout every sensor shares:
`js/core/sensor-page.js`; the one-screen dashboard and the readings strip: `js/core/dashboard.js`. The component
catalogue is in `calibration/app/static/CLAUDE.md`.

**Add a firmware parameter** (live, settable with SET, exported to the header):
1. `firmware/include/calibration.h`: `#define CAL_<NAME>` default.
2. `firmware/src/apps/airspeed.cpp`: a field in `Params` and a row in `kParams`; use it in the chain.
3. `calibration/calib/models.py`: a `ParamSpec` in `PARAMS`, the name in `FIRMWARE_PARAMS`, and mirror its
   effect in `apply()` so the PC preview matches the board.
4. A test in `calibration/tests/test_chain.py`; a settings field in `js/sensors/wind-speed.js` if it should
   be tunable from the dashboard.

**Add a firmware bench app.** `firmware/src/apps/<name>.cpp`, an `[env:<name>]` with
`build_src_filter = +<apps/<name>.cpp>` in `platformio.ini`, and add the env to `.github/workflows/ci.yml`.

**Add a toolkit command.** A `cmd_<name>(a)` and a `sub.add_parser("<name>")` in `calibration/calib/cli.py`,
plus a row in the command table in `calibration/README.md`.

**Add a server message.** Handle it in the websocket loop in `calibration/app/server.py`, add an action in
`js/core/store.js`, document it in `calibration/docs/SPEC.md` §6, and cover it in `tests/test_server.py`.

**Recover the board** (no data, a sensor reads nothing, readings drop back to zero). In order:
1. Free the port: one program at a time owns it. `lsof /dev/cu.usbmodem*` shows who has it; stop extra dashboards
   (`pkill -f "calib serve"`) and serial monitors. Two readers split the bytes and both keep reconnecting.
2. Clean-reinstall the STM32 libraries, rebuild and flash (PlatformIO is `../.venv/bin/pio` from `firmware/`):
   ```bash
   cd firmware && rm -rf .pio/libdeps/airspeed_drag_plate .pio/build/airspeed_drag_plate \
     && ../.venv/bin/pio pkg install -e airspeed_drag_plate \
     && ../.venv/bin/pio run -e airspeed_drag_plate -t upload
   ```
   This is what got the load cell reading. Any env works the same way (`-e loadcell_test`, `-e i2c_scan`).
3. The board boots in velocity-only mode. If the dashboard shows the board streaming but force or other columns
   are blank (after a reflash or reset with the dashboard running), click "Send MODE D" or restart `pnpm dev`.
4. Ask the board: `STATS` reports `lc_samples` and `lc_read_failures` (should stay 0); `GET` reports `lc_frontend`
   (`weight_i2c`, `nau7802` or `none`). Bus check: flash `i2c_scan` (Weight I2C 0x26, NAU7802 0x2A).
5. Cross-check on another software stack: the STM32 HAL program in `labs/loadcell/` (no Arduino, own NAU7802
   driver). Build with `python3 labs/loadcell/build.py`, flash it, read raw counts at 2 Hz, then reflash step 2:
   ```bash
   OCD=~/.platformio/packages/tool-openocd; $OCD/bin/openocd -s $OCD/openocd/scripts -f board/st_nucleo_f4.cfg \
     -c "program labs/loadcell/build/loadcell.bin 0x08000000 verify reset exit"
   ```
   If both stacks agree, the fault is in hardware.

**Load cell checks** (`docs/hardware.md` has the detail):
- The firmware uses whichever amplifier answers on I²C, the M5Stack Weight I2C (HX711, 10/s, needs 5V) or the
  Qwiic Scale (NAU7802, 320/s). Swapping is a re-plug, then tare and scale again.
- The cell itself: unplugged, a multimeter on Ω 2W reads about 1.0 kΩ red–black and green–white (probes on bare
  copper, fingers off the metal; tens of kΩ or MΩ means no contact, not a dead cell).
- Wires need bright bare copper fully in the terminal, or solder them to the RED/BLK/WHT/GRN pads. A reading that
  jumps between ~0 and millions within milliseconds is a wire making and losing contact.
- Mount a bar cell by its end block only, on a spacer, arrow pointing down, force on the free end. Clamping the
  middle (gauge) section or resting the bar on the table gives no reading.
- `bridge_probe` checks a bridge without a meter (NAU7802 channel survey, A0/A1 as a voltmeter).

**Calibrate from noisy tunnel runs and switch calibrations.** `python -m calib tunnel-fit <recordings…> --name <n>`
(dashboard recordings in `calibration/runs/`, which hold force and ρ) cleans the ground truth and fits a profile:
`calibration/profiles/<n>.{json,cmd}` and `firmware/include/profiles/<n>/calibration_profile.h`. Switch a running board on the Wind speed
page (Settings › Calibration › Use on board), or flash `pio run -e airspeed_drag_plate_<n> -t upload` (add the env
to `platformio.ini` and `ci.yml`, copying the `giga_20261029` ones). Flash `airspeed_drag_plate`, or pick Default, to go back.
`--zero fit` bakes in that day's zero offset; `--zero tare` trusts a fan-off TARE.

**Tunnel session.** The assessed runs come from the Wind Tunnel Logger (`pnpm logger`, ground truth *Wind tunnel*
on the white tunnel, *GIGA pitot tube* on the wood one). It reads the last number on each line as the speed, so
the debug row ends in `v`; keep it last. To calibrate in the tunnel with force and ρ alongside, use
`pnpm dev` (white tunnel's VDAS box, the default) or `pnpm dev --gt-giga` (wood tunnel) instead. Record there also saves the
run in the logger's format in `data/tunnel/`. Both run at once: with `pnpm dev --gt-giga` up, `pnpm logger` gets the board as
"Board via the dashboard" (`calibration/.board-relay`) and picks the GIGA through the dashboard as its ground truth by
itself (`calibration/.giga-relay`, both ways: the logger's settings and zero reach the GIGA and win over ours); each
program records on its own, and stopping one never stops the other (`calib/relay.py`, `calib/giga_gt.py`). The VDAS
box cannot be shared: with `pnpm dev` (VDAS) the logger has no tunnel ground truth, so for an assessed white-tunnel
run close the dashboard or start it with `--no-gt`. Compare saved runs
on the Wind speed page's Tunnel runs panel or with `calib tunnel`; "Replay" runs a logger run through the
calibration views (live chart, accuracy, by speed, score, diagnosis; no force was logged, so no curve fit).

## Rules

- The board is the truth. Everything runs on the Nucleo (brief requirement); the PC only previews, fits and
  records. A PC change reaches the board only by a push (SET) or an export into the header.
- Keep `calib/models.py` identical in behaviour to `airspeed.cpp`, defaults included (`CAL_SQRT_K` equals the
  toolkit's `sqrt_k`); `test_chain.py` guards it.
- Sensors are tested on the open bench and in the tunnel. Nothing that needs airflow or the pitot may block
  a bench step; such steps return `{ tone: 'skip' }`.
- Pins and addresses live in `board_config.h` and `docs/hardware.md`, never in app code.
- When a sensor or the stream misbehaves, check the STM32 software side first and follow ST's documented way of
  doing things (the STM32duino core and STM32 HAL as ST and the library authors describe them), before blaming
  wiring: run "Recover the board" below.
- Commits: imperative sentence-case subject ("Add …", "Make …"), body explains why. No AI co-author trailers.

## Check a change

```bash
pnpm test                                   # calibration
pytest host -q                              # host tools
cd firmware && pio test -e native           # airmath
cd firmware && pio run -e airspeed_drag_plate
```

Port 8765 may already be serving the real board from another terminal; `pnpm dev` then uses the next port.
