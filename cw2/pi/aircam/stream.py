"""The 200 Hz output: one line per tick, in the form the Wind Tunnel Logger reads.

The logger (cw1/tools/wind-tunnel-logger, app.py:441-469) splits each line on commas and takes the *last* field
as the speed; it skips empty lines and lines starting with '#'. A line with exactly 7 fields is read as a GIGA
pitot line instead (speed from field 4, and the logger writes INFO back), so a debug row must never have 7.
It stamps lines with the laptop's clock on arrival, so our latency is lag in its score. At 115200 baud a line
must stay under ~57 bytes to fit 200 lines a second.

The ticker keeps an absolute 5 ms schedule on the camera's clock. When the process is held up it sends the
missed ticks straight away (up to `catch_up`) rather than skipping them, so the logger's 5 s rate stays at 200.
"""
from __future__ import annotations

import sys
import time

MAX_LINE = 56


def clock() -> float:
    """Seconds on the clock libcamera stamps frames with (CLOCK_BOOTTIME on Linux), so ticks and frames agree."""
    return time.clock_gettime(time.CLOCK_BOOTTIME) if hasattr(time, "CLOCK_BOOTTIME") else time.monotonic()


def line(v: float, extra=()) -> bytes:
    fields = [f"{x:.4g}" for x in extra] + [f"{v:.3f}"]
    if len(fields) == 7:
        raise ValueError("a 7-field line is read by the logger as a GIGA pitot line: add or drop a column")
    out = (",".join(fields) + "\n").encode()
    if len(out) > MAX_LINE:
        raise ValueError(f"{len(out)}-byte line will not fit 200 lines/s at 115200 baud")
    return out


class Ticker:
    def __init__(self, hz: float = 200.0, catch_up: int = 20, now=clock, sleep=time.sleep):
        self.period, self.catch_up, self.now, self.sleep = 1.0 / hz, catch_up, now, sleep
        self.next = now() + self.period
        self.late = 0          # ticks sent late (behind schedule by more than one period)
        self.skipped = 0       # ticks given up on after a stall longer than catch_up periods

    def wait(self) -> float:
        """Sleep until the next tick; return its scheduled time."""
        t = self.now()
        behind = t - self.next
        if behind > self.catch_up * self.period:
            n = int(behind / self.period)
            self.skipped += n
            self.next += n * self.period
        elif behind > self.period:
            self.late += 1
        elif behind < 0:
            self.sleep(-behind)
        tick = self.next
        self.next += self.period
        return tick


def udp_port(address: str) -> str:
    """'10.0.0.5' or '10.0.0.5:5005' as a Sink port: udp:HOST:PORT (5005 unless given)."""
    address = address[4:] if address.startswith("udp:") else address
    return f"udp:{address if ':' in address else address + ':5005'}"


def comment(**kv) -> bytes:
    """A '# aircam k=… arm=…' line: the logger skips lines starting with '#', and the screen simulator keeps the
    values so `aircam fit` knows which map the run used."""
    return ("# aircam " + " ".join(f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}" for k, v in kv.items())
            + "\n").encode()


class Sink:
    """Where the lines go: a serial port (pyserial), stdout when port is '-', or udp:HOST:PORT (one datagram per
    line, to the screen simulator on the laptop; nothing waits for it, so a missing listener costs nothing)."""

    def __init__(self, port: str = "-", baud: int = 115200):
        if port == "-":
            self._w, self._close = sys.stdout.buffer.write, lambda: sys.stdout.flush()
        elif port.startswith("udp:"):
            import socket
            host, _, p = port[4:].rpartition(":")
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            addr = (host or p, int(p) if host else 5005)
            self._w, self._close = (lambda data: sock.sendto(data, addr)), sock.close
        else:
            import serial
            s = serial.Serial(port, baud, write_timeout=0.05)
            self._w, self._close = s.write, s.close
        self.errors = 0

    def write(self, data: bytes):
        try:
            self._w(data)
        except Exception:      # a full buffer or an unplugged cable must not stop the estimator
            self.errors += 1

    def close(self):
        self._close()
