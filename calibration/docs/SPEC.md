# Calibration toolkit — spec

The PC-side toolkit for tuning the airspeed sensor against the Dwyer 616KD + pitot ground truth (GT).
The sensor is a **drag plate**: plate force on the NAU7802 load cell, `F = ½ρ·Cd·A·v²` (model id `sqrt`).
Everything here is **calibration tooling only**. The brief requires the final estimate to run on the
Nucleo, so the output of this toolkit is a parameter set (JSON + C header + `SET` commands) that the
firmware applies. Python (`calib/models.py`) and the dashboard re-implement the *same* maths as
`firmware/lib/airmath` + `firmware/src/apps/airspeed.cpp` `tick()` so PC previews match the board.

## 1. Serial contract with the Nucleo (115200 baud) — owned by firmware/ (repo session)

Matches `firmware/` exactly. Lines are newline terminated (`\r\n` tolerated), commands case-insensitive.

| Stream | Line | Notes |
|---|---|---|
| Velocity (default, `MODE V`) | `5.123` | one float per line, m/s, 200 Hz. What the marking script reads. |
| Debug (`MODE D`) | `<n>,<v>,<feature>,<rho>,<temp_c>,<pressure_pa>` | `n` sample counter (gap > 1 = dropped tick); `feature` = drag-plate force F in N (after tare and load-cell scale). |
| Header | `#cols,n,v,feature,rho,temp_c,pressure_pa` | printed on every `MODE D` and on `HDR`; names the debug columns (parse it, don't hard-code). The PC sends `MODE D`, `HDR`, `GET` after every (re)connect. |
| Any `#` line | informational, never data | `#cal {json}`, `#ok name=value`, `#err reason`, `#env ...`, `#stats ...` |

`#env` (reply to `ENV`): `#env t_air=… mcp9808=… sht40=… stts22h=… lps22df=… tmp36=… lm35=… lm35_v=… rh=…
p=… rho=…`, `nan` for a missing sensor. Each thermometer is reported raw. `t_air` is the one `temp_source` picks
plus its `<name>_offset`, and is the temperature in `rho` (and `temp_c` in debug and `#cal`). The thermometers
are never averaged; with the chosen one missing `t_air` is `nan` and `rho` uses 15 °C.

Commands PC → Nucleo: `SET <name> <value>` (→ `#ok name=value` / `#err reason`, RAM only),
`GET` (→ `#cal {json}` with every live param + method, build date, rho, temp, and on the drag plate
`lc_frontend` (`weight_i2c` | `nau7802` | `none`) and `lc_sps` (its conversions per second)), `MODE V|D`, `TARE`
(load-cell zero, non-blocking), `HDR`, `ENV`, `STATS`, `HELP`. `#cal` `method` must be `drag_plate`; anything
else is logged as an error (flash the drag-plate build).

Firmware param names (also the export keys; header macro is `CAL_<UPPER_NAME>`):
`sqrt_k`, `sqrt_x0`, `lc_offset`, `lc_n_per_count`, `lead_tau`, `filter_kind`, `ema_alpha`, `lpf_hz`,
`temp_source` (0 MCP9808, 1 SHT40, 2 STTS22H, 3 LPS22DF, 4 TMP36, 5 LM35), `mcp9808_offset`, `sht40_offset`,
`stts22h_offset`, `lps22df_offset`, `tmp36_offset`, `lm35_offset` (°C, reference minus raw).
Method is compile-time (`AIRSPEED_METHOD = METHOD_DRAG_PLATE`).

### Standalone load-cell lab firmware

`labs/loadcell` uses its own human-readable protocol at the same 115200 baud. The serial source
detects its startup/help text or a `Raw: N counts (uncalibrated)` / `Weight: G g  Raw: N` line.
These samples appear only as `frame.raw.lab_raw` (counts) and `frame.raw.lab_weight` (grams,
`null` until calibrated); they never supply a force feature, velocity, or computed airspeed.
`telemetry.loadcell_lab` is `null` for other sources, otherwise it contains `raw_counts`, `weight_g`,
`tared`, `calibrated`, `scale_factor` (counts/g), `zero_offset` (counts), `tare_seq`,
`calibration_seq`, `error_seq`, and `last_error`. Unknown values are `null`.

The website sends `t` unloaded, then `k <positive known grams>` with that mass applied. Success
requires the board's `Tare: N counts` or `Calibration: F counts/g` acknowledgement, tracked through
the respective monotonic sequence counters. `ERROR: ...`, `First remove ...`, and `Use k ...`
rejections increment `error_seq` and expose their reason in `last_error`; informational replies also
reach the log. `h` requests help/status (`Scale factor: F counts/g; tared=0|1; calibrated=0|1`).
`GET` maps to `h`, `MODE D`/`HDR`/`ENV` are harmless no-ops after detection, and unsupported
competition commands are rejected locally. Opening an unknown board retains the competition
startup probes until its protocol is identified. Calibration is RAM-only: reconnect, startup,
sensor reinitialization, or a return from calibrated weights to uncalibrated raw output clears
calibration and latest weight. Sequence counters remain monotonic for each serial-source instance.

## 2. Ground truth adapter

The GT script (to be supplied) is wrapped by `calib.sources.GroundTruthSource`. Three ways to plug it in:

1. **Python callable** — `--gt-module path/to/gt.py:read_sample` where `read_sample()` returns
   airspeed m/s (or `(t, v)`), called in a thread.
2. **Subprocess stdout** — `--gt-cmd "python their_script.py"`; every stdout line containing a float
   (last float on the line) is a sample.
3. **Serial** — `--gt-port /dev/tty.xxx` with a float per line.

If the script prints pitot differential pressure in Pa instead of m/s, add `--gt-pressure` (websocket:
`connect.gt_pressure`): each sample becomes `v = √(2·max(dp, 0)/ρ)` with the board's
latest ρ (`rho_ref` until one arrives).

All samples are timestamped on the PC with `time.perf_counter()` on arrival.

## 3. Model (force → airspeed), mirroring `firmware/lib/airmath`

`rho` comes from the debug stream column (firmware computes it from temp + baro). If absent, `rho_ref`
(default 1.204). The PC never re-derives the feature: the feature column is what the model consumes.

Static map `speed_sqrt_density`: `arg = sqrt_k*(F - sqrt_x0)/rho`; `arg > 0 ? sqrt(arg) : 0`, i.e. the drag
equation solved for v with `sqrt_k = 2/(Cd·A)` and `sqrt_x0` = F₀, the force at zero flow (N).

Output chain after the static map, `fs = 200`, exactly as `tick()`:

1. `Median3`: the first three samples pass through (`count_ < 3`), then the median of the last three.
2. `LeadCompensator` (`lead_tau`, 0 = off): `y_k = x_k + lead_tau * fs * (x_k - x_{k-1})`; the first sample
   passes through.
3. Smoother, chosen by `filter_kind` (`>= 0.5` → EMA, otherwise, incl. NaN → Butterworth). The toolkit
   default is EMA; an unconfigured board starts on Butterworth. Changing `filter_kind` resets BOTH smoothers,
   so the next sample re-primes the selected one. The `dynamics` fit tunes the active one's parameter with
   `lead_tau`; export and push write `filter_kind` plus that parameter only.
   - `Ema` (`filter_kind` 1): `V = α·V_new + (1−α)·V_prev`, primed to its first input (`y = x`), then
     `y += α(x − y)`. `ema_alpha` clamped to `[1e-4, 1]`; NaN or `<= 0` → `1e-4`. Changing `α` alone keeps
     the state.
   - `ButterworthLowPass` `lpf_hz` (`filter_kind` 0): 2nd order, so it damps the plate's ringing better
     than the EMA for the same lag. RBJ biquad, Q = 1/√2, transposed DF-II, primed to the
   first sample (steady state), pass-through if `lpf_hz <= 0` or `>= fs/2`:
   `w0 = 2π lpf_hz/fs; cw = cos w0; α = sin w0 /(2Q); a0 = 1+α; b0 = (1-cw)/2/a0; b1 = (1-cw)/a0; b2 = b0;
   a1 = -2cw/a0; a2 = (1-α)/a0`. Prime: `s2 = x(b2-a2); s1 = x(1-b0)`. Step: `y = b0 x + s1;
   s1 = b1 x - a1 y + s2; s2 = b2 x - a2 y`. Changing `lpf_hz` re-configures and so re-primes it.
4. Clamp: `v < 0 → 0`.

The median runs *before* the lead, so a single-sample spike is removed before the lead can amplify it.

Load cell (`lc_offset`, `lc_n_per_count`) is applied on the board before the feature. It is calibrated
by the static weight routine (`python -m calib weights`): `TARE` with no load, then hang known masses
and set `lc_n_per_count_new = lc_n_per_count_old * (m g / F_measured)` from a least-squares slope.
Params in the `board` group (`lc_offset`, `lc_n_per_count`, `temp_source` and the thermometer offsets) are
mirrored from every `#cal` reply into the PC params.

Thermometers are calibrated one at a time on their dashboard tab: settled next to a reference thermometer,
`<name>_offset = T_ref − mean(raw over 30 s)`, sent with `SET` and kept by exporting the header.

## 4. Metrics (what the marking uses + diagnostics)

On samples aligned to the GT time base: `mse`, `rmse`, `bias = mean(est - gt)`, `sigma = std(est - gt)`,
`mae`, `max_abs`, `r2`, `lag_ms` (cross-correlation peak of est vs gt), `sensor_hz`, `gt_hz`, `drops`
(gaps > 1.5 × nominal period). Step metrics per detected GT step: rise time 10–90 %, overshoot %.

**Diagnosis** (`metrics.diagnose`): rules that turn a disagreement with the pitot into a suggested change.
Static checks use steady samples only (`|gs[i+10] − gs[i−10]|/(20·dt) < 0.3 m/s²`, `gs` = GT through a centred
50-sample boxcar, and `gt > 1`). Findings are sorted by severity, then expected MSE gain:

| id | fires when | severity | `apply` |
|---|---|---|---|
| `static_refit` | refit of F vs ½ρv² gives `mse_new < 0.9·mse_now` and saves > 0.002 | high if `mse_now > 0.05` | `sqrt_k = 2/CdA, sqrt_x0 = F₀` |
| `scale` | steady `est/gt` ratio `r` off by > 2 % | high if > 5 % | `sqrt_k / r²` |
| `low_speed_offset` | bias(1.5–4 m/s) − bias(> 6 m/s) > 0.15 m/s | high if > 0.3 | `sqrt_x0 + ΔF₀` |
| `dead_zone` | reads 0 for > 2 % of samples with wind | high | — |
| `drift` | bias of the last third − first third > 0.1 m/s | medium | — |
| `lag` | lag > 40 ms | high if > 100 ms | `ema_alpha ×1.5` / `lpf_hz ×1.5` |
| `noise` | steady σ > 0.12 m/s (and no lag finding) | medium | `ema_alpha ×0.7` / `lpf_hz ×0.7` |
| `overshoot` | median step overshoot > 8 % | medium | `lead_tau / 2`, or Butterworth 4 Hz if no lead |
| `nonlinear` | bias spread > 0.2 m/s over ≥ 3 of the bands 2–4–6–8–11 m/s (> 1 s each), not with `static_refit`/`low_speed_offset` | info | — |
| `rho` | median ρ outside 1.10–1.30 kg/m³ | high | — |
| `gt_noise` | pitot σ (vs its 1 s mean) > 0.15 m/s below 3 m/s | info | — |

## 5. Calibration file

`calibration_<timestamp>.json`:

```json
{ "model": "sqrt", "method": "METHOD_DRAG_PLATE", "params": {"sqrt_k": 171.4, "sqrt_x0": 0.0021, "lc_offset": 84212.0, "lc_n_per_count": 2.07e-5, "filter_kind": 1.0, "ema_alpha": 0.2, "lead_tau": 0.0},
  "fit": {"method": "robust_lsq", "window_s": 60, "n": 12000},
  "metrics_before": {...}, "metrics_after": {...},
  "created": "2026-10-15T12:00:00Z" }
```

Exported alongside a header (`#define CAL_<UPPER_NAME> <value>f` per param) and a `.cmd` file of `SET` lines.
`CAL_FILTER_KIND` plus the active smoother's parameter (`CAL_EMA_ALPHA` or `CAL_LPF_HZ`), never `rho_ref`;
`lc_offset` is in the header but never in `SET` (a push must not undo a TARE).
The header export target is `firmware/include/calibration_generated.h` (it overrides
`firmware/include/calibration.h` defaults); the toolkit writes a copy to `calibration/exports/` and only
writes into `firmware/include/` when `--install` is passed.

## 6. Live app websocket protocol (`ws://localhost:8765/ws`)

Server → client JSON messages:

- `hello`: `{type, mode: "live"|"replay"|"sim", model: "sqrt", models: {sqrt: {label: "Drag plate", methods, fit_params, params: [{name, value, min, max, step, unit, desc, group}]}}, params: {name: value}, channels: [..], window_s, profiles, firmware_params, fit_methods, telemetry, labels: {sqrt: "Drag plate"}, method: "drag_plate"|…|null, gt_pressure: bool, source: "sim"|"serial"|"replay", sources: {sensor, gt}, runs_dir, sim: {profile, fan: "on"|"off", load_g}|null}`. `group` is `static | dynamics | environment | board`. A fresh `hello` is broadcast to every client after a successful `connect`: treat it as "reset everything".
- `frame` (~20 Hz): `{type, t: [s], gt: [m/s], est: [m/s], dev: [m/s|null], raw: {feature: [..], rho: [..], temp_c: [..], pressure_pa: [..]}, telemetry: {sensor_hz, gt_hz, drops, rho, temp_c, pressure_pa, port_sensor, port_gt, recording, method}}`.
  Rows are on the sensor time base; `gt` is interpolated onto it (rows are held back until GT has caught up).
  **`replace: true`** means "discard your buffer and use this": sent to a new client and after any param
  change / fit, carrying the last 60 s at full rate (`decimated: 1`). Normal frames have
  `replace: false, decimated: 1` and are appended. telemetry also has `bad_lines, sensor_status, gt_status,
  sensor_connected, gt_connected, profile, auto_push, board, env` (`board` = last `#cal` JSON); `recording` is the
  file name or null. `env` is the board's last `ENV` reply, which the server requests every second (`#env` lines
  are parsed, not logged): `{t_air, mcp9808, sht40, stts22h, lps22df, tmp36, lm35, lm35_v, rh, p, rho,
  age_s}` (`lm35_v` is the LM35 output in V, reported even when out of range), a missing sensor is `null`; `env`
  is null until the first reply. The simulator answers `ENV` too.
  `board_state` / `board_detail` say where the real board stands, so the dashboard never shows silence or the
  simulator as data: `sim | replay | none | absent` (no Nucleo on USB, `--port auto`) `| closed` (cannot open the
  port) `| opening | silent` (port open, no line for 2 s) `| no_data` (lines but no readings, e.g. `MODE V`)
  `| streaming`, with a sentence for the operator in `board_detail`.
- `telemetry` (every 0.5 s while no `frame` is sent: no board, a silent board): `{type, telemetry}`, the same
  telemetry object as in `frame`. With no board plugged in, this is the only stream: no frames are invented.
- `reload` (live reload, `calib serve --live-reload`, which `pnpm dev` passes): `{type, files: [names], css_only}`
  when a file under `app/static/` or `docs/datasheets/` changes. Swap the stylesheets if `css_only`, else reload
  the page. `hello` carries `server_id` (new on every restart) and `live_reload`: a page that sees `server_id`
  change with `live_reload` on reloads too, which picks up Python changes after the launcher restarts the server.
- `metrics` (~2 Hz): `{type, window_s, est: {...metrics, steps: {rise_est_s, rise_gt_s, delay_t90_ms, overshoot_pct}, bands: [{band, n, bias, sigma, rmse}]}, dev: {...metrics}|null, diagnosis: [{id, severity: "high"|"medium"|"info", title, why, action, apply: {name: value}|null, gain: number|null}]}`.
  `diagnosis` is §4's rules on the metrics window with the PC preview; send `apply` back as `set_params` to act on one.
- `fit_result`: `{type, method, params, previous, before, after, free, n, ok, message, elapsed_s, window_s}`.
  On `ok` the server has already applied `params`; send `set_params` with `previous` to undo.
