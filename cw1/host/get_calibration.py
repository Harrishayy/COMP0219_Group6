#!/usr/bin/env python3
"""Fetch the board's calibration ("calibration file on demand") and save it as JSON.

    python host/get_calibration.py                  # prints and saves data/calibration_<time>.json
    python host/get_calibration.py -o cal.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from airspeed_tools import serialio

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port")
    ap.add_argument("-o", "--out", type=Path)
    args = ap.parse_args()

    ser = serialio.open_port(args.port)
    line = serialio.query(ser, "GET", "#cal ")
    if line is None:
        print("no '#cal' reply: is the airspeed firmware flashed?", file=sys.stderr)
        return 1
    cal = json.loads(line[len("#cal ") :])
    env = serialio.query(ser, "ENV", "#env")
    if env:
        cal["env_status"] = env[len("#env ") :]

    out = args.out or ROOT / "data" / time.strftime("calibration_%Y%m%d-%H%M%S.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cal, indent=2) + "\n")
    print(json.dumps(cal, indent=2))
    print(f"saved {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
