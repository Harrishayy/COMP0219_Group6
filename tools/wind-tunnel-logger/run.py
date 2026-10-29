#!/usr/bin/env python3
"""Start the course's Wind Tunnel Logger (wind_tunnel_logger/, unmodified v1.0.0) with its runs saved in this
repo's data/tunnel/ instead of ~/Documents/Wind Tunnel Logger/logs, so the dashboard and `calib tunnel` find
them next to everything else. Arguments pass through: `--demo` runs a simulated tunnel and board.

While the dashboard (pnpm dev) owns the board, its port list also offers "Board via the dashboard": the board's
stream copied by the dashboard (calibration/calib/relay.py), so both run at once and each records on its own.

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

RELAY = HERE.parents[1] / "calibration" / ".board-relay"   # calib/relay.py LINK
_comports = app.serial.tools.list_ports.comports


def _comports_with_relay(*args, **kwargs):
    ports = list(_comports(*args, **kwargs))
    if RELAY.is_symlink() and RELAY.exists():
        from serial.tools.list_ports_common import ListPortInfo

        p = ListPortInfo(str(RELAY), skip_link_detection=True)
        p.description = "Board via the dashboard (pnpm dev)"
        ports.append(p)
    return ports


app.serial.tools.list_ports.comports = _comports_with_relay

if __name__ == "__main__":
    app.main()
