#!/usr/bin/env python3
"""Start the course's Wind Tunnel Logger (wind_tunnel_logger/, unmodified v1.0.0) with its runs saved in this
repo's data/tunnel/ instead of ~/Documents/Wind Tunnel Logger/logs, so the dashboard and `calib tunnel` find
them next to everything else. Arguments pass through: `--demo` runs a simulated tunnel and board.

    pnpm logger            (installs requirements.txt into .venv on first run)
    pnpm logger --demo
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGS = HERE.parents[1] / "data" / "tunnel"
sys.path.insert(0, str(HERE))

from wind_tunnel_logger import app  # noqa: E402

_init = app.MainWindow.__init__


def _init_in_repo(self, cfg, settings, log_dir, *args, **kwargs):
    # --smoke-test passes its own folder; keep that one
    _init(self, cfg, settings, log_dir if "--smoke-test" in sys.argv else LOGS, *args, **kwargs)


app.MainWindow.__init__ = _init_in_repo

if __name__ == "__main__":
    app.main()
