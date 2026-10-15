"""(3) Serial line parsing: '#' lines are never data, #cols resyncs columns, garbage is dropped."""
from calib.sources import LineParser


def test_stream_formats():
    p = LineParser()
    assert p.parse("5.123\r\n") == ("v", 5.123)                       # MODE V
    assert p.parse(b"#cols,n,v,feature,rho,temp_c,pressure_pa\n")[0] == "cols"
    kind, row = p.parse("17,4.2,1.85,1.1890,22.31,100870")            # MODE D
    assert kind == "d" and row["n"] == 17 and row["feature"] == 1.85


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
