"""The board's stream copied to a virtual serial port for the Wind Tunnel Logger (calib/relay.py)."""
import os

import pytest

from calib import relay

pytestmark = pytest.mark.skipif(os.name != "posix", reason="pseudo-terminals are POSIX only")


def test_relay_copies_the_stream_through_its_stable_name(tmp_path):
    serial = pytest.importorskip("serial")
    link = tmp_path / ".board-relay"
    r = relay.BoardRelay(link)
    try:
        assert relay.current(link) == str(link)
        for _ in range(200):                     # nobody reading yet: the buffer fills and is flushed, never blocks
            r.write(b"1,0.01,1.19,22.5,nan,0.000\n")
        assert r.flushed > 0
        with serial.Serial(str(link), 115200, timeout=0.5) as s:
            s.reset_input_buffer()
            r.write(b"7,0.04,1.19,22.5,nan,6.125\n")
            assert s.readline() == b"7,0.04,1.19,22.5,nan,6.125\n"
            s.write(b"INFO\n")                   # what the logger writes is dropped, not sent to the board
            r.write(b"8,0.04,1.19,22.5,nan,6.130\n")
            assert s.readline().endswith(b"6.130\n")
    finally:
        r.close()
    assert not link.exists() and not link.is_symlink()

