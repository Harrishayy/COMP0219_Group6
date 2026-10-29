"""Wind Tunnel Logger runs (calib/tunnel.py): read the logger's CSVs, score them and measure drift."""
import csv

import numpy as np
import pytest

from calib import tunnel


def write_run(d, name, t, gt, board):
    with open(d / f"{name}_aligned.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_iso", "timestamp_epoch", "ground_truth_mps", "student_mps"])
        for ti, g, b in zip(t, gt, board):
            w.writerow(["", f"{ti:.6f}", f"{g:.6f}", "" if b is None else f"{b:.6f}"])


def test_score_and_drift(tmp_path):
    t = 1.7e9 + np.arange(0, 120, 0.2)                      # 2 min of ground truth at 5 Hz
    gt = 2 + 8 * (np.arange(t.size) % 300) / 300             # ramps 2 -> 10 m/s
    board = gt + 0.1 + 0.5 * (t - t[0]) / 60                  # +0.1 m/s bias, drifting +0.5 m/s per minute
    write_run(tmp_path, "run1", t, gt, [*board[:-3], None, None, None])
    r = tunnel.load("run1", dirs=[tmp_path])
    assert r["pairs"] == t.size - 3 and r["missing"] == 3
    assert r["drift_per_min"] == pytest.approx(0.5, abs=1e-3)
    assert r["bias_first"] < r["bias_last"]
    sr = r["series"]
    assert len(sr["t"]) == len(sr["board"]) == len(sr["gt"]) == len(sr["error_mean"])
    late = [e for e in sr["error_mean"][-200:-20] if e is not None]
    assert late and min(late) > 0.9                          # the moving mean shows the drift late in the run
    assert [x["name"] for x in tunnel.list_runs([tmp_path])] == ["run1"]


def test_drag_area_suggestion(tmp_path):
    t = 1.7e9 + np.arange(0, 60, 0.2)
    gt = np.linspace(2.5, 10, t.size)
    write_run(tmp_path, "k", t, gt, gt * np.sqrt(1.21))       # board reads 10 % high: its sqrt_k is 21 % too big
    assert tunnel.load(str(tmp_path / "k_aligned.csv"), series=False)["k_scale"] == pytest.approx(1 / 1.21)


def test_no_runs(tmp_path):
    with pytest.raises(FileNotFoundError):
        tunnel.load(None, dirs=[tmp_path])


def test_logger_run_replays_through_the_dashboard(tmp_path):
    """A logger run loads as a replayable Run: board speed as `v`, the tunnel as ground truth, on one time base."""
    from calib.data import load_csv

    t = 1.7e9 + np.arange(0, 10, 0.2)
    write_run(tmp_path, "r", t, np.full(t.size, 5.0), np.full(t.size, 5.2))
    run = load_csv(tmp_path / "r_aligned.csv")
    assert run.channels == ["v"] and run.t_g[0] == 0.0
    assert np.allclose(run.gt, 5.0) and np.allclose(run.sensor["v"], 5.2)


def test_write_run_matches_the_logger(tmp_path):
    """A dashboard recording saved as a logger run: the logger's columns, each tunnel sample paired with the nearest
    board sample within 150 ms, and read back by load() like a run the logger saved."""
    tg = 1.7e9 + np.arange(0, 20, 0.2)                       # tunnel at 5 Hz
    ts = 1.7e9 + 0.003 + np.arange(0, 19, 0.005)             # board at 200 Hz, stops a second early
    files = tunnel.write_run(tmp_path / "rec_giga", {"t": tg, "v": np.full(tg.size, 6.0), "pa": np.full(tg.size, 21.6),
                                                     "device": "GIGA", "port": "/dev/cu.usbmodemG"},
                             {"t": ts, "v": np.full(ts.size, 6.3), "name": "Student", "port": "/dev/cu.usbmodemN"})
    assert [f.name for f in files] == ["rec_giga_vdas.csv", "rec_giga_student.csv", "rec_giga_aligned.csv"]
    with open(files[0], newline="") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0]) == tunnel.LOG_HEADER and rows[0]["device_name"] == "GIGA" and rows[0]["pa_corrected"] == "21.6000"
    with open(files[2], newline="") as f:
        assert next(csv.reader(f)) == tunnel.ALIGNED_HEADER
    r = tunnel.load("rec_giga", dirs=[tmp_path])
    assert r["gt_samples"] == tg.size and r["missing"] == 4     # 19.2–19.8 s: no board sample within 150 ms
    assert r["bias"] == pytest.approx(0.3) and r["board_rate_hz"] == pytest.approx(200, rel=1e-3)


