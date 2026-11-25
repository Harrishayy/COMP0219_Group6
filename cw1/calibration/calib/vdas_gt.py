"""Ground truth from the white TecQuipment tunnel's VDAS box, read the way the course's Wind Tunnel Logger reads it
(its vdas_afa5.py, tools/wind-tunnel-logger), for `calib serve --gt-vdas`.

`read_pa` is a generator for GroundTruthSource's module adapter: it opens the box, averages its offset for
ZERO_S with the fan off (as the logger does at start-up), then yields |dp − offset| in Pa. --gt-vdas also sets
--gt-pressure, so the server turns each sample into v = √(2·dp/ρ) with the board's ρ (the logger uses C = 1 and
the ρ typed into its window). The box streams whole pascals about 5 times a second.

The cell is the logger's choice (1.0.1): cell CELL if it reports, else the only other AFA5 cell that does. The white
tunnel's cell 1 broke in October 2026, so it reads cell 2. If the cell in use changes mid-run, it zeroes again, as
the logger does: each cell has its own offset.

Only one program can drive the box: close the logger and TecQuipment's VDAS software first.
"""
from __future__ import annotations

import importlib.util
import queue
from pathlib import Path

VENDORED = Path(__file__).resolve().parents[2] / "tools" / "wind-tunnel-logger" / "wind_tunnel_logger" / "vdas_afa5.py"
ZERO_S = 5.0            # logger config vdas.zero_seconds
MAX_OFFSET_PA = 6.0     # vdas.max_offset_pa: a larger fan-off mean means the fan was running
FALLBACK_OFFSET_PA = 3.0
CELL = 1                # vdas.cell: the AFA5 cell that is the tunnel's airspeed reference


def _vdas_module():
    spec = importlib.util.spec_from_file_location("vdas_afa5", VENDORED)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # needs hidapi (calibration/requirements.txt)
    return mod


def zero_offset(samples: list[float]) -> tuple[float, str]:
    """The logger's fan-off zero: the mean, unless it is too large to be an offset."""
    if not samples:
        return FALLBACK_OFFSET_PA, "no samples during the zero"
    mean = sum(samples) / len(samples)
    if abs(mean) > MAX_OFFSET_PA:
        return FALLBACK_OFFSET_PA, f"read {mean:+.1f} Pa during the zero (fan running?): using {FALLBACK_OFFSET_PA:+g} Pa"
    return mean, f"offset {mean:+.2f} Pa"


def _open_reader():
    """The logger's reader, with its one "not found (open failed)" error split in two: macOS opens HID devices
    exclusively, so a box that is plugged in but will not open is held by another program, usually the logger."""
    mod = _vdas_module()
    try:
        return mod.GroundTruthReader(cell=CELL)
    except RuntimeError:
        if mod.hid.enumerate(mod.VENDOR_ID, mod.PRODUCT_ID):
            raise RuntimeError("VDAS box is plugged in but busy: another program has it open (the Wind Tunnel "
                               "Logger or TecQuipment's VDAS software). Close it, or start the dashboard with "
                               "--no-gt and let the logger keep the box") from None
        raise RuntimeError("VDAS box not plugged in (no TecQuipment VDAS on USB)") from None


def read_pa(reader=None):
    if reader is None:
        reader = _open_reader()
    q: queue.Queue = queue.Queue()
    reader.add_listener(lambda r: r["valid"] and q.put((r["t"], float(r["pa"]), r["cell"])))
    try:
        while True:
            zero, t_end, cell = [], None, None
            while t_end is None or (zero and zero[-1][0] < t_end):
                t, pa, c = q.get(timeout=10)                    # no data for 10 s: let the adapter reopen the box
                if c != cell:                                   # a new cell: its own zero, from its first reading
                    zero, t_end, cell = [], None, c
                t_end = t_end or t + ZERO_S
                zero.append((t, pa))
            offset, note = zero_offset([pa for _, pa in zero])
            why = "" if cell == CELL else f" (cell {CELL} sends nothing)"
            print(f"VDAS zero on pressure cell {cell}{why}: {note}", flush=True)
            while reader.is_plugged():
                try:
                    _, pa, c = q.get(timeout=1)
                except queue.Empty:
                    continue
                if c != cell:
                    print(f"VDAS: pressure cell {c} took over from cell {cell}: zeroing again (stop the fan)", flush=True)
                    break
                yield abs(pa - offset)
            else:
                return
    finally:
        reader.close()