- `params`: `{type, model, params}` (after any change)
- `log`: `{type, level: "info"|"error"|"warning", msg}` (export logs also carry `paths`)
- `session` (~1 Hz while recording, once more on stop with `recording: false`): `{type, recording, path,
  json_path, elapsed_s, rows, psets, pset, segments: [last ≤ 12 segment summaries, see §7], events: [last ≤ 20]}`
- `ports`: `{type, ports: [{device, description, hwid}], sensor: {port, baud, connected}, gt: {kind, value, connected}}`
  (`ports` is empty without pyserial; for sim/replay `gt.kind` is the source name and `gt.value` its label)
- `runs`: `{type, runs: [{name, csv, json, size_bytes, started, model, mode, rows, duration_s, psets, best_mse_pc}]}`,
  newest first. Session runs read their JSON sidecar; bare CSVs (`json: null`) only have `started` (file mtime)
  and `rows`; the rest are null.
- `run_summary`: `{type, name, meta, summary, segments, param_sets, events}` (the session sidecar, §7)
- `exported`: `{type, files: [paths written], installed: bool}` (after `export`)

Client → server:

- `{type:"set_param", name, value}` — PC preview updates immediately; if `auto_push` is on it also sends `SET` to the board.
- `{type:"set_params", params: {name: value}}` (bulk, e.g. undo a fit or apply a diagnosis), `{type:"reset_params"}` (defaults)
- `{type:"set_window", window_s}` (metrics/fit window, 5–120 s)
- `{type:"fit", method: "robust_lsq"|"lsq"|"dynamics", window_s, free: [names]}` (`free` optional: defaults
  are `sqrt_k, sqrt_x0`; `dynamics` = Nelder–Mead on the active smoother's parameter (`ema_alpha` or `lpf_hz`)
  and `lead_tau`. `filter_kind` is never fitted: choose it by hand.)