def test_giga_protocol():
    """The GIGA is driven as the logger drives it: settings after #HELLO, readings only once its #CFG confirms our
    LEQ and C and the zero is answered, the zero given back after a reset."""
    from calib.giga_gt import Giga

    g = Giga(100870, 22.5)
    row = "1,2,0.51,21.6,5.98,23.1,0"
    assert g.on_line(row, 0.0) == (["INFO"], None)           # data before #HELLO: ask for it again
    cmds, _ = g.on_line("#HELLO GIGA fw=1.2", 0.1)
    assert cmds == ["P 100870", "LEQ 102.177", "C 1", "TAIR 22.5", "RATE 100"]
    assert g.on_line("#CFG leq_m=0 c=1", 0.2) == ([], None) and g.on_line(row, 0.3)[1] is None
    g.on_line("#CFG leq_m=102.177 c=1", 0.4)
    assert g.tick(0.5) == [] and g.tick(0.8) == ["ZERO"] and g.tick(1.0) == []   # after the 0.3 s settle, once
    assert g.on_line(row, 1.0)[1] is None                    # not zeroed yet
    g.on_line("#EVT zero_ok zero_pa=0.0312", 3.0)
    assert g.on_line(row, 3.1) == ([], 21.6)
    assert g.on_line("1,2,0.51,21.6,5.98,23.1,64", 3.2)[1] is None   # ZEROING flag
    assert g.on_line("1,2,0.51,-0.4,0,23.1,0", 3.3)[1] == 0.4
    cmds, _ = g.on_line("#HELLO GIGA fw=1.2", 9.0)           # the board reset: settings and its zero again
    assert cmds[-1] == "ZERO SET 0.0312" and g.on_line(row, 9.1)[1] is None


def test_giga_rejected_zero_keeps_reading():
    from calib.giga_gt import Giga

    g = Giga(tair_c=None)
    assert "TAIR OFF" in g.on_line("#HELLO GIGA", 0.0)[0]
    g.on_line("#CFG leq_m=102.177 c=1", 0.0)
    assert g.tick(0.5) == ["ZERO"] and g.tick(7.0) == ["ZERO"]    # no answer in 6 s: again
    g.on_line("#EVT zero_reject reason=unsteady mean_pa=3.1", 8.0)
    assert any("refused the zero" in n for n in g.notes)
    assert g.on_line("1,2,0.5,12.0,4.4,23,0", 8.1)[1] == 12.0


def _afa5(cell, pa):
    """A VDAS-F HID report from AFA5 cell `cell` (vdas_afa5.py protocol): 16-bit sign-magnitude Pa."""
    raw = (0x8000 | -pa) if pa < 0 else pa
    return [0, cell << 3, 1, 2, raw >> 8, raw & 0xFF] + [0] * 58


def test_vdas_reads_cell_2_when_cell_1_is_silent(monkeypatch):
    """The white tunnel's cell 1 broke: the logger's 1.0.1 reader picks cell 2, and --gt-vdas zeroes and reads it."""
    import threading
    import time

    from calib import vdas_gt

    mod = vdas_gt._vdas_module()
    monkeypatch.setattr(vdas_gt, "ZERO_S", 0.3)

    class Box(mod.GroundTruthReader):
        """The logger's reader with the HID device replaced by a script of reports."""
        def __init__(self, script):
            self.preferred, self.cell, self.last_report = 1, None, 0.0
            self._seen, self._first, self._listeners, self._alive = [0.0] * 8, 0.0, [], True
            self._thread = threading.Thread(target=self._play, args=(script,), daemon=True)
            self._thread.start()

        def _play(self, script):
            time.sleep(0.05)
            for cell, pa, dt in script:
                r = self.handle(_afa5(cell, pa), time.time())
                if r:
                    for cb in list(self._listeners):
                        cb(r)
                time.sleep(dt)
            self._alive = False

        def close(self):
            self._alive = False

    # cell 2 only: fan off at +2 Pa for 2 s (the first 1.5 s waits for cell 1), then 40 Pa; then cell 2 goes quiet
    # and cell 3 takes over with its own offset (-1 Pa)
    script = [(2, 2, 0.05)] * 40 + [(2, 42, 0.05)] * 20 + [(3, -1, 0.05)] * 70 + [(3, 29, 0.05)] * 20
    box = Box(script)
    got = list(vdas_gt.read_pa(box))
    assert box.cell == 3
    assert set(got) == {0, 40, 30}                  # fan off reads 0 on each cell after its own zero
    assert got.index(30) > len(got) - 25 > got.index(40)   # cell 2 minus its +2 Pa, then cell 3 minus its -1 Pa


