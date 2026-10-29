"""Ground truth from the wood tunnel's GIGA pitot tube (an Arduino GIGA R1 running WindSpeedGiga), driven the way the
course's Wind Tunnel Logger drives it (its GigaSource, tools/wind-tunnel-logger), for `calib serve --gt-giga`.

`read_pa` is a generator for GroundTruthSource's module adapter. The GIGA keeps its settings in RAM only and boots
with LEQ 0 (no tube-loss correction), reading 50-60 % low, so after every #HELLO it is sent the logger's settings
(P, LEQ 102.177 m, C 1, TAIR, RATE 100), and a reading counts only once its #CFG shows our LEQ and C. Then it is
zeroed on the board (ZERO, fan off) and `read_pa` yields |dp| in Pa, the pressure after the GIGA's zero, altitude and
tube-loss corrections. --gt-giga also sets --gt-pressure, so the server turns each sample into v = √(2·dp/ρ) with
the board's ρ (the logger uses C = 1 and the ρ typed into its window). About 100 samples/s.

The CLI passes its settings in the environment: CALIB_GIGA_PORT (empty: the first Arduino on USB), CALIB_GIGA_P_PA
and CALIB_GIGA_TAIR_C (the day's pressure and air temperature, as typed into the logger).

Only one program can own the GIGA's port: close the logger first.
"""
from __future__ import annotations

import os
import time

VID = 0x2341                    # Arduino
LEQ_M, C = 102.177, 1.0         # logger config giga.leq_m, giga.c
RATE_HZ = 100                   # giga.gt_rate_hz
P_PA, TAIR_C = 101325.0, 20.0   # logger config air.p_amb_pa, air.air_temp_c
HOLD, OVR, CLIP, ZEROING = 1, 8, 16, 64
SETTLE_S = 0.3                  # rows already queued under the old settings can follow the confirming #CFG
RESEND_S = 2.0                  # its #CFG still shows another LEQ or C this long after ours: send again
ZERO_RETRY_S = 6.0              # a zero takes 2.2 s: no answer by then, send ZERO again
SILENT_S = 10.0                 # nothing for this long: let the adapter reopen the port


def cmd_number(x) -> str:
    """Number as the GIGA command parser accepts it: plain decimal, no exponent."""
    return f"{float(x):.4f}".rstrip("0").rstrip(".")


def evt_fields(line: str) -> dict:
    """key=value fields of a GIGA metadata line ("#EVT zero_ok zero_pa=0.0312 ..." -> {"zero_pa": "0.0312"})."""
    return dict(kv.split("=", 1) for kv in line.split()[1:] if "=" in kv)


class Giga:
    """The logger's GigaSource without its Qt thread: feed it lines, it answers with commands to send and readings.

    It zeroes once, when the settings are confirmed (as the logger does at start-up). A rejected zero (the fan was
    running) keeps the GIGA's own zero and says so."""

    def __init__(self, p_pa: float = P_PA, tair_c: float | None = TAIR_C):
        self.calibration = [f"LEQ {cmd_number(LEQ_M)}", f"C {cmd_number(C)}"]
        self.settings = [f"P {cmd_number(p_pa)}", *self.calibration,
                         f"TAIR {cmd_number(tair_c)}" if tair_c is not None else "TAIR OFF", f"RATE {RATE_HZ}"]
        self.want = (round(LEQ_M, 3), round(C, 3))     # as its #CFG prints them
        self.hello = self.asked_info = self.ready = False
        self.ready_from = self.sent_at = 0.0
        self.zero_pa: float | None = None              # last accepted zero: given back if the board resets
        self.zeroed = False                            # zero answered (accepted or rejected)
        self.zero_sent: float | None = None
        self.notes: list[str] = []

    def tick(self, now: float) -> list[str]:
        """Commands due before the next read: the start-up zero, once the settings are in."""
        if self.hello and self.ready and now >= self.ready_from and not self.zeroed:
            if self.zero_sent is None or now - self.zero_sent > ZERO_RETRY_S:
                self.zero_sent = now
                self.notes.append("GIGA: zeroing on the board (keep the fan off)")
                return ["ZERO"]
        return []

    def on_line(self, line: str, now: float) -> tuple[list[str], float | None]:
        """(commands to send, |dp| in Pa when the line is a usable reading)."""
        if not line:
            return [], None
        if line.startswith("#"):
            return self._meta(line, now), None
        fields = line.split(",")
        if len(fields) != 7:
            return [], None
        try:
            flags, pa = int(fields[6]), float(fields[3])
        except ValueError:
            return [], None
        cmds = []
        if not self.hello and not self.asked_info:
            self.asked_info = True
            cmds.append("INFO")                        # its #HELLO was missed: ask again, so the settings get sent
        usable = (self.ready and self.zeroed and now >= self.ready_from
                  and not flags & (HOLD | ZEROING | OVR | CLIP))
        return cmds, abs(pa) if usable else None

    def _meta(self, line: str, now: float) -> list[str]:
        if line.startswith("#HELLO"):
            self.hello, self.ready = True, False
            cmds = list(self.settings)
            if self.zero_pa is not None:               # the board reset: give it its zero back
                cmds.append(f"ZERO SET {cmd_number(self.zero_pa)}")
            self.sent_at = now
            self.notes.append(f"GIGA: sent {', '.join(cmds)}")
            return cmds
        if line.startswith("#CFG"):
            f = evt_fields(line)
            try:
                have = (round(float(f["leq_m"]), 3), round(float(f["c"]), 3))
            except (KeyError, ValueError):
                return []
            cmds = []
            if have == self.want and not self.ready:
                self.ready_from = now + SETTLE_S
                self.notes.append(f"GIGA: LEQ {have[0]:g} m, C {have[1]:g} set")
            elif have != self.want and now - self.sent_at > RESEND_S:   # changed behind our back: put ours back
                cmds, self.sent_at = list(self.calibration), now
                self.notes.append(f"GIGA has LEQ {have[0]:g} m, C {have[1]:g}: sending ours again")
            self.ready = have == self.want
            return cmds
        if line.startswith("#EVT zero_ok") or line.startswith("#EVT zero_reject"):
            self.zeroed = True
            if line.startswith("#EVT zero_ok"):
                try:
                    self.zero_pa = float(evt_fields(line)["zero_pa"])
                except (KeyError, ValueError):
                    pass
                self.notes.append(f"GIGA zero: {line[5:]}")
            else:
                self.notes.append(f"GIGA refused the zero ({line[5:]}): the fan was running? Using its own zero; "
                                  "stop the fan and restart the dashboard to zero again")
        elif line.startswith("#EVT sensor_fault") or (line.startswith("#SENSOR") and "state=missing" in line):
            self.notes.append(f"GIGA: its pressure sensor isn't answering ({line[1:]}): check the 4 sensor wires")
        elif line.startswith("#ERR") and not line.startswith("#ERR ZERO reason=busy"):
            self.notes.append(f"GIGA refused a command: {line[1:]}")
        return []


def find_port() -> str | None:
    from serial.tools import list_ports

    return next((p.device for p in list_ports.comports() if p.vid == VID), None)


def _env_float(name: str, default: float | None) -> float | None:
    v = os.environ.get(name, "").strip()
    if not v:
        return default
    return None if v.lower() == "off" else float(v)


def read_pa():
    import serial

    port = os.environ.get("CALIB_GIGA_PORT", "").strip() or find_port()
    if not port:
        raise RuntimeError("no GIGA pitot tube on USB (no Arduino found; pass --gt-giga PORT)")
    os.environ["CALIB_GIGA_PORT"] = port       # the same GIGA after a reconnect, and the port in the saved run
    link = Giga(_env_float("CALIB_GIGA_P_PA", P_PA), _env_float("CALIB_GIGA_TAIR_C", TAIR_C))

    def send(ser, cmds):
        for c in cmds:
            ser.write((c + "\n").encode())

    # 115200, never 1200: opening a GIGA at 1200 baud reboots it into its bootloader
    with serial.Serial(port, 115200, timeout=0.2, write_timeout=0.5) as ser:
        print(f"GIGA pitot tube on {port}")
        buf, last = b"", time.monotonic()
        while True:
            now = time.monotonic()
            send(ser, link.tick(now))
            chunk = ser.read(ser.in_waiting or 1)
            if not chunk:
                if now - last > SILENT_S:
                    raise TimeoutError(f"the GIGA on {port} sent nothing for {SILENT_S:g} s")
                continue
            last = now
            buf += chunk
            *lines, buf = buf.split(b"\n")
            buf = buf[-4096:]
            for raw in lines:
                cmds, pa = link.on_line(raw.decode("ascii", "replace").strip(), now)
                send(ser, cmds)
                while link.notes:
                    print(link.notes.pop(0))
                if pa is not None:
                    yield pa
