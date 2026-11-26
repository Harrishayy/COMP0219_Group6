# Contactless Airspeed Sensor (COMP0219, Stage 2)

A ping pong ball hangs on a straw from a hinge between two pillars. The wind pushes it downstream. The Raspberry Pi
5's HQ camera watches it side-on, and the Pi turns **how far the ball has moved from where it hangs in still air**
into m/s. It streams one float per line at 200 Hz over 115200 baud.

- [`docs/brief.md`](docs/brief.md): the brief as a requirements table, and the questions still open with the
  module team. The PDF is [`docs/project-brief-part2.pdf`](docs/project-brief-part2.pdf).
- [`docs/algorithms.md`](docs/algorithms.md): the processing chain and why each piece was chosen, with the
  simulator's numbers. Read it before changing an algorithm.

## Is it real time, and where do we see it?

Yes. Every camera frame (120 a second) is processed on the Pi as it arrives, in well under a millisecond. A
Kalman filter predicts the speed at every 5 ms tick between frames, and the Pi sends that line straight away. You
see it in three places:

| Where | What | When |
| --- | --- | --- |
| The **Wind Tunnel Logger** on the laptop | Our 200 Hz line plotted live against the pitot, and saved for marking | The demo, and every tunnel run |
| The Pi's terminal (Pi Connect remote shell) | `aircam run` prints the speed, the frame rate, how often the ball was found, and the latency, once a second | Always |
| `aircam preview`: a window on the Pi's desktop, or `--web` in the laptop's browser | The live camera feed with the tracking drawn on it (below), and the speed, angle and a 10 s chart | Setting up and checking. Never during a scored run: drawing makes the camera drop frames |

The screen simulator below also shows the Pi's speed against the truth live, in a strip chart.

## On the Pi

Once, from a Pi Connect remote shell:

```bash
git clone https://github.com/Harrishayy/COMP0219_Group6.git ~/sensors   # or: cd ~/sensors && git pull
cd ~/sensors/cw2 && ./aircam install        # apt packages, the week 6 venv, the serial port, the service
sudo reboot                                  # only the first time (it turns on the serial port)
```

Then, from anywhere:

```bash
aircam setup        # fan off: finds the ball, saves where it rests (rerun whenever the camera or rig moves)
aircam preview      # live feed with the tracking drawn on it (Pi desktop; q quits)
aircam preview --web    # the same in the laptop's browser: open the address it prints
aircam check        # 10 s of tracking, then a report: frames with the ball, noise in mm and m/s
aircam run          # stream to the laptop; prints the speed once a second; Ctrl+C stops
aircam start        # the same in the background, so closing the browser tab does not stop it
aircam stop
aircam cal          # the calibration file, on demand (a demo deliverable)
aircam set k=6.9 arm=141                     # what `aircam fit` tells you to type
aircam pluck --write                         # flick the ball in still air: its frequency and damping
aircam update                                # git pull
```

Everything lives in `~/aircam.json`; `aircam set key=value` changes any of it. The shortcuts are `k`, `arm`,
`port`, `exposure`, `gain`, `fps`, `sigma` and `q`.

**Wiring to the laptop.** The Pi 5's USB-C port is its power input, so the lines go out of the Pi's own serial port
through a 3.3 V USB–UART adapter:
- Pi header pin 8 (GPIO14 TXD) goes to the adapter's RX.
- Pin 6 (GND) goes to the adapter's GND.

The laptop sees the adapter as `/dev/tty.usbserial-…` (or a COM port); pick it in the logger.
`aircam install` turns the port on (`dtparam=uart0=on` → `/dev/ttyAMA0`).

## How well is it tracking? (the real ball, no tunnel needed)

**Watch it live.** Run `aircam preview`. Use the Pi's desktop or Pi Connect screen sharing, or run
`aircam preview --web` in any shell and open the printed `http://<pi>:8080/` on the laptop (same network). On every
shown frame:

| Mark | Meaning |
| --- | --- |
| Yellow ring | Where the tracker searched this frame: 2 ball radii around where the ball was the frame before |
| Green circle | The ball it found. Red means it lost the ball; the circle stays where it was last seen |
| Blue cross | The ball's rest position from `aircam setup`; the green line from it to the ball is the reading |
| Orange trail | Where the ball has been over the last half second |
| Top left | Speed, the reading in mm, the angle, the frame rate, tracking time per frame, and how much of the last second had the ball |
| Bottom | The last 10 s of speed |

