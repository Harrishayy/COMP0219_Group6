import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os  # noqa: E402

# Tests never publish the board relay: a live dashboard on this machine owns calibration/.board-relay (calib/relay.py)
os.environ["BENCH_RELAY"] = "0"
