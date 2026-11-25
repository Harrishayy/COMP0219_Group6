#!/usr/bin/env python3
"""Start the course's Wind Tunnel Logger (wind_tunnel_logger/, unmodified v1.0.1) with its runs saved in this
repo's data/tunnel/ instead of ~/Documents/Wind Tunnel Logger/logs, so the dashboard and `calib tunnel` find
them next to everything else. Arguments pass through: `--demo` runs a simulated tunnel and board.

While the dashboard (pnpm dev) owns the board, its port list also offers "Board via the dashboard": the board's
stream copied by the dashboard (calibration/calib/relay.py), so both run at once and each records on its own. The
GIGA pitot tube is shared the same way, and is picked as the ground truth the first time one is offered; the board
relay is connected as Your board when the window opens.

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

RELAY = HERE.parents[1] / "calibration" / ".board-relay"        # calib/relay.py LINK
GIGA_RELAY = HERE.parents[1] / "calibration" / ".giga-relay"   # calib/relay.py GIGA_LINK
ST_VID = 0x0483                                                # the Nucleo's ST-LINK
_comports = app.serial.tools.list_ports.comports


def _live(link):
    """A dashboard serves this relay now (calib/relay.py live()): the link resolves and its owner, in <link>.pid,
    runs. A killed dashboard leaves a link to a device node that still exists."""
    import os

    try:
        os.kill(int(link.with_name(link.name + ".pid").read_text().strip()), 0)
    except (OSError, ValueError):
        return False
    return link.is_symlink() and link.exists()


def _comports_with_relay(*args, **kwargs):
    """The ports, with what the dashboard holds swapped for its relay: the board ("Board via the dashboard") and the
    GIGA (listed with the GIGA's USB id, so the logger offers it as GIGA pitot tube ground truth). The devices
    themselves are left out while the dashboard has them: opening one twice would split its bytes."""
    from serial.tools.list_ports_common import ListPortInfo

    board, giga = _live(RELAY), _live(GIGA_RELAY)
    ports = [p for p in _comports(*args, **kwargs)
             if not (board and p.vid == ST_VID) and not (giga and p.vid == app.GIGA_VID)]
    if board:
        p = ListPortInfo(str(RELAY), skip_link_detection=True)
        p.description = "Board via the dashboard (pnpm dev)"
        ports.append(p)
    if giga:
        p = ListPortInfo(str(GIGA_RELAY), skip_link_detection=True)
        p.description, p.vid = "GIGA pitot tube via the dashboard (pnpm dev)", app.GIGA_VID
        ports.append(p)
    return ports


app.serial.tools.list_ports.comports = _comports_with_relay

# Ground truth: the GIGA pitot tube. The first time one is offered (plugged in, or shared by the dashboard), it is
# picked instead of "Wind tunnel"; after that the choice in the window is left alone.
_update_giga_choices = app.MainWindow.update_giga_choices


def _prefer_giga(self, ports=None):
    _update_giga_choices(self, ports)
    if getattr(self, "_giga_picked", False) or self.logging or self.gt_key != "tunnel":
        return
    keys = [self.cmb_source.itemData(i) for i in range(self.cmb_source.count())]
    giga = [k for k in keys if str(k).startswith("giga:")]
    if giga:
        self._giga_picked = True

        def pick():  # after the window is built: this first runs inside its __init__, inside set_source("tunnel")
            if self.gt_key == "tunnel" and not self.logging:
                self.set_source(giga[0])
                self._show_source(giga[0])

        app.QtCore.QTimer.singleShot(0, pick)


app.MainWindow.update_giga_choices = _prefer_giga

# Your board: while the dashboard shares the board, connect to it as soon as the window is up, as if "Board via the
# dashboard" had been picked and Connect pressed (Disconnect and pick another port as usual).
_init_with_logs = app.MainWindow.__init__


def _init_with_board(self, cfg, settings, log_dir, simulated=False, fake_student=False):
    _init_with_logs(self, cfg, settings, log_dir, simulated, fake_student)
    if fake_student:
        return

    def connect():
        i = self.cmb_port.findData(str(RELAY))
        if _live(RELAY) and i >= 0 and self.student_reader is None:
            self.cmb_port.setCurrentIndex(i)
            self.toggle_student()

    app.QtCore.QTimer.singleShot(0, connect)


app.MainWindow.__init__ = _init_with_board

if __name__ == "__main__":
    app.main()
