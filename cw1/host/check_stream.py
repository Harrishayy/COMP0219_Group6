#!/usr/bin/env python3
"""Check the 200 Hz / 115200 baud requirement: rate, dropped samples, unparseable lines.

    python host/check_stream.py -d 30

Velocity mode can only measure the average rate (USB delivers lines in bursts, so
host-side gaps say little). With --debug the board's own sample counter n is used,
which detects every dropped or overrun tick exactly. Exit code 1 on failure.
"""

from __future__ import annotations

import argparse
import sys
import time

from airspeed_tools import serialio

TARGET_HZ = 200.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port")
    ap.add_argument("-d", "--duration", type=float, default=20.0)
    ap.add_argument("--debug", action="store_true", help="use MODE D sample counter for exact drop detection")
    args = ap.parse_args()

    ser = serialio.open_port(args.port)
    serialio.send(ser, "MODE D" if args.debug else "MODE V")
    time.sleep(0.3)
    ser.reset_input_buffer()

    cols = serialio.Columns()
    data = bad = gaps = missing = 0
    last_n = None
    t_start = None
    for line in serialio.read_lines(ser):
        if t_start is None:
            t_start = line.host_time
        if line.host_time - t_start > args.duration:
            break
        if line.values is None:
            if line.raw and not cols.update(line):
                bad += 0 if line.is_info else 1
            continue
        data += 1
        if args.debug and cols.names and len(line.values) == len(cols.names):
            n = int(line.values[cols.names.index("n")])
            if last_n is not None and n != last_n + 1:
                gaps += 1
                missing += n - last_n - 1
            last_n = n
        elif not args.debug and len(line.values) != 1:
            bad += 1

    elapsed = time.time() - t_start
    rate = data / elapsed
    if args.debug:
        serialio.send(ser, "MODE V")
    stats = serialio.query(ser, "STATS", "#stats")

    print(f"lines: {data} in {elapsed:.1f} s = {rate:.2f} Hz (target {TARGET_HZ:.0f})")
    print(f"unparseable lines: {bad}")
    if args.debug:
        print(f"sample-counter gaps: {gaps} ({missing} samples missing)")
    if stats:
        print(f"board: {stats}")

    ok = abs(rate - TARGET_HZ) < 2.0 and bad == 0 and missing == 0
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