- `{type:"push_params"}` (SET each firmware param, then GET to confirm), `{type:"auto_push", on}`, `{type:"command", line}` (raw passthrough e.g. TARE)
- `{type:"record", on, label?}` — start/stop a recording session (§7) in `calibration/runs/`
- `{type:"note", text}` — add a note event to the running session (e.g. "fan to 8 m/s")
- `{type:"export", install?: bool}` — writes calibration JSON + header + cmd file (plus the stable
  `exports/calibration_generated.h`) to `calibration/exports/`, and `firmware/include/calibration_generated.h`
  only if `install`; replies with a `log` naming every file and an `exported` message
- `{type:"sim", profile?, fan?: "on"|"off", load_g?: number}` (simulator source only). `fan: "off"` holds the
  set-point at 0 (airspeed decays through the fan lag; GT shows only pitot noise) until `fan: "on"` resumes the
  profile. `load_g` hangs a static mass (0–5000 g) on the drag plate for the weights routine; 0 removes it.
  `TARE` (via `command`) zeroes the load cell at the current force, like the board; the server sends `GET`
  0.6 s after any `TARE` so the new `lc_offset` comes back in `#cal`.
- `{type:"ports"}` → `ports`
- `{type:"connect", source: "sim"|"serial"|"replay", sensor_port? ("auto": wait for and follow the first ST-LINK port), baud?, gt_kind?: "cmd"|"module"|"port"|"none",
  gt_value?, gt_baud?, gt_pressure?, replay_file?, speed?, profile?, seed?}` — switch data sources without
  restarting. The old sources stop, any recording is finalised (its closing `session` message is broadcast),
  buffers reset, params are kept, then a fresh `hello` goes to all clients. `serial` needs a
  sensor port and/or a GT source; `replay_file` may be a path or a file name in `runs/`. Problems (port busy,
  missing file, module not found, unknown command) are checked first and reported as
  `log {level: "error", msg: "connect failed: …"}`; the previous source keeps running.
