# Contact-Based Airspeed Sensor (COMP0219, Stage 1)

Embedded airspeed sensor for the UCL *Sensors & Signal Conditioning* project. A mechanical transducer
interacts with the flow in a mini wind tunnel, an STM32 **Nucleo-F411RE** estimates airspeed over
**2–10 m/s** entirely on board, and streams it as one float per line at **200 Hz over 115200 baud**.
The tunnel's Dwyer 616KD differential-pressure pitot provides ground truth. The brief is in
[`docs/project-brief-part1.pdf`](docs/project-brief-part1.pdf).

| Requirement (brief)                     | Where it is handled                                                      |
| --------------------------------------- | ------------------------------------------------------------------------ |
| Contact-based transducer, 2–10 m/s      | `AIRSPEED_METHOD` in [`firmware/include/calibration.h`](firmware/include/calibration.h) |
| All processing on the Nucleo            | [`firmware/src/apps/airspeed.cpp`](firmware/src/apps/airspeed.cpp); `host/` only logs and scores |
| 200 Hz float stream, 115200 baud        | TIM5 tick; `host/check_stream.py` verifies rate and drops               |
| Calibration file on demand              | `GET` command → `#cal {json}`; `host/get_calibration.py` saves it        |
| Buildable CubeIDE/PlatformIO project    | [`firmware/platformio.ini`](firmware/platformio.ini), built in CI       |
| Report: MSE, bias, σ                    | `host/evaluate.py`                                                       |

## Repository layout

```
firmware/                 PlatformIO project (Arduino core for STM32 + direct HAL/register access)
  platformio.ini          one env per app: pio run -e <env>
  include/board_config.h  pin map, I2C addresses, rates
  include/calibration.h   transducer selection + calibration constants (overridable, see below)
  src/apps/               entry points: airspeed (competition) + bench tools
  lib/airmath/            pure C++ maths: density, temperature fusion, models, filters (unit-tested)
  lib/drivers/            sensor drivers: TMP36, MCP9808, SHT40, IKS4A1 env, NAU7802, encoder, Hall
  test/                   native unit tests for lib/airmath
host/                     Python tools: logging, stream check, live plot, evaluation, density
calibration/              calibration toolkit + live tuning dashboard (see calibration/docs/SPEC.md)
docs/                     brief, wiring, calibration procedure
data/                     logs and calibration runs worth keeping
```

## Quick start

```bash
# toolchain (once)
python3 -m venv .venv && source .venv/bin/activate
pip install platformio -r host/requirements.txt

# check the I2C bus, then the environmental sensors
cd firmware
pio run -e i2c_scan -t upload && pio device monitor
pio run -e env_monitor -t upload && pio device monitor

# competition firmware
pio run -e airspeed -t upload
cd ..
python host/check_stream.py -d 30 --debug      # 200 Hz, no drops?
python host/live_plot.py                       # watch it
```

On macOS/Linux the ST-LINK port is auto-detected; pass `--port COM5` (or similar) otherwise.

## Firmware

### Apps (PlatformIO envs)

| Env                                         | What it does                                                                 |
| ------------------------------------------- | ---------------------------------------------------------------------------- |
| `airspeed` (default)                        | Competition firmware: 200 Hz estimate, density-compensated, serial protocol |
| `airspeed_cup_hall` / `_drag_plate` / `_cantilever` | Same firmware for the other transducer variants (kept building in CI)  |
| `env_monitor`                               | All five thermometers, pressure, humidity, fused T and air density, 4 Hz CSV |
| `i2c_scan`                                  | Lists I2C devices with the kit part each address belongs to                 |
| `loadcell_test`                             | NAU7802 raw/force; `t` tares, `k 200` calibrates with a 200 g mass           |
| `encoder_test`                              | EM14 count and rev/s from the TIM2 hardware decoder                         |
| `hall_test`                                 | Hall switch rotation rate and linear-Hall voltage                           |
| `imu_test`                                  | LSM6DSO16IS accel/gyro + LIS2MDL magnetometer at 100 Hz                     |

### Signal chain (`airspeed`)

```
transducer feature ──► static model ──► lead ──► median-3 ──► Butterworth LPF ──► "5.123\n"
 (rev/s, N, V)          (uses ρ)        (lead_tau)  (spikes)     (lpf_hz)
                           ▲
 MCP9808 ─┐                │
 SHT40 ───┤ inverse-variance T,  LPS22DF p,  SHT40 RH  ──►  ρ (humid-air ideal gas)
 STTS22H ─┤
 LPS22DF ─┤   refreshed every 250 ms in the idle time between ticks,
 TMP36 ───┘   one bus transaction at a time so the 200 Hz tick never slips
```

Transducer options (`AIRSPEED_METHOD`):

| Method                    | Feature                         | Model                                   |
| ------------------------- | ------------------------------- | --------------------------------------- |
| `METHOD_CUP_ENCODER`      | rotor rev/s from the EM14 (TIM2, x4) | `v = rotor_slope·f + rotor_offset`  |
| `METHOD_CUP_HALL`         | rotor rev/s from magnets + Hall switch | same                              |
| `METHOD_DRAG_PLATE`       | drag force from the load cell [N] | `v = √(sqrt_k·(F − sqrt_x0)/ρ)`, with `sqrt_k = 2/(C_d·A)` |
| `METHOD_CANTILEVER_HALL`  | linear-Hall voltage from a deflecting cantilever | `v = √(sqrt_k·(x − sqrt_x0)/ρ)` |

### Air density

`ρ = (p − p_v)/(R_d·T) + p_v/(R_v·T)`, with `p_v = RH·p_sat(T)` (Magnus, Alduchov–Eskridge).
Missing inputs fall back gracefully: no barometer → 101325 Pa; no humidity → dry air; no thermometer → 15 °C
(ISA, 1.225 kg/m³). Temperatures are fused by inverse variance using datasheet accuracies
(MCP9808 0.25 °C, SHT40 0.2 °C, STTS22H 0.5 °C, LPS22DF 1.5 °C, TMP36 2 °C).

For sqrt-law transducers `v ∝ ρ^-½`, so a 1 K temperature error moves the estimate by about 0.17%, and
ignoring a 10 K swing would cost about 1.7%. The SHT40, STTS22H and LPS22DF sit on the IKS4A1 above the
MCU, so they can read warm. Compare them against the MCP9808 with `env_monitor` before trusting them.

### Calibration constants

Defaults are in [`firmware/include/calibration.h`](firmware/include/calibration.h). Fitted values go in
`firmware/include/calibration_generated.h` (same `CAL_*` macro names, picked up automatically if the file
exists), written by the `calibration/` toolkit. Every `CAL_<NAME>` is also a live parameter that can be
changed over serial with `SET <name> <value>` without reflashing. See [`docs/calibration.md`](docs/calibration.md).

## Serial protocol

115200 baud, 8N1, ST-LINK virtual COM port. Commands are newline-terminated lines and case-insensitive.

| Line from the board       | Meaning                                                                 |
| ------------------------- | ----------------------------------------------------------------------- |
| `5.123`                   | Velocity mode (default): airspeed in m/s, 200 Hz                        |
| `n,v,feature,rho,temp_c,pressure_pa` | Debug mode CSV; `n` is the sample counter (a gap means a dropped tick) |
| `#...`                    | Informational. Never data, so any parser should skip lines starting with `#` |

| Command            | Reply                                                                       |
| ------------------ | --------------------------------------------------------------------------- |
| `MODE V`           | Switch to velocity stream (default)                                        |
| `MODE D`           | Switch to debug CSV; prints `#cols,n,v,feature,rho,temp_c,pressure_pa`      |
| `HDR`              | Re-print the `#cols,...` header (resync after reconnecting)                 |
| `GET`              | `#cal {json}`: method, build, every parameter, current ρ and T             |
| `SET <name> <v>`   | `#ok name=v` / `#err ...`; RAM only, lost on reset                          |
| `TARE`             | Re-zero the load cell from the next 160 samples (drag plate only)          |
| `ENV`              | `#env ...` with every temperature source, RH, p, ρ                          |
| `STATS`            | `#stats n=... overruns=... max_busy_us=...`                                 |
| `HELP`             | Command and parameter list                                                  |

Parameters: `rotor_slope rotor_offset rotor_min_hz rotor_window_ms sqrt_k sqrt_x0 lc_offset lc_n_per_count lead_tau lpf_hz`.

## Host tools

| Script                      | Purpose                                                                 |
| --------------------------- | ----------------------------------------------------------------------- |
| `host/log_serial.py`        | Log the stream to `data/logs/*.csv` with host timestamps                 |
| `host/check_stream.py`      | Pass/fail on 200 Hz with no dropped samples or bad lines                 |
| `host/live_plot.py`         | Scrolling plot of any column                                             |
| `host/get_calibration.py`   | Save the board's `GET` output as JSON (the "calibration file on demand") |
| `host/evaluate.py`          | MSE, RMSE, bias, σ vs ground truth, with lag search and per-speed breakdown |
| `host/airspeed_tools/density.py` | Same density equations as the firmware, for cross-checks and the error budget |

## Hardware

Wiring and pin choices are in [`docs/hardware.md`](docs/hardware.md). In short: the IKS4A1 sits on the Arduino
headers, and the Qwiic parts (NAU7802, MCP9808) share I2C1 on D14/D15. The encoder goes on A0/A1 (TIM2), the
TMP36 on PC2, and the Hall sensor on D7 (digital) or PC3 (linear).

## Tests and CI

```bash
cd firmware && pio test -e native     # lib/airmath unit tests
pytest host                           # host tool tests
```

GitHub Actions builds every firmware env and runs both test suites on each push.

## Status / next steps

- [ ] Pick the transducer (prototype more than one if time allows) and set `AIRSPEED_METHOD`
- [ ] Confirm I2C addresses with `i2c_scan`, then check thermometer agreement with `env_monitor`
- [ ] Calibrate in the tunnel (see `docs/calibration.md`), then tune `lead_tau` and `lpf_hz` on steps and ramps
- [ ] Error budget and dynamic results for the 6-page IEEE report