Push the ball by hand, or blow it with a fan or hairdryer, and watch the green circle stay on it. Hold it at a
measured angle with a protractor and compare the angle shown: that checks the geometry. Add `--udp <laptop>` to
also feed the screen simulator's panel.

**Get numbers.** `aircam check` tracks for 10 s and reports:
- the frame rate and how many frames had the ball;
- the longest run of frames without it;
- tracking time per frame;
- where the ball sat (mm, angle, m/s on the current map);
- the camera's own noise: frame to frame, in mm and in m/s at 2, 6 and 10 m/s;
- any sudden jumps (reflections).

Run it with the fan off for the noise floor, and with a fan blowing to see how the ball is followed. It saves
`~/aircam-check.png` with the ball circled. If something is wrong, it says what to fix.

**How the ball itself behaves.** `aircam pluck` measures how fast the ball swings and how quickly it settles.

**The real score** needs the pitot: a tunnel run through the logger, then `aircam fit` (next section). It prints the
logger's MSE before and after calibration.

## Calibrating

Calibration uses only what the laptop records: the map the run used can be undone from the streamed speeds, so
nothing needs copying off the Pi.

1. On the Pi, `aircam run`. Its first line shows the map it is using (`k` and `arm`).
2. Record a run with the Wind Tunnel Logger. The demo protocol is best: it covers 2–10 m/s.
3. On the laptop, run the fit. It prints the line to type on the Pi.

   ```bash
   cd cw2 && ./aircam fit ../cw1/data/tunnel/run_…_aligned.csv --k 8.6 --arm 150
   ```

   It also prints the logger's MSE before and after, and the time shift it found (camera latency against the
   pitot's lag).
4. On the Pi, `aircam set k=… arm=…`.
5. Rehearse the demo in both orders.

## Rehearsing without the tunnel: the screen simulator

The laptop draws the pendulum full-screen. It swings with real physics: drag on the ball and the straw, gravity,
inertia and turbulence. The Pi's camera films the screen and runs exactly what it runs in the tunnel. The laptop
receives the Pi's lines over Wi-Fi, shows them next to the true wind speed, and scores and saves each run the way
the logger does.

The simulator is not a shortcut. Its rig (straw length, masses, drag coefficients, turbulence) is hidden and drawn
from a seed (`--rig`). Its physics is richer than the Pi's model. Its picture is a shaded object, not a white dot.
So the Pi has to find the ball and calibrate against recorded truth, just as in the tunnel.

```bash
cd cw2 && ./aircam install           # once: .venv with numpy, pyserial, pygame
./aircam screen                      # full screen (--windowed for a window); --mode manual to start in MANUAL
```

The pendulum fills the left of the screen. The right is the **side panel**:
- the buttons;
- big **Wind (truth)** and **Pi estimate** numbers, the difference, and the air at the ball (turbulent);
- the Pi's lines per second, and the running MSE, bias and n for the run;
- a 20 s chart of the truth against the Pi;
- the command to type on the Pi.

There are two modes:
- **DEMO** replays Part 1's graded protocol (`cw1/docs/demo-protocol.md`).
  - Protocol 1: full speed, L2, L1, L2, full (15 s each), then fan off and coast.
  - Protocol 2: 3 s on/off pulses, then coast.
  - Its diagram is drawn in the panel with a cursor moving along it, and the Pi's line drawn over it.
  - Pick the order (1 then 2, or 2 then 1) and the full speed.
- **MANUAL** has speed buttons (0, 2–10 m/s and ±0.5). Use it to see how well the ball is tracked at a given speed or
  through a step you choose. The air follows the setting with the tunnel's lag: 1.35 s spinning up, 4.85 s coasting to
  rest, 0.5 s between levels.