- `{type:"list_runs"}` → `runs`; `{type:"run_summary", name}` → `run_summary` (name is a run stem in `runs/`)

## 7. Recording sessions (`calib/recorder.py: Session`)

A recording is a session: one CSV of aligned rows plus a JSON sidecar describing every parameter set
used and what changed between them. Files: `runs/run_<YYYYmmdd_HHMMSS>_<label>.csv` and the same stem `.json`
(rewritten every 2 s, so a crash keeps everything up to then).

**CSV**: one row per 200 Hz sensor sample, GT interpolated onto it (rows are held back until GT catches up):

```
t,gt,v_board,v_pc,err_board,err_pc,feature,rho,temp_c,pressure_pa,pset,sqrt_k,sqrt_x0,lc_offset,lc_n_per_count,filter_kind,ema_alpha,lpf_hz,lead_tau,rho_ref
```

`t` is PC `perf_counter` seconds; `v_board` the board's own output (`v` column), `v_pc` the PC preview with
the params in force for that row; `err_* = v_* - gt`.

**JSON**:

```json
{ "meta": {"started", "stopped", "mode", "model", "method", "port_sensor", "port_gt", "profile", "fs": 200,
           "warmup_s": 0.5, "label", "csv", "t0", "rows"},
  "param_sets": [{"pset": 0, "t_start", "t_end", "params": {...}}],
  "events": [{"t", "rel_s", "kind": "start|stop|param|fit|board|push|tare|command|note",
              "name", "from", "to", "detail"}],
  "segments": [{"pset", "t_start", "t_end", "duration_s", "n", "n_scored", "changed": [names],
                "metrics_pc": {...§4}, "metrics_board": {...§4}}],
  "summary": {"segments", "pc": {"n", "mse", "rmse", "bias", "best_pset", "best_mse"}, "board": {...}} }
```

A new pset opens on any change of params: `set_param`, `set_params`, `reset_params`, an applied fit
(`kind: "fit"`), or a board `#cal` reply that changes board-side values (`kind: "board"`).
Each changed name gets its own event with `from`/`to`. Segment metrics skip the first `warmup_s` after the
change so filter transients aren't blamed on the new values. `push`, `TARE` and other commands, and notes,
are events that don't open a pset. `python -m calib session runs/run_*.json [--png out.png]` prints the
per-pset table; `fit` and `report` read the session CSV directly. This is the only recording format:
the dashboard, the server and `python -m calib record` (headless) all write it.

