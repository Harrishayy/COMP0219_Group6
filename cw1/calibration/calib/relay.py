"""The board's serial stream copied onto a virtual serial port, so the course's Wind Tunnel Logger can read the board
while the dashboard owns its USB port (one program per port). The dashboard keeps sending its commands; the logger
sees every byte the board sends, as if it were plugged in itself: data rows (`v` last, as it needs) and the `#`
replies it ignores. Anything the logger writes is dropped, never sent to the board.

The port is a pseudo-terminal, reached through the stable name `calibration/.board-relay` (a symlink to it), so the
logger reconnects to the same name after the dashboard restarts. `tools/wind-tunnel-logger/run.py` lists it as
"Board via the dashboard". With nobody reading, its buffer (about 1 kB) is flushed rather than left to go stale.
Not on Windows, which has no pseudo-terminals.

The GIGA pitot tube is shared the same way (calib/giga_gt.py, `GIGA_LINK`), but both ways: the logger drives it
(settings, zero), so what it writes is kept (`keep_input`) and passed on to the GIGA.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

LINK = Path(__file__).resolve().parents[1] / ".board-relay"
LABEL = "Board via the dashboard (pnpm dev)"
GIGA_LINK = LINK.with_name(".giga-relay")
GIGA_LABEL = "GIGA pitot tube via the dashboard (pnpm dev)"


def available() -> bool:
    return os.name == "posix" and os.environ.get("BENCH_RELAY", "1") != "0"


def _pid_file(link: Path) -> Path:
    return Path(link).with_name(Path(link).name + ".pid")


def live(link: Path = LINK) -> bool:
    """A dashboard serves this relay now: the link resolves and the process that made it (its .pid file) runs. A
    dashboard that was killed leaves a link to a device node that still exists, so the link alone does not tell."""
    link = Path(link)
    try:
        pid = int(_pid_file(link).read_text().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return link.is_symlink() and link.exists()


def current(link: Path = LINK) -> str | None:
    """The relay's stable name while a dashboard serves it, else None."""
    return str(link) if live(link) else None


class BoardRelay:
    def __init__(self, link: Path = LINK, keep_input: bool = False):
        import termios
        import tty

        self._termios = termios
        self.master, self.slave = os.openpty()
        # raw, and held open here so it stays raw (no echo, no line editing) between the logger's connections
        tty.setraw(self.slave)
        os.set_blocking(self.master, False)
        self.path = os.ttyname(self.slave)
        self.link = Path(link)
        self._publish()
        self._checked = time.monotonic()
        self.sent = self.flushed = 0
        self.keep_input, self._rx = keep_input, b""

    def _publish(self):
        tmp = self.link.with_name(self.link.name + ".tmp")
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        os.symlink(self.path, tmp)
        os.replace(tmp, self.link)
        _pid_file(self.link).write_text(f"{os.getpid()}\n")

    def _keep_link(self):
        """Every 2 s: put the stable name back if it is gone or points at a port nobody serves any more (another
        program that took it has stopped). A name another live relay holds is left to it."""
        now = time.monotonic()
        if now - self._checked < 2.0:
            return
        self._checked = now
        try:
            if not self.link.is_symlink() or (os.readlink(self.link) != self.path and not live(self.link)):
                self._publish()
        except OSError:
            pass

    def write(self, data: bytes):
        """Called from the board's reader thread with every chunk it reads."""
        self._keep_link()
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
        self._read_input()

    def _read_input(self):
        try:
            while True:
                data = os.read(self.master, 4096)
                if not data:
                    break
                if self.keep_input:
                    self._rx += data[: 65536 - len(self._rx)]
        except OSError:  # nothing more to read
            pass

    def take_input(self) -> bytes:
        """What the reader wrote since the last call (keep_input only; otherwise it is dropped)."""
        self._read_input()
        data, self._rx = self._rx, b""
        return data

    def close(self):
        try:
            if self.link.is_symlink() and os.readlink(self.link) == self.path:
                self.link.unlink()
                _pid_file(self.link).unlink(missing_ok=True)
        except OSError:
            pass
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass
