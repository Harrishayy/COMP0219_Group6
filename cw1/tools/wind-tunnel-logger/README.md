# Wind Tunnel Logger (course tool, vendored)

The COMP0219 TA's logger for the tunnel sessions: it plots the board's speed live next to the tunnel's own
reading (ground truth) and saves both as CSV for the report and for assessment. `wind_tunnel_logger/` is
v1.0.1 from <https://github.com/Dkaka/COMP0219_Tutorials/releases/latest>, copied unchanged from the
wheel. 1.0.1 reads the white tunnel's working pressure cell by itself (cell 1 broke; cell 2 now gives the airspeed):
at the tunnel the window title says 1.0.1 and the status line ends in "(pressure cell 2)". Do not edit it: the
module team scores runs saved by this program. To update, unzip the new wheel and copy its package folder over this
one.

User manual: <https://github.com/Dkaka/COMP0219_Tutorials/releases/latest/download/Wind_Tunnel_Logger_User_Manual.pdf>

## Run it

```bash
pnpm logger          # first run installs requirements.txt (PySide6, hidapi, …) into .venv
pnpm logger --demo   # simulated tunnel and simulated board, no hardware
```

`run.py` changes where runs are saved (`data/tunnel/` in this repo) and adds the dashboard's board relay to the
port list. Each run is three files:

| File | What it holds |
| --- | --- |
| `<run>_aligned.csv` | each tunnel sample paired with the nearest board sample (within 150 ms): `timestamp_epoch, ground_truth_mps, student_mps`. The file the module's `Score.py` reads |
| `<run>_vdas.csv` | every ground-truth sample, with the raw and zero-corrected pressure (Pa) |
| `<run>_student.csv` | every board sample at 200 Hz |

Compare a run against the tunnel (MSE, bias, σ and drift over the run) in the dashboard's Wind speed section,
or with `cd calibration && ../.venv/bin/python -m calib tunnel` (latest run) or `… tunnel <run>`.

## Before a tunnel session

- One program owns the board's port. Either stop `pnpm dev` and pick the board's own port, or keep `pnpm dev`
  running and pick *Board via the dashboard (pnpm dev)*: the dashboard copies the board's stream to the logger, and
  each program records on its own. The GIGA is shared the same way: *GIGA pitot tube (…/.giga-relay)* is picked as
  the ground truth by itself (run.py), and the logger drives it as if it were plugged in.
- The logger reads the last number on each line as the speed. The board boots in `MODE V` (one speed per line).
  In `MODE D` its rows end in `v` for the same reason, so either mode logs the right number.
- Set *Ground truth* to the tunnel you are on: the white TecQuipment tunnel is *Wind tunnel* (its VDAS box over
  USB; close TecQuipment's VDAS software), the wood tunnel is *GIGA pitot tube*. *Simulated (bench test)* checks
  the connection and the 200 Hz rate at home; its speed is made up.
- Enter the room's pressure and temperature in the window: the logger's ground truth uses them for ρ.
