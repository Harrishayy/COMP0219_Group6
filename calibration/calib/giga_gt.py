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

The GIGA's port is opened exclusively. To run the course's Wind Tunnel Logger at the same time, the GIGA is shared
through `calibration/.giga-relay` (calib/relay.py), which run.py offers as the logger's GIGA: everything the GIGA
sends goes to both, and the logger's commands go to the GIGA. The logger is in charge while it is there: after a
#HELLO it sends its own settings (with the P and TAIR typed into its window), so ours wait DEFER_S and are dropped
if it answered; it zeroes the GIGA itself, so ours is not sent.
"""
from __future__ import annotations

import atexit
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
DEFER_S = 1.0                   # with the logger on the relay: how long its answer to a #HELLO may take
LOGGER_QUIET_S = 3.0            # the logger has written nothing this long: ours may put the calibration back


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
        self.logger_wrote = 0.0                        # last command from the Wind Tunnel Logger on the relay
        self._pending: tuple[float, float, list[str]] | None = None   # (hello time, due, commands)

    def logger_command(self, cmd: str, now: float):
        """The logger sent `cmd` to the GIGA through the relay: it is driving the GIGA."""
        if cmd.startswith("ZERO") and self.logger_wrote == 0.0:
            self.notes.append("GIGA: the Wind Tunnel Logger is driving it (its settings and zero)")
        self.logger_wrote = now

    def tick(self, now: float) -> list[str]:
        """Commands due before the next read: settings deferred to the logger, the start-up zero."""
        if self._pending:
            t_hello, due, cmds = self._pending
            if self.logger_wrote > t_hello:            # the logger answered the #HELLO itself
                self._pending = None
            elif now >= due:
                self._pending = None
                self.notes.append(f"GIGA: sent {', '.join(cmds)}")
                return cmds
            return []
        if self.logger_wrote:                          # the logger zeroes the GIGA itself
            return []
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
            if self.logger_wrote:                      # the logger answers too: give it DEFER_S to do so
                self._pending = (now, now + DEFER_S, cmds)
                return []
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
            elif (have != self.want and now - self.sent_at > RESEND_S
                  and now - self.logger_wrote > LOGGER_QUIET_S):  # changed behind our back: put ours back
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


def _wait_for_port() -> str:
    """The GIGA's port: CALIB_GIGA_PORT, else the first Arduino on USB, waiting quietly until one is plugged in."""
    said = False
    while True:
        port = os.environ.get("CALIB_GIGA_PORT", "").strip() or find_port()
        if port:
            return port
        if not said:
            print("GIGA pitot tube: waiting for one on USB (the wood tunnel's ground truth)", flush=True)
            said = True
        time.sleep(1.0)


def read_pa():
    import serial

    from calib import relay  # absolute: GroundTruthSource loads this file on its own, outside the package

    port = _wait_for_port()
    os.environ["CALIB_GIGA_PORT"] = port       # the same GIGA after a reconnect, and the port in the saved run
    link = Giga(_env_float("CALIB_GIGA_P_PA", P_PA), _env_float("CALIB_GIGA_TAIR_C", TAIR_C))

    def send(ser, cmds):
        for c in cmds:
            ser.write((c + "\n").encode())

    # 115200, never 1200: opening a GIGA at 1200 baud reboots it into its bootloader. exclusive: never two readers.
    with serial.Serial(port, 115200, timeout=0.05, write_timeout=0.5, exclusive=True) as ser:
        share = relay.BoardRelay(relay.GIGA_LINK, keep_input=True) if relay.available() else None
        if share:  # this reader thread is not joined at exit, so its finally may never run: close it then too
            atexit.register(share.close)
        print(f"GIGA pitot tube on {port}" + (f"; the Wind Tunnel Logger can use it too: {relay.GIGA_LINK}"
                                              if share else ""), flush=True)
        buf, rx, last = b"", b"", time.monotonic()
        try:
            while True:
                now = time.monotonic()
                if share:                          # the logger's commands, whole lines, to the GIGA
                    rx += share.take_input()
                    *cmds, rx = rx.split(b"\n")
                    rx = rx[-4096:]
                    for c in cmds:
                        text = c.decode("ascii", "replace").strip()
                        if text:
                            link.logger_command(text, now)
                            send(ser, [text])
                send(ser, link.tick(now))
                chunk = ser.read(ser.in_waiting or 1)
                if not chunk:
                    if now - last > SILENT_S:
                        raise TimeoutError(f"the GIGA on {port} sent nothing for {SILENT_S:g} s")
                    continue
                last = now
                if share:
                    share.write(chunk)
                buf += chunk
                *lines, buf = buf.split(b"\n")
                buf = buf[-4096:]
                for raw in lines:
                    cmds, pa = link.on_line(raw.decode("ascii", "replace").strip(), now)
                    send(ser, cmds)
                    while link.notes:
                        print(link.notes.pop(0), flush=True)
                    if pa is not None:
                        yield pa
        finally:
            if share:
                share.close()
