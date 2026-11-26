# Contactless Airspeed Sensor (COMP0219, Stage 2)

A ping pong ball hangs on a straw from a hinge between two pillars, and the wind pushes it downstream. The Raspberry
Pi 5's HQ camera watches it side-on, in colour. The Pi turns **how far the ball has moved from where it hangs in still
air** into m/s, and streams one float per line at 200 Hz over 115200 baud.

The design and the numbers behind it are in [`docs/algorithms.md`](docs/algorithms.md). The brief, as requirements,
is in [`docs/brief.md`](docs/brief.md).

## Install once (on the Pi)

```bash
git clone https://github.com/Harrishayy/COMP0219_Group6.git ~/sensors   # or: cd ~/sensors && git pull
cd ~/sensors/cw2 && ./aircam install    # apt packages, the week 6 venv, the serial port, the service
sudo reboot                              # first time only: turns on the serial port
rpicam-hello --list-cameras              # must list imx477; if not, power off and reseat the camera cable
```

On the laptop, `cd cw2 && ./aircam install` makes `.venv` for the simulator and the fit.

## The physical rig: `aircam real …`

Three steps, in order. Each one refuses to start until the step before it is done.

```bash
aircam real detect      # 1. fan off: finds the ping pong ball and shows it in colour, circled. Confirm it.
aircam real view        # 2. watch it track: the live colour feed with the detection drawn on it
aircam real run         # 3. stream to the laptop's Wind Tunnel Logger; prints the speed once a second
```

1. **Detect.** OpenCV finds round edges (`HoughCircles`), then keeps only those filled by one colour that their
   surroundings do not have. The image shows the ball in green with a focus reading, other candidates in amber,
   and what it turned down in red, with the reason. If the focus reading says soft, turn the lens's focus ring and
   press N until the number is smallest. It stays open until you are happy:

   | Key | Does |
   | --- | --- |
   | **Y** | Saves it |
   | **N** | Tries again with fresh frames. Move the ball, the camera or the light first if it was wrong |
   | **Click** the ball | Takes the circle there, or the blob of one colour under the click if it missed a blurred ball |
   | **Q** | Quits without saving |

   - On the Pi's desktop (or in Pi Connect screen sharing), these are keys and clicks in the window.
   - In a remote shell, it prints an address to open in the laptop's browser and asks in the shell instead: `y`, `n`,
     `q`, or the ball's position as `X,Y` (the image has a grid every 100 px).

   Only a confirmed detection is saved. Rerun detect whenever the camera or the rig is moved.
2. **View.** Add `--web` to watch in the laptop's browser instead of a window.

   | Mark | Meaning |
   | --- | --- |
   | Yellow ring | Where it searched this frame |
   | Green circle | The ball it found. Red means it lost the ball |
   | Blue cross | Where the ball rests; the green line from it to the ball is the reading |
   | Top left | Speed, mm, angle, frame rate, how much of the last second had the ball |
   | Bottom | The last 10 s of speed |

3. **Run.** It sends the speed out of the Pi's serial port through a 3.3 V USB–UART adapter.
   - Wiring: header pin 8 (TXD) goes to the adapter's RX, and pin 6 (GND) to its GND.
   - In the Wind Tunnel Logger, pick the adapter's port.
   - Ctrl+C stops it. `aircam real start` / `aircam real stop` run it as a background service instead, so closing
     the shell doesn't stop it.

Also:
- `aircam real check`: 10 s of tracking, then a report (frames with the ball, noise in mm and m/s, what to fix).
- `aircam real pluck`: how the ball swings and settles.
- `aircam real cal`: shows the calibration file (a demo deliverable).
- `aircam real set k=… arm=…`: changes it.

The calibration file is `~/aircam-real.json`.

## The screen simulation: `aircam screen` + `aircam sim …`

The laptop draws the pendulum and moves it with real physics. The Pi's camera films the screen and runs the same
tracking as the real rig. Its calibration is separate (`~/aircam-sim.json`), so rehearsing never touches the real
rig's. The simulator's rig is hidden and drawn from a seed, so the Pi has to detect the ball and calibrate from
recorded runs, just as in the tunnel.

On the laptop:

```bash
./aircam screen         # full screen; the pendulum on the left, the controls and truth-vs-Pi on the right
```

On the Pi, with its camera on the left of the laptop's screen (keep the side panel out of the frame; turn off True
Tone, Night Shift and auto-brightness):

```bash
aircam sim detect               # 1. wind at 0: find the simulated ball, confirm it
aircam sim view                 # 2. watch it track
aircam sim run <laptop address> # 3. stream to the simulator (the address is on its panel; remembered after)
```

The simulator's side panel shows the **true wind** and the **Pi's estimate** side by side, their difference, the
running MSE and a 20 s chart.

| Button | Key | Does |
| --- | --- | --- |
| DEMO / MANUAL | TAB | DEMO replays Part 1's protocol, with its diagram (full, L2, L1, L2, full, coast, pulses). MANUAL lets you set the speed |
| START / STOP | SPACE | DEMO: play the protocol. MANUAL: record |
| 1 then 2 / 2 then 1 | 1 / 2 | Protocol order (DEMO) |
| full − / + | Up / Down | Full speed (DEMO) |
| 0, 2…10, ±0.5 | 0, 2–9, 1 = 10, Up / Down | Wind speed (MANUAL). It follows with the tunnel's fan lag |
| PLUCK | P | Flick the ball in still air |
| QUIT | Q | Quit (a run in progress is saved first) |

Each run is saved in `cw2/data/sim/` in the logger's format and scored as the logger would.

## Calibrating (either rig)

Record a run that covers 2–10 m/s: the demo protocol in the tunnel with the logger, or DEMO in the simulator. Then,
on the laptop:

```bash
./aircam fit ../cw1/data/tunnel/run_…_aligned.csv --k 8.6 --arm 150   # a tunnel run: k and arm from `aircam real cal`
./aircam fit data/sim/sim_…_aligned.csv                                # a simulator run: it knows the map it ran with
```

It prints the logger's MSE before and after, and the line to type on the Pi, for example
`aircam real set k=6.9 arm=141`. Nothing needs copying off the Pi. Then rehearse the demo in both orders.

## Is it real time?

Yes. Every frame (120 a second) is processed on the Pi as it arrives, in well under a millisecond. The speed goes
out every 5 ms, predicted by a Kalman filter between frames. You see it in these places:
- live in the Wind Tunnel Logger against the pitot (what is marked);
- once a second in the Pi's terminal;
- in `aircam … view`;
- on the simulator's panel.

## Layout

| Path | What it is |
| --- | --- |
| `aircam` | The one command (`install`, `update`, `real …`, `sim …`, `screen`, `fit`) |
| `pi/aircam/` | The code. Ball detection and tracking (`features.py`), mm to m/s (`models.py`), the filter (`estimator.py`), the runner (`app.py`), the live view (`view.py`), output lines (`stream.py`), the camera (`capture.py`), calibration (`fit.py`), the screen simulator (`screen.py`, `rig.py`), the offline design tool (`sim.py`, `demo.py`) |
| `pi/tests/` | Tests, including the simulator's drawing filmed through a camera model (`test_e2e.py`) |
| `pi/config.example.json` | The calibration file's shape |
| `prototype/` | The physical rig's notes |

On the laptop: `cd pi && SDL_VIDEODRIVER=dummy ../.venv/bin/python -m pytest -q tests`.
