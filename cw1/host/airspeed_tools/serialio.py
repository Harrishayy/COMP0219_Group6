"""Talking to the Nucleo over the ST-LINK virtual COM port."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import serial
from serial.tools import list_ports

BAUD = 115200
ST_VID = 0x0483  # STMicroelectronics, used by the ST-LINK/V2-1 on the Nucleo


def find_port() -> str:
    """Return the first ST-LINK virtual COM port, or raise with the ports that exist."""
    ports = list(list_ports.comports())
    for p in ports:
        text = f"{p.description} {p.manufacturer or ''}".lower()
        if p.vid == ST_VID or "stlink" in text or "st-link" in text or "stm32" in text:
            return p.device
    listing = ", ".join(f"{p.device} ({p.description})" for p in ports) or "none"
    raise RuntimeError(f"No ST-LINK port found. Pass --port explicitly. Ports: {listing}")


def open_port(port: str | None = None, timeout: float = 0.5) -> serial.Serial:
    ser = serial.Serial(port or find_port(), BAUD, timeout=timeout)
    ser.reset_input_buffer()
    return ser


def send(ser: serial.Serial, command: str) -> None:
    ser.write((command.strip() + "\n").encode("ascii"))
    ser.flush()


@dataclass
class Line:
    """One line from the board: either data (values) or an informational '#' line."""

    host_time: float
    raw: str
    values: list[float] | None = None

    @property
    def is_info(self) -> bool:
        return self.raw.startswith("#")


def parse(raw: str, host_time: float | None = None) -> Line:
    raw = raw.strip()
    t = time.time() if host_time is None else host_time
    if not raw or raw.startswith("#"):
        return Line(t, raw)
    try:
        return Line(t, raw, [float(x) for x in raw.split(",")])
    except ValueError:
        return Line(t, raw)


@dataclass
class Columns:
    """Tracks the column names announced by '#cols,...' headers."""

    names: list[str] = field(default_factory=list)

    def update(self, line: Line) -> bool:
        if line.raw.startswith("#cols,"):
            self.names = line.raw.split(",")[1:]
            return True
        return False

    def for_values(self, values: list[float]) -> list[str]:
        if len(self.names) == len(values):
            return self.names
        if len(values) == 1:
            return ["v"]
        return [f"c{i}" for i in range(len(values))]


def read_lines(ser: serial.Serial):
    """Yield parsed lines forever (until the caller stops iterating)."""
    while True:
        raw = ser.readline()
        if not raw:
            continue
        yield parse(raw.decode("ascii", errors="replace"))


def query(ser: serial.Serial, command: str, prefix: str, timeout: float = 2.0) -> str | None:
    """Send a command and return the first line starting with prefix (e.g. '#cal')."""
    send(ser, command)
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = ser.readline().decode("ascii", errors="replace").strip()
        if raw.startswith(prefix):
            return raw
    return None
