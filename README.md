# COMP0219 airspeed sensors (Group 6)

UCL *Sensors & Signal Conditioning*: two airspeed sensors for the course's mini wind tunnel. Both estimate
2–10 m/s and stream one float per line at 200 Hz, 115200 baud, to a virtual COM port. Both are ranked by MSE
against the tunnel's pitot, from the CSVs the TA's Wind Tunnel Logger saves.

| | Coursework 1: [`cw1/`](cw1/README.md) | Coursework 2: [`cw2/`](cw2/README.md) |
| --- | --- | --- |
| Sensing | Contact: a drag plate on a load cell | Contactless: a camera tracking a ping pong ball pendulum |
| Runs on | Nucleo-F411RE (C++, PlatformIO) | Raspberry Pi 5 + HQ camera (Python, Picamera2) |
| Brief | `cw1/docs/project-brief-part1.pdf` | `cw2/docs/project-brief-part2.pdf`, as requirements in `cw2/docs/brief.md` |
| Status | Demo done; calibration history in `cw1/docs/` | Ball-pendulum tracker and screen simulator in `cw2/`, prototype to come. Demo 11 Dec, report 16 Dec 16:00 |

Work inside one coursework at a time: `cd cw1` or `cd cw2` first. Each one's commands and paths are relative to
its own folder, and each has its own `.venv`.

## Shared between the two

- **The Wind Tunnel Logger** (`cw1/tools/wind-tunnel-logger`, unmodified; `pnpm -C cw1 logger`) records both
  courseworks' assessed runs into `cw1/data/tunnel/`.
  - It reads the last comma field of each line as the speed and skips `#` lines.
  - A 7-field line is taken for a GIGA pitot line.
  - It stamps lines with the laptop's clock on arrival, so latency is lag.
- **The graded demo protocol** (`cw1/docs/demo-protocol.md`): level steps, a coast, 3 s pulses; only 2–10 m/s is
  scored. Coursework 2's simulator (`cw2/pi/aircam/demo.py`) assumes the same protocol.
- **What Part 1 learned:**
  - The zero offset and the gain dominate the MSE.
  - Score fits on 2–10 m/s, not on steady holds.
  - Rehearse the protocol in both orders.

  The detail is in `cw1/docs/calibration-2026-11-04.md`.
- **CI** (`.github/workflows/ci.yml`) runs both: firmware tests and builds, host tests and the wiring check for
  `cw1/`, and the `aircam` tests for `cw2/`.