def test_giga_defers_to_the_logger_on_the_relay():
    """With the Wind Tunnel Logger on the GIGA relay, the logger drives the GIGA: our settings wait for its answer to
    a #HELLO and are dropped when it gives one; our start-up zero is left to it; without it, ours go out late."""
    from calib.giga_gt import DEFER_S, Giga

    g = Giga()
    g.on_line("#HELLO GIGA", 0.0)
    g.on_line("#CFG leq_m=102.177 c=1", 0.1)
    assert g.tick(0.5) == ["ZERO"]
    g.on_line("#EVT zero_ok zero_pa=0.02", 3.0)
    g.logger_command("INFO", 10.0)                                   # the logger connects
    assert g.on_line("#HELLO GIGA", 10.05)[0] == []                  # ours wait…
    g.logger_command("LEQ 102.177", 10.1)                            # …and the logger answers
    assert g.tick(10.05 + DEFER_S + 0.1) == []
    assert g.on_line("#HELLO GIGA", 20.0)[0] == []                   # a reset with the logger gone quiet
    assert g.tick(20.0 + DEFER_S + 0.1)[-1] == "ZERO SET 0.02"       # ours go out, with the zero
    assert g.tick(30.0) == []                                        # and no zero of our own while it drives


def test_giga_relay_passes_the_loggers_commands_on(tmp_path):
    import os

    serial = pytest.importorskip("serial")
    if os.name != "posix":
        pytest.skip("pseudo-terminals are POSIX only")
    from calib import relay

    r = relay.BoardRelay(tmp_path / ".giga-relay", keep_input=True)
    try:
        with serial.Serial(str(tmp_path / ".giga-relay"), 115200, timeout=0.5) as s:
            r.write(b"#HELLO GIGA\n")
            assert s.readline() == b"#HELLO GIGA\n"
            s.write(b"LEQ 102.177\nZERO\n")   # no flush(): on a pty it waits for the other end, as the logger never does
            import time
            time.sleep(0.05)
            assert r.take_input() == b"LEQ 102.177\nZERO\n"
    finally:
        r.close()


def test_giga_adapter_runs_as_the_server_loads_it(monkeypatch):
    """GroundTruthSource loads giga_gt.py as a file, outside the package (a relative import broke it once): driven
    through that loader against a scripted GIGA, it sends the settings, zeroes and yields the pressure."""
    import os
    import threading
    import time
    import tty
    from pathlib import Path

    if os.name != "posix":
        pytest.skip("pseudo-terminals are POSIX only")
    pytest.importorskip("serial")
    from calib.sources import GroundTruthSource

    m, s = os.openpty()
    tty.setraw(s)
    got, stop = [], threading.Event()

    def giga():
        buf, leq = b"", 0.0
        while not stop.is_set():
            os.set_blocking(m, False)
            try:
                buf += os.read(m, 1024)
            except BlockingIOError:
                pass
            *lines, buf = buf.split(b"\n")
            for ln in lines:
                cmd = ln.decode().strip()
                got.append(cmd)
                reply = {"INFO": "#HELLO GIGA", "ZERO": "#EVT zero_ok zero_pa=0.01"}.get(cmd)
                if cmd.startswith("LEQ "):
                    leq = float(cmd[4:])
                    reply = f"#CFG leq_m={leq:.3f} c=1.000"
                if reply:
                    os.write(m, (reply + "\n").encode())
            os.write(m, b"1,0,24.01,24.0,6.3,23,0\n")
            time.sleep(0.01)

    threading.Thread(target=giga, daemon=True).start()
    monkeypatch.setenv("CALIB_GIGA_PORT", os.ttyname(s))
    fn = GroundTruthSource(module=f"{Path(__file__).parents[1] / 'calib' / 'giga_gt.py'}:read_pa")._load_callable()
    try:
        gen = fn()
        assert next(gen) == 24.0
        assert {"LEQ 102.177", "C 1", "ZERO"} <= set(got)
        gen.close()
    finally:
        stop.set()
