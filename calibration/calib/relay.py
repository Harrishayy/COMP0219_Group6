"""The board's serial stream copied onto a virtual serial port, so the course's Wind Tunnel Logger can read the board
while the dashboard owns its USB port (one program per port). The dashboard keeps sending its commands; the logger
sees every byte the board sends, as if it were plugged in itself: data rows (`v` last, as it needs) and the `#`
replies it ignores. Anything the logger writes is dropped, never sent to the board.

The port is a pseudo-terminal, reached through the stable name `calibration/.board-relay` (a symlink to it), so the
logger reconnects to the same name after the dashboard restarts. `tools/wind-tunnel-logger/run.py` lists it as
"Board via the dashboard". With nobody reading, its buffer (about 1 kB) is flushed rather than left to go stale.
Not on Windows, which has no pseudo-terminals.
"""
from __future__ import annotations

import os
from pathlib import Path

LINK = Path(__file__).resolve().parents[1] / ".board-relay"
LABEL = "Board via the dashboard (pnpm dev)"


def available() -> bool:
    return os.name == "posix" and os.environ.get("BENCH_RELAY", "1") != "0"


def current(link: Path = LINK) -> str | None:
    """The relay's stable name while a dashboard serves it, else None."""
    return str(link) if link.is_symlink() and link.exists() else None


class BoardRelay:
    def __init__(self, link: Path = LINK):
        import termios
        import tty

        self._termios = termios
        self.master, self.slave = os.openpty()
        # raw, and held open here so it stays raw (no echo, no line editing) between the logger's connections
        tty.setraw(self.slave)
        os.set_blocking(self.master, False)
        self.path = os.ttyname(self.slave)
        self.link = Path(link)
        tmp = self.link.with_name(self.link.name + ".tmp")
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        os.symlink(self.path, tmp)
        os.replace(tmp, self.link)
        self.sent = self.flushed = 0

    def write(self, data: bytes):
        """Called from the board's reader thread with every chunk it reads."""
        for attempt in (0, 1):
            try:
                self.sent += os.write(self.master, data)
                break
            except BlockingIOError:   # nobody reading: drop what is queued so a new reader starts fresh
                if attempt == 0:
                    self.flushed += 1
                    self._termios.tcflush(self.slave, self._termios.TCIFLUSH)
            except OSError:
                break
        try:
            while os.read(self.master, 4096):  # what the logger writes: dropped
                pass
        except OSError:
            pass

    def close(self):
        try:
            if self.link.is_symlink() and os.readlink(self.link) == self.path:
                self.link.unlink()
        except OSError:
            pass
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass
