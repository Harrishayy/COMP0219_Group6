# Known compliance gaps

Audit of the repository against [`project-brief-part1.pdf`](project-brief-part1.pdf), 27 October 2026. Each
item says what is wrong, where it lives, what it costs against the brief, how to see it, and the fix. None
of them are fixed yet; this file is the record, not a changelog.

The signal path itself is sound and is **not** listed here: `F = (counts − lc_offset) × lc_n_per_count`
(`load_cell.h`) feeds `v = √(sqrt_k·(F − sqrt_x0)/ρ)` with `sqrt_k = 2/(Cd·A)`
(`airspeed_models.cpp`), i.e. the exact inverse of `F = ½ρ·Cd·A·v²`, then median-3 → lead → EMA or
Butterworth → clamp at 0 (`airspeed.cpp` `tick()`). `calib/models.py` mirrors it and
`tests/test_chain.py` holds the mirror to a literal port of the C++. The estimate runs entirely on the
Nucleo, the stream is 200 Hz of one float per line at 115200 baud, and `GET` gives the calibration file
on demand. All of that meets the brief.

## 1. The default PlatformIO env builds the wrong transducer

`firmware/platformio.ini:10` sets `default_envs = airspeed`, and `[env:airspeed]`
(`firmware/platformio.ini:31`) carries no `-DAIRSPEED_METHOD`; the line that would set it is commented out
at `firmware/platformio.ini:35`. `firmware/include/calibration.h:18` then falls through to
`#define AIRSPEED_METHOD METHOD_CUP_ENCODER`.

**Costs:** the brief's code submission is a *buildable* PlatformIO project. Cloning it and running
`pio run -t upload` flashes the cup/encoder firmware, which never reads the load cell. Only
`-e airspeed_drag_plate` is correct, and that is documented in `CLAUDE.md` but enforced nowhere.

**See it:** `pio run -d firmware -t upload`, then `GET` over the monitor — `"method":"cup_encoder"`.

**Fix:** `default_envs = airspeed_drag_plate`, or give `[env:airspeed]` the
`-DAIRSPEED_METHOD=METHOD_DRAG_PLATE` flag.

## 2. The preferred amplifier is the 10 SPS one

`firmware/include/board_config.h:51` sets `kLoadCellPrefer = LoadCellFrontend::kWeightI2c`. With both
amplifiers on the bus the M5Stack Weight I2C unit (HX711, 10 conversions/s) wins over the NAU7802
(320 SPS) that the brief lists as primary hardware. `read_feature()` (`airspeed.cpp:160`) polls once per
200 Hz tick and holds the last conversion, so each force value is repeated for about 20 ticks.

**Costs:** up to 100 ms of quantised staleness on exactly the steps and ramps the MSE rank is computed
over. The lead compensator makes it worse: at 200 Hz on a 10 Hz staircase it turns every conversion
boundary into a spike, the failure mode the comment at `airspeed.cpp:220` describes for rotors. With the
HX711 in use, keep `lead_tau` at 0.

**See it:** `GET` reports `lc_frontend` and `lc_sps`.

**Fix:** prefer `kNau7802`, or unplug the Weight unit. Either way, re-tare and re-scale — the two front
ends give different raw counts.

## 3. No fitted calibration is committed

`firmware/include/calibration_generated.h` does not exist and is not in `.gitignore` either, so the repo
ships the placeholders in `calibration.h`:

| Constant | Shipped | Should be about |
| --- | --- | --- |
| `CAL_SQRT_K` (`calibration.h:38`) | `1.0f` | ~171 for a 100 cm² plate at Cd 1.15 |
| `CAL_SQRT_X0` (`calibration.h:41`) | `0.0f` | the measured zero-flow force F₀ |
| `CAL_LC_N_PER_COUNT` (`calibration.h:49`) | `1.0e-5f` | ~9.1e-6 for the 1 kg cell at gain 128 |