| Button | Key | Does |
| --- | --- | --- |
| DEMO / MANUAL | TAB (or D / M) | Mode |
| START / STOP | SPACE | DEMO: play the protocol (STOP aborts). MANUAL: record (STOP ends); the speed buttons work either way |
| 1 then 2 / 2 then 1 | 1 / 2 (DEMO) | Protocol order |
| full − / + | Up / Down (DEMO) | Full speed ±0.5 m/s |
| 0, 2…10 | 0, 2–9, and 1 for 10 (MANUAL) | Set the wind speed |
| −0.5 / +0.5 | Up / Down (MANUAL) | Nudge the wind speed |
| PLUCK | P | Flick the ball from 25° in still air (for `aircam pluck`) |
| QUIT | Q / ESC | Quit (a run in progress is saved first) |

1. Point the Pi's camera at the left of the screen so the pendulum fills the frame. Keep the side panel out of the
   frame. Turn off True Tone, Night Shift and auto-brightness.
2. On the Pi, with the wind at 0: `aircam setup`.
3. On the Pi: `aircam run --udp <laptop address>`. The address is in the panel.
4. Press SPACE. Every run that ends is saved in `cw2/data/sim/` and scored; the panel shows the fit command.
5. Calibrate. The Pi's map comes with the run, so no `--k`/`--arm` is needed:

   ```bash
   ./aircam fit data/sim/sim_…_aligned.csv
   ```

   Then type the `aircam set …` line it prints on the Pi.
6. Switch the order and run DEMO again: that run's score is the calibrated sensor's.

`--turb 0.04` makes the air rougher. `--display-lag` takes off the screen's own lag (tens of ms that the tunnel
does not have), once you have measured it.

## Laptop-only design tools

```bash
cd cw2/pi
../.venv/bin/python -m pytest -q tests             # every algorithm, no camera needed
../.venv/bin/python -m aircam sim --turb 0.015     # the chain without a camera, calibrated then scored
../.venv/bin/python -m aircam compare              # estimators × frame rates
```

## Layout

| Path | What it is |
| --- | --- |
| `aircam` | The one command: `install`, `start`/`stop`/`logs`, `update`, and everything in `pi/aircam/` |
| `pi/aircam/` | The sensor in Python (see the module table below) |
| `pi/tests/` | Tests for everything except the real camera |
| `pi/config.example.json` | The calibration file's shape: camera, ball, map, estimator, output |
| `pi/deploy/aircam.service` | systemd unit: the remote interface starts and stops it and does nothing else |
| `data/sim/` | Screen-simulator runs, in the logger's format (not committed) |
| `prototype/` | The physical prototype: CAD, photos, dimensions |
| `docs/tutorials/` | The course's week 6 Pi setup and Picamera2 lab, copied unmodified |

| Module | What it does |
| --- | --- |
| `features.py` | Frame → how far the ball is from rest (mm); setup finds the ball |
| `models.py` | mm → m/s: v = k·√tan θ, with θ from the chord |
| `estimator.py` | Readings at 120 fps → a speed at every 5 ms tick (Kalman; gates glints) |
| `stream.py` | The output lines (serial, UDP or stdout) and the 5 ms ticker |
| `capture.py` | The camera, through Picamera2 |
| `app.py` | `run` (two threads), `setup`, `preview`, `check`, the pluck recording |
| `view.py` | The live view: the feed with the tracking drawn on it, in a window or a browser (MJPEG) |
| `fit.py` | Calibration from a logger or simulator run; the pluck fit |
| `demo.py` | The graded demo profile and the logger's scoring |
| `rig.py` | The pendulum's physics, for the simulators |
| `screen.py` | The screen simulator (laptop) |
| `sim.py` | The whole chain without a camera |

## Working rules

- **The Pi is the truth.** Everything runs on it, per the brief. The laptop only records (the logger, or the screen
  simulator), fits and previews.
- **Score as the demo is scored.** The logger pairs each pitot sample with the nearest line by laptop arrival
  time within 150 ms, and the demo tests only 2–10 m/s. Use `demo.score`, never a hand-rolled MSE.
- **Never emit a 7-field line.** The logger reads the last comma field as the speed, and a 7-field line as a
  GIGA pitot line. `stream.line` enforces it. The `# aircam …` lines are skipped by the logger.
- **Measure, then choose.** Base a design choice on an `aircam sim` or screen-simulator run with the prototype's
  measured numbers, and put the numbers in `docs/algorithms.md`.
- **Per frame, NumPy on a window around the ball only.** No preview window while measuring.
