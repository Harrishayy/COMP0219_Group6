"""(3) Serial line parsing: '#' lines are never data, #cols resyncs columns, garbage is dropped."""
import pytest

from calib.sources import LineParser


def test_stream_formats():
    p = LineParser()
    assert p.parse("5.123\r\n") == ("v", 5.123)                       # MODE V
    assert p.parse(b"#cols,n,feature,rho,temp_c,pressure_pa,v\n")[0] == "cols"
    kind, row = p.parse("17,1.85,1.1890,22.31,100870,4.2")            # MODE D
    assert kind == "d" and row["n"] == 17 and row["feature"] == 1.85 and row["v"] == 4.2


def test_debug_rows_end_with_the_speed():
    """The course's Wind Tunnel Logger reads the last field of a comma line as the speed."""
    from calib.sources import DEFAULT_COLS
    assert DEFAULT_COLS[-1] == "v"


def test_hash_lines_are_never_data():
    p = LineParser()
    assert p.parse('#cal {"method":"drag_plate","sqrt_k":171.0}') == ("cal", {"method": "drag_plate", "sqrt_k": 171.0})
    assert p.parse("#ok ema_alpha=0.2") == ("ok", "ema_alpha", 0.2)
    for line in ("#err unknown param foo", "#env 22.1 100900", "#5.0", "#stats 0 120"):
        assert p.parse(line)[0] in ("info", "ok", "cal", "cols")


def test_cols_header_resyncs_and_garbage_is_dropped():
    p = LineParser()
    p.parse("#cols,n,feature,v")                                       # HDR after a reconnect
    assert p.parse("3,0.5,2.0") == ("d", {"n": 3, "feature": 0.5, "v": 2.0})
    for junk in ("", "   ", "\x00\x00", "abc", "1.2.3", "3,0.5", "3,0.5,2.0,9", "nan", b"\xff\xfe", "5,6,x"):
        assert p.parse(junk) is None
    assert p.bad_lines > 0


def test_rows_in_one_usb_chunk_get_their_own_times():
    """USB hands over 2-3 rows at once: each gets its own time, 5 ms apart, ending at the chunk's arrival, and
    times never go backwards across chunks."""
    from calib.sources import SerialSensorSource

    got = []
    s = SerialSensorSource("/dev/null-board", on_sensor=lambda t, row: got.append(t))
    s._handle_chunk(10.000, [b"#cols,n,feature,rho,temp_c,pressure_pa,v"])
    s._handle_chunk(10.020, [b"1,0.1,1.19,22,nan,3.0\r", b"2,0.1,1.19,22,nan,3.0\r", b"#ok sqrt_k=242.5\r",
                             b"3,0.1,1.19,22,nan,3.0\r"])
    s._handle_chunk(10.021, [b"4,0.1,1.19,22,nan,3.0\r", b"5,0.1,1.19,22,nan,3.0\r"])
    assert got[:3] == pytest.approx([10.010, 10.015, 10.020])
    assert got[3] > got[2] and got[4] == pytest.approx(10.021)