**Costs:** a flash from a clean clone reads about 0.76 m/s where the true speed is 10 m/s.
`calibration/exports/` and `calibration/runs/` are gitignored and empty, so there is also no recorded
calibration evidence kept anywhere in the repo.

**Fix:** `python -m calib fit … --install` writes `calibration_generated.h`; commit it, and keep one
exported `calibration_*.json` with its fit metrics alongside.

## 4. Firmware and PC defaults disagree

| Parameter | Firmware (`calibration.h`) | PC (`calib/models.py`) |
| --- | --- | --- |
| `sqrt_k` | `1.0f` (:38) | `171.0` (:59) |
| `lc_n_per_count` | `1.0e-5f` (:49) | `2.0e-5` (:62) |
| `filter_kind` | `0.0f`, Butterworth (:57) | `1.0`, EMA (:63) |
| `ema_alpha` | `1.0f`, pass-through (:63) | `0.2` (:64) |

**Costs:** the dashboard pulls live values with `GET`, so this stays hidden in normal use, but
`calib fit` on a CSV without `--params` starts from a different model than the board runs. The PC default
`lc_n_per_count = 2.0e-5` also sits outside the plausibility band the dashboard computes for this cell
(4.6e-6 to 1.1e-5 N/count, `load-cell.js` `SCALE_RANGE`), so the toolkit's own default would trip its own
warning.

**Fix:** make `PARAMS` defaults the same values as the committed `calibration_generated.h`.

## 5. No mechanical design is captured

The plate area lives only in browser `localStorage` under `calib.plateAreaCm2`, defaulting to 100 cm²
(`js/sensors/airspeed.js:23`). Nothing in version control records the plate dimensions, the lever arm,
the mount, how the device attaches to the 30 cm square mounting plate, or that it fits the
30 × 30 × 30 cm envelope. `docs/design.md` exists but is empty (0 bytes).

**Costs:** the envelope and the mounting plate are hard constraints in the brief. The implied-Cd sanity
check in the curve-fit step (it warns outside 1.1–1.3) is meaningless without a recorded plate area, and
the area cannot be reproduced on another machine or by another team member.

**Fix:** write the geometry into `docs/design.md` and take the plate area from there rather than from
`localStorage`.

## 6. The cell capacity deviates from the brief

`js/sensors/load-cell.js:11` assumes a 1 kg bar cell (`CELL.capacityG = 1000`); the brief lists a 5 kg
load cell with the Qwiic Scale.

The deviation is defensible — drag on a 100 cm² plate is about 2.8 g at 2 m/s and 70 g at 10 m/s, so a
1 kg cell gives roughly five times the resolution — and `info.brief` in that module already says so. But
it is a departure from the specified hardware that has to be justified rather than left implicit, and the
1 kg figure is hardcoded in the dashboard with no cross-check against the firmware or `board_config.h`.

**Fix:** state the cell actually fitted in `docs/hardware.md`, and derive `CELL.capacityG` from one place.

## 7. The load cell has no wiring diagram or datasheet tab

`calibration/app/static/js/wiring/parts/` holds only `lm35.js` and `mcp9808.js`, and `docs/datasheets/`
only `lm35.pdf` and `mcp9808.pdf`. The Load cell tab has neither a Wiring nor a Datasheet sub-tab, while
two thermometers have both.

**Costs:** the load cell is the transducer the whole design rests on, and its four-wire bridge
(red E+, black E−, white A−, green A+) plus the 5 V supply requirement for the Weight unit are the
wiring most likely to be got wrong. The recovery notes in `CLAUDE.md` and `docs/hardware.md` carry the
detail, but the bench UI does not.

**Fix:** the `wiring-diagram` skill (`.claude/skills/wiring-diagram/SKILL.md`): a part spec in
`js/wiring/parts/`, the NAU7802 and cell PDFs in `docs/datasheets/`, then `pnpm check:wiring`.
