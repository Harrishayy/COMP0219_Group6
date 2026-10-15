#!/usr/bin/env python3
"""Log the board's stream to CSV with host timestamps.

    python host/log_serial.py                      # velocity stream -> data/logs/<time>.csv
    python host/log_serial.py --mode d -d 120      # debug CSV for two minutes
    python host/log_serial.py --app                # bench tools (env_monitor etc.): no commands

Informational '#' lines are echoed to the terminal and kept out of the CSV.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

from airspeed_tools import serialio

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="serial port (default: auto-detect the ST-LINK)")
    ap.add_argument("--mode", choices=["v", "d"], help="send MODE V/D before logging")
    ap.add_argument("--app", action="store_true", help="bench-tool firmware: send no commands")
    ap.add_argument("-d", "--duration", type=float, default=0, help="seconds to log (0 = until Ctrl-C)")
    ap.add_argument("-o", "--out", type=Path, help="output CSV (default: data/logs/<timestamp>.csv)")
    args = ap.parse_args()

    out = args.out or ROOT / "data" / "logs" / time.strftime("%Y%m%d-%H%M%S.csv")
    out.parent.mkdir(parents=True, exist_ok=True)

    ser = serialio.open_port(args.port)
    cols = serialio.Columns()
    if not args.app:
        if args.mode:
            serialio.send(ser, f"MODE {args.mode.upper()}")
        serialio.send(ser, "HDR")  # resync column names after a reconnect

    writer = None
    header: list[str] = []
    n = 0
    t0 = time.time()
    print(f"logging {ser.port} -> {out}  (Ctrl-C to stop)", file=sys.stderr)
    with open(out, "w", newline="") as f:
        try:
            for line in serialio.read_lines(ser):
                if args.duration and line.host_time - t0 > args.duration:
                    break
                if line.values is None:
                    if line.raw:
                        if cols.update(line) and writer is not None and cols.names != header[1:]:
                            print("# columns changed mid-log; restart the logger", file=sys.stderr)
                        print(line.raw, file=sys.stderr)
                    continue
                if writer is None:
                    header = ["host_time_s"] + cols.for_values(line.values)
                    writer = csv.writer(f)
                    writer.writerow(header)
                if len(line.values) != len(header) - 1:
                    continue
                writer.writerow([f"{line.host_time - t0:.4f}"] + line.raw.split(","))
                n += 1
                if n % 1000 == 0:
                    elapsed = line.host_time - t0
                    print(f"\r{n} lines, {n / elapsed:.1f} Hz", end="", file=sys.stderr)
        except KeyboardInterrupt:
            pass
    print(f"\nwrote {n} lines to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
