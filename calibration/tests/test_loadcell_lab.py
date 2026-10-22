"""The standalone teaching lab reports counts/grams, never competition airspeed."""
import json
import math
import time

from app.server import Engine
from calib.sources import SerialSensorSource


class FakeSerial:
    def __init__(self):
        self.writes = []

    def write(self, value):
        self.writes.append(value)


def lab_source():
    rows, info = [], []
    source = SerialSensorSource("fake", on_sensor=lambda t, row: rows.append(row), on_info=info.append)
    source._ser = FakeSerial()
    source.connected = True
    return source, rows, info


def test_lab_counts_tare_and_calibration():
    source, rows, info = lab_source()
    source._handle(1, b"Raw: -1043 counts (uncalibrated)\r")
    assert source.loadcell_lab["raw_counts"] == -1043
    assert not source.loadcell_lab["calibrated"]
    assert rows[-1]["lab_raw"] == -1043 and math.isnan(rows[-1]["lab_weight"])
    source._handle(2, "Tare: -1043 counts")
    source._handle(3, "Calibration: -12.345678 counts/g")
    source._handle(4, "Weight: 249.12 g  Raw: -4119")
    state = source.loadcell_lab
    assert state["tared"] and state["calibrated"]
    assert state["zero_offset"] == -1043 and state["scale_factor"] == -12.345678
    assert state["tare_seq"] == state["calibration_seq"] == 1
    assert rows[-1] == {"lab_raw": -4119, "lab_weight": 249.12}
    assert source.data_rows == 2 and source.parser.bad_lines == 0


def test_lab_commands_and_errors():
    source, rows, info = lab_source()
    source._handle(1, "Commands: t = tare with no load; k 249 = calibrate WITH 249 g on scale; h = help. End with CR or LF.")
    for command in ("MODE D", "HDR", "ENV", "GET", "t", "k 249", "h"):
        assert source.send_command(command)
    assert source._ser.writes == [b"h\n", b"t\n", b"k 249\n", b"h\n"]
    assert not source.send_command("SET lc_offset 123")
    assert len(source._ser.writes) == 4 and source.loadcell_lab["error_seq"] == 1
    for text in ("First remove all load and send t to tare", "Use k <positive known mass in grams>",
                 "ERROR: load change too small; tare unloaded, then add known mass"):
        source._handle(2, text)
    assert source.loadcell_lab["error_seq"] == 4
    assert source.loadcell_lab["last_error"].startswith("ERROR: load change")
    assert not rows and len(info) == 5


def test_lab_resets_clear_measurements_but_keep_ack_ids():
    source, rows, info = lab_source()
    source._handle(1, "Tare: 100 counts")
    source._handle(2, "Calibration: 5 counts/g")
    source._handle(3, "Weight: 20.0 g  Raw: 200")
    source._handle(4, "Raw: 101 counts (uncalibrated)")  # reset banner may be missed
    state = source.loadcell_lab
    assert not state["calibrated"] and not state["tared"]
    assert state["weight_g"] is state["scale_factor"] is state["zero_offset"] is None
    assert state["tare_seq"] == state["calibration_seq"] == 1
    source._handle(5, "Tare: 110 counts")
    source._handle(6, "Calibration: 10 counts/g")
    source._handle(7, "ERROR: ADC read timed out; reconnecting sensor")
    assert not source.loadcell_lab["calibrated"] and source.loadcell_lab["error_seq"] == 1
    source._handle(8, "NAU7802 initialized. RAW counts (uncalibrated).")
    assert source.loadcell_lab["last_error"] is None
    assert source.loadcell_lab["tare_seq"] == source.loadcell_lab["calibration_seq"] == 2
    source._reset_lab(detected=False)  # serial reconnect
    assert source.loadcell_lab is None
    source._handle(9, "Scale factor: 1.000000 counts/g; tared=0; calibrated=0")
    assert source.loadcell_lab["tare_seq"] == 2
    source._handle(10, "Tare: 111 counts")
    assert source.loadcell_lab["tare_seq"] == 3
    source._handle(11, "#cols,n,v,feature,rho,temp_c,pressure_pa")
    assert source.loadcell_lab is None  # replaced by competition firmware


def test_lab_malformed_and_nonfinite_lines_do_not_create_samples():
    source, rows, info = lab_source()
    source._handle(1, "LAB2 load cell: firmware running; USART2 115200 8N1, flow control OFF")
    for text in ("Weight: nan g  Raw: 10", "Weight: 1e999 g  Raw: 10", "Weight: 1 g  Raw:",
                 "Raw: 12 counts", "Calibration: inf counts/g", "Calibration: 0 counts/g",
                 "Tare: " + "9" * 400 + " counts"):
        source._handle(2, text)
    assert not rows and not source.loadcell_lab["calibrated"]
    assert source.loadcell_lab["tare_seq"] == 0
    assert source.loadcell_lab["raw_counts"] is None


def test_lab_frame_exposes_grams_and_counts_without_airspeed(monkeypatch):
    engine = Engine("live", sensor_port="fake")
    source = engine.sensor_src
    now = time.perf_counter()
    source.connected, source.opened_t = True, now
    source._handle(now, "Raw: 100 counts (uncalibrated)")
    source._handle(now + 0.5, "Tare: 100 counts")
    source._handle(now + 0.6, "Calibration: 5 counts/g")
    source._handle(now + 1, "Weight: 20.00 g  Raw: 200")
    frame = engine.build_frame()
    assert frame["raw"] == {"lab_raw": [100.0, 200.0], "lab_weight": [None, 20.0]}
    assert frame["est"] == frame["dev"] == [None, None]
    assert frame["telemetry"]["loadcell_lab"]["weight_g"] == 20
    assert frame["telemetry"]["board_state"] == "streaming"
    json.dumps(frame, allow_nan=False)
    # Plain 't' is a blocking LAB2 command and must not schedule the competition TARE readback.
    source._ser = FakeSerial()
    monkeypatch.setattr("app.server.threading.Timer", lambda *_args: (_ for _ in ()).throw(AssertionError("timer")))
    assert engine.command("t")
    assert source._ser.writes == [b"t\n"]
    engine.sensor_src = None
    assert engine.telemetry()["loadcell_lab"] is None
