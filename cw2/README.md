# Contactless Airspeed Sensor (COMP0219, Stage 2)

A Raspberry Pi 5 with the HQ camera watches a passive instrument in the wind tunnel's airflow and estimates
2–10 m/s on board. It streams one float per line at 200 Hz over 115200 baud. The camera runs at 120 fps; a
Kalman filter upsamples to 200 Hz and rejects bad frames.

- [`docs/brief.md`](docs/brief.md): the brief as a requirements table, and the questions still open with the
  module team. The PDF is [`docs/project-brief-part2.pdf`](docs/project-brief-part2.pdf).
- [`docs/algorithms.md`](docs/algorithms.md): the processing chain and why each piece was chosen, with the
  simulator's numbers. Read it before changing an algorithm.

## Run it

```bash
cd cw2/pi
../.venv/bin/python -m pytest -q tests                  # laptop: every algorithm, no camera needed
../.venv/bin/python -m aircam compare                   # estimators × frame rates on the simulated graded demo
../.venv/bin/python -m aircam sim --fn 1.2 --zeta 0.08  # one run; every SimConfig field is a flag
python -m aircam run --config ~/aircam.json --port /dev/ttyAMA0 --record ~/aircam-runs   # on the Pi
python -m aircam cal --config ~/aircam.json             # the calibration file, on demand
python -m aircam fit <session> <run>_aligned.csv --write ~/aircam.json
python -m aircam pluck <session>                        # fn and ζ from a free decay
```

On a laptop, `.venv` holds numpy, pyserial and pytest: `python3 -m venv .venv && .venv/bin/pip install -r
pi/requirements.txt`. On the Pi, use the week 6 setup instead: apt packages (`python3-picamera2 python3-numpy
python3-serial`) in `python3 -m venv ~/comp0219 --system-site-packages`. Never pip-install numpy or opencv
there.

## Layout

| Path | What it is |
| --- | --- |
| `pi/aircam/` | The sensor, in Python, run on the Pi (see the module table below) |
| `pi/tests/` | Tests for everything except the camera |
| `pi/config.example.json` | The calibration file's shape (camera, feature, map, estimator) |
| `pi/deploy/aircam.service` | systemd unit: the remote interface starts and stops it and does nothing else |
| `prototype/` | The physical prototype: CAD, photos, dimensions (to come) |
| `docs/tutorials/` | The course's week 6 Pi setup and Picamera2 lab, copied unmodified |

The modules in `pi/aircam/`:

| Module | What it does |
| --- | --- |
| `features.py` | Frame → reading |
| `models.py` | Reading → m/s |
| `estimator.py` | Readings → a 200 Hz speed |
| `stream.py` | The output lines and the 5 ms ticker |
| `capture.py` | The camera, through Picamera2 |
| `app.py` | The two-thread runner |
| `fit.py` | Calibration fits |
| `demo.py` | The graded demo and the logger's scoring |
| `sim.py` | The whole chain, simulated |

## Working rules

- **The Pi is the truth.** Everything runs on it (brief); the laptop only records with the Wind Tunnel Logger
  (`pnpm -C ../cw1 logger`, the same logger as Part 1).
- **Score as the demo is scored.** The logger pairs each pitot sample with the nearest line by laptop arrival
  time within 150 ms, and the demo tests only 2–10 m/s. Use `demo.score`, never a hand-rolled MSE.
- **Never emit a 7-field line.** The logger reads the last comma field as the speed, and a 7-field line as a
  GIGA pitot line. `stream.line` enforces it; keep the speed last in any debug row.
- **Measure, then choose.** Base each design choice (fps, estimator, tuning) on an `aircam compare` or `sim` run
  with the prototype's measured fn, ζ, σy and latency, and put the numbers in `docs/algorithms.md`.
- **Per frame, NumPy on an ROI only**, under the frame time (8.3 ms at 120 fps). No preview window while
  measuring: it makes the camera drop frames.
