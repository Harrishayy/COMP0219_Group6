"""`calib board-check` and the flashable calibration profiles behind `pnpm flash <profile>`."""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from calib import cli, models, tunnel_fit

ROOT = Path(__file__).resolve().parents[2]
INI = ROOT / "firmware" / "platformio.ini"


def _f32(x):
    return float(np.float32(x))


def test_profile_matches_board_float32():
    p = {**models.default_params(), "sqrt_k": 149.2, "sqrt_x0": 0.02404, "lpf_hz": 0.51}
    board = {k: _f32(v) for k, v in p.items()}
    rows = cli.profile_mismatches(p, board)
    assert all(same for *_, same in rows)
    assert [r[0] for r in rows] == ["sqrt_k", "sqrt_x0", "lc_n_per_count", "filter_kind", "lpf_hz", "lead_tau"]


def test_profile_mismatch_and_inactive_smoother():
    p = {**models.default_params(), "filter_kind": float(models.FILTER_EMA), "ema_alpha": 0.0103}
    board = {**p, "lpf_hz": 99.0}                    # the unused Butterworth setting is not compared
    assert all(r[3] for r in cli.profile_mismatches(p, board))
    board = {**p, "sqrt_k": 150.0}
    bad = [r[0] for r in cli.profile_mismatches(p, board) if not r[3]]
    assert bad == ["sqrt_k"]
    assert [r[0] for r in cli.profile_mismatches(p, {}) if not r[3]]  # nothing reported: all differ


def test_board_check_refuses_a_port_someone_holds(monkeypatch):
    monkeypatch.setattr(cli, "_port_holders", lambda port: ["python3.1 (pid 1)"])
    monkeypatch.setattr(cli, "_cal_via_serial", lambda *a: pytest.fail("must not open a held port"))
    a = SimpleNamespace(profile="default_20261104", port="/dev/cu.usbmodemX", baud=115200, wait=1.0)
    with pytest.raises(SystemExit, match="has it open"):
        cli.cmd_board_check(a)


def test_board_check_reports_a_different_board(monkeypatch):
    monkeypatch.setattr(cli, "_port_holders", lambda port: [])
    prof = cli._find_profile("new_calibration_20261105")
    on_default = {**tunnel_fit.default_profile()["params"], "build": "x", "method": "drag_plate"}
    monkeypatch.setattr(cli, "_cal_via_serial", lambda *a: (on_default, a[0]))
    a = SimpleNamespace(profile=prof["name"], port="/dev/cu.usbmodemX", baud=115200, wait=1.0)
    with pytest.raises(SystemExit, match="NOT on new_calibration_20261105"):
        cli.cmd_board_check(a)
    monkeypatch.setattr(cli, "_cal_via_serial", lambda *a: ({**prof["params"], "build": "x"}, a[0]))
    cli.cmd_board_check(a)                           # the right profile passes


def _profile_envs():
    text = INI.read_text()
    return re.findall(r"^\[env:airspeed_drag_plate_(\w+)\]\n(?:[^\[]*?)-I include/profiles/(\w+)", text, re.M)


@pytest.mark.parametrize("env,folder", _profile_envs())
def test_each_profile_build_flashes_its_saved_profile(env, folder):
    """`pnpm flash <profile>` builds airspeed_drag_plate_<profile> from include/profiles/<profile>/: the header
    there must hold the same numbers as calibration/profiles/<profile>.json, which board-check compares against."""
    assert env == folder
    rec = json.loads((tunnel_fit.PROFILE_DIR / f"{env}.json").read_text())
    header = (tunnel_fit.HEADER_DIR / env / "calibration_profile.h").read_text()
    defs = {m[0].lower(): float(m[1]) for m in re.findall(r"#define CAL_(\w+) ([-0-9.e]+)f", header)}
    rows = cli.profile_mismatches(rec["params"], defs)
    assert all(same for *_, same in rows), rows


def test_the_frozen_default_equals_the_flashed_default():
    """default_20261104 is the 4 Nov default saved apart from calibration.h: while calibration.h still holds that
    default, the two must agree (if calibration.h changes on purpose, this test is the reminder to keep the copy)."""
    saved = json.loads((tunnel_fit.PROFILE_DIR / "default_20261104.json").read_text())["params"]
    assert all(r[3] for r in cli.profile_mismatches(saved, tunnel_fit.default_profile()["params"]))
    assert saved["sqrt_k"] == 145.1 and saved["sqrt_x0"] == 0.0 and saved["lpf_hz"] == 0.75


def test_new_calibration_profile_values():
    p = json.loads((tunnel_fit.PROFILE_DIR / "new_calibration_20261105.json").read_text())["params"]
    assert (p["sqrt_k"], p["sqrt_x0"], p["filter_kind"], p["lpf_hz"], p["lead_tau"]) == (149.2, 0.02404, 0.0, 0.51, 0.0)
