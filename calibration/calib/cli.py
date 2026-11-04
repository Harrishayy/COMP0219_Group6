"""Command line: python -m calib <command> ...  (run from calibration/)."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from . import export, fit, metrics, models  # noqa: E402
from .data import load_csv, save_csv  # noqa: E402
from .sources import AUTO_PORT, PROFILES, SerialSensorSource, SimulatedTunnel, list_ports  # noqa: E402


def _add_gt_args(p):
    g = p.add_argument_group("ground truth (pick one)")
    g.add_argument("--gt-cmd", help='command whose stdout prints GT samples, e.g. "python gt.py"')
    g.add_argument("--gt-module", help="path/to/gt.py:function returning m/s (or a generator)")
    g.add_argument("--gt-port", help="serial port streaming GT floats")
    g.add_argument("--gt-baud", type=int, default=115200)
    g.add_argument("--gt-pressure", action="store_true", help="GT prints pitot differential pressure (Pa), not m/s")
    g.add_argument("--gt-vdas", action="store_true",
                   help="the white tunnel's VDAS box over USB, read as the Wind Tunnel Logger reads it (fan off at start)")
    g.add_argument("--gt-giga", nargs="?", const="auto", metavar="PORT",
                   help="the wood tunnel's GIGA pitot tube over USB, driven as the Wind Tunnel Logger drives it "
                        "(found by its Arduino USB id unless PORT is given; fan off at start)")
    g.add_argument("--gt-p-pa", type=float, default=101325.0,
                   help="today's pressure in Pa, sent to the GIGA as the logger sends it (default %(default)g)")
    g.add_argument("--gt-tair-c", default="20",
                   help="today's air temperature in °C sent to the GIGA, or off (default %(default)s)")


def _resolve_gt(a):
    """--gt-vdas and --gt-giga are the module adapters calib/vdas_gt.py and calib/giga_gt.py with --gt-pressure."""
    if getattr(a, "gt_vdas", False) and getattr(a, "gt_giga", None):
        raise SystemExit("pick one ground truth: --gt-vdas (white tunnel) or --gt-giga (wood tunnel)")
    if getattr(a, "gt_vdas", False):
        a.gt_module, a.gt_pressure = f"{Path(__file__).with_name('vdas_gt.py')}:read_pa", True
    if getattr(a, "gt_giga", None):
        import os

        # the adapter runs in this process and reads its settings from the environment (calib/giga_gt.py)
        os.environ["CALIB_GIGA_PORT"] = "" if a.gt_giga == "auto" else a.gt_giga
        os.environ["CALIB_GIGA_P_PA"] = str(a.gt_p_pa)
        os.environ["CALIB_GIGA_TAIR_C"] = str(a.gt_tair_c)
        a.gt_module, a.gt_pressure = f"{Path(__file__).with_name('giga_gt.py')}:read_pa", True


def _load_params(path: str | None) -> dict:
    p = models.default_params()
    if path:
        p.update(export.load(path)["params"])
    return p


def _fmt_metrics(m: dict) -> str:
    keys = ("mse", "rmse", "bias", "sigma", "mae", "max_abs", "r2", "lag_ms", "n")
    return "  ".join(f"{k}={m[k]:.4g}" if isinstance(m.get(k), float) else f"{k}={m.get(k)}" for k in keys if k in m)


# ------------------------------------------------------------------------------------- commands
def cmd_ports(a):
    ports = list_ports()
    if not ports:
        print("no serial ports found (is pyserial installed and the Nucleo plugged in?)")
    for p in ports:
        print(f"{p['device']:30s} {p['description']}  [{p['hwid']}]")


def cmd_sim(a):
    sim = SimulatedTunnel(profile=a.profile, seed=a.seed)
    run = sim.generate(a.duration)
    out = save_csv(run, a.out or ROOT / "runs" / f"sim_{a.profile}_{a.seed}.csv")
    print(f"wrote {len(run.t_s)} samples to {out}")
    tr = sim.truth
    print(f"truth: sqrt_k≈{tr.sqrt_k * tr.lc_n_per_count / sim.board['lc_n_per_count']:.1f} "
          f"sqrt_x0≈{(tr.lc_offset - sim.board['lc_offset']) * sim.board['lc_n_per_count']:.4f}")


def cmd_record(a):
    """Headless recording in the same session format as the dashboard (CSV + JSON sidecar), with the
    PC preview computed from --params (default: defaults)."""
    from app.server import Engine

    _resolve_gt(a)
    if not (a.port or a.gt_cmd or a.gt_module or a.gt_port):
        raise SystemExit("nothing to record: give --port and/or a --gt-* option")
    eng = Engine("live", sensor_port=a.port, baud=a.baud, gt_cmd=a.gt_cmd, gt_module=a.gt_module,
                 gt_port=a.gt_port, gt_baud=a.gt_baud, gt_pressure=a.gt_pressure)
    if a.params:
        eng.set_params({k: v for k, v in _load_params(a.params).items() if k in models.bounds()})
    eng.start()
    print(eng.toggle_record(True, a.label or "rec_drag_plate"), f"for {a.duration:g} s (Ctrl-C to stop early)")
    t0 = time.time()
    try:
        while time.time() - t0 < a.duration:
            time.sleep(0.05)
            eng.record_tick()
            while eng.info:
                print("\n" + eng.info.popleft(), end="")
            if int((time.time() - t0) * 20) % 20 == 0:
                st = eng.session_status() or {}
                print(f"\r{time.time() - t0:5.0f} s  rows={st.get('rows', 0)}  "
                      f"sensor:{getattr(eng.sensor_src, 'status', '-')}  gt:{getattr(eng.gt_src, 'status', '-')}",
                      end="", flush=True)
    except KeyboardInterrupt:
        pass
    print("\n" + eng.toggle_record(False))
    eng.stop()


def _report_text(params, run, window):
    raw, gt = fit.prepare(run, window)
    est = models.apply(params, raw)
    lines = ["PC preview: " + _fmt_metrics(metrics.compute(est, gt))]
    if "v" in raw:
        lines.append("board v : " + _fmt_metrics(metrics.compute(raw["v"], gt)))
    st = metrics.step_metrics(est, gt)
    if st["steps"]:
        lines.append(f"steps ({len(st['steps'])}): " + "  ".join(f"{k}={v:.3g}" for k, v in st["median"].items()))
    for b in metrics.by_speed_band(est, gt):
        lines.append(f"  band {b['band']:>6} m/s  n={b['n']:6d}  bias={b['bias']:+.3f}  sigma={b['sigma']:.3f}  rmse={b['rmse']:.3f}")
    for f in metrics.diagnose(est, gt, raw.get("feature"), raw.get("rho"), params):
        lines.append(f"[{f['severity']}] {f['title']}: {f['why']} -> {f['action']}"
                     + (f"  apply {f['apply']}" if f["apply"] else ""))
    return "\n".join(lines), raw, gt, est


def cmd_report(a):
    run = load_csv(a.csv)
    params = _load_params(a.params)
    text, raw, gt, est = _report_text(params, run, a.window)
    print(text)
    if a.png:
        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError:
            print("matplotlib not installed; skipping plot")
            return
        t = run.window(a.window).t_s
        t = t - t[0]
        fig, ax = plt.subplots(3, 1, figsize=(11, 8), sharex=False)
        ax[0].plot(t, gt, lw=1, label="ground truth")
        ax[0].plot(t, est, lw=1, label="PC preview")
        if "v" in raw:
            ax[0].plot(t, raw["v"], lw=0.8, alpha=0.6, label="board v")
        ax[0].set_ylabel("m/s"); ax[0].legend(); ax[0].grid(alpha=0.3)
        ax[1].plot(t, est - gt, lw=0.8); ax[1].set_ylabel("error m/s"); ax[1].grid(alpha=0.3)
        ax[2].scatter(gt, est, s=1, alpha=0.3); lim = [0, np.nanmax(gt) * 1.1]
        ax[2].plot(lim, lim, "k--", lw=0.8); ax[2].set_xlabel("ground truth m/s"); ax[2].set_ylabel("estimate m/s")
        fig.tight_layout(); fig.savefig(a.png, dpi=130)
        print(f"saved {a.png}")


def cmd_fit(a):
    run = load_csv(a.csv)
    params = _load_params(a.params)
    free = a.free.split(",") if a.free else None
    raw, gt = fit.prepare(run, a.window, a.lag_ms / 1000.0)
    r = fit.fit_arrays(params, raw, gt, a.method, free)
    print(f"{a.method} on {r.n} samples ({r.elapsed_s:.2f} s): {r.message}")
    print("before: " + _fmt_metrics(r.before))
    print("after : " + _fmt_metrics(r.after))
    for f in r.free:
        print(f"  {f:16s} {params[f]:>14.6g} -> {r.params[f]:.6g}")
    if a.cv:
        cv = fit.cross_validate(r.params, raw, gt, a.method, free, k=a.cv)
        print(f"blocked {a.cv}-fold CV: test mse {cv['test_mse_mean']:.5f} ± {cv['test_mse_std']:.5f}")
    if not r.ok:
        raise SystemExit("fit failed; nothing exported")
    paths = export.write(r.params, {"method": a.method, "free": r.free, "n": r.n, "source": a.csv,
                                    "window_s": a.window}, r.before, r.after, install=a.install)
    for k, v in paths.items():
        print(f"{k:9s} {v}")


def _session_table(sess: dict) -> str:
    segs = sess.get("segments", [])
    hdr = f"{'pset':>4} {'start s':>8} {'dur s':>7}  {'changed':<28} {'MSE pc':>9} {'MSE board':>9} {'bias':>7} {'sigma':>7} {'dMSE':>8}"
    lines = [hdr, "-" * len(hdr)]
    t0 = sess.get("meta", {}).get("t0") or (segs[0]["t_start"] if segs else 0.0)
    prev = None
    for s in segs:
        mp, mb = s.get("metrics_pc") or {}, s.get("metrics_board") or {}
        ref = mb if mb.get("mse") is not None else mp
        mse = ref.get("mse")
        d = "" if prev is None or mse is None else f"{mse - prev:+.4f}"
        ch = ",".join(s.get("changed") or []) or "(start)"
        f = lambda v, n=4: "-" if v is None else f"{v:.{n}f}"  # noqa: E731
        lines.append(f"{s['pset']:>4} {s['t_start'] - t0:8.1f} {s['duration_s']:7.1f}  {ch[:28]:<28} "
                     f"{f(mp.get('mse')):>9} {f(mb.get('mse')):>9} {f(ref.get('bias'), 3):>7} {f(ref.get('sigma'), 3):>7} {d:>8}")
        if mse is not None:
            prev = mse
    ps = {p["pset"]: p for p in sess.get("param_sets", [])}
    lines.append("")
    for s in segs:
        if s.get("changed") and s["pset"] in ps and s["pset"] - 1 in ps:
            a_, b_ = ps[s["pset"] - 1]["params"], ps[s["pset"]]["params"]
            diff = "  ".join(f"{k}: {a_.get(k):.6g} -> {b_.get(k):.6g}" for k in s["changed"]
                             if isinstance(a_.get(k), (int, float)) and isinstance(b_.get(k), (int, float)))
            if diff:
                lines.append(f"pset {s['pset']}: {diff}")
    notes = [e for e in sess.get("events", []) if e.get("kind") in ("note", "push", "tare", "fit")]
    for e in notes:
        lines.append(f"  {e.get('rel_s', 0):7.1f} s  {e['kind']:<5} {e.get('detail') if e['kind'] != 'fit' else e.get('name')}")
    summ = sess.get("summary", {})
    for k in ("pc", "board"):
        if k in summ:
            lines.append(f"overall {k}: mse={summ[k]['mse']:.4f} bias={summ[k]['bias']:+.3f}  best pset {summ[k]['best_pset']} (mse {summ[k]['best_mse']:.4f})")
    return "\n".join(lines)


def cmd_session(a):
    from .recorder import load_session

    path = Path(a.json)
    if path.suffix == ".csv":
        path = path.with_suffix(".json")
    sess = load_session(path)
    m = sess.get("meta", {})
    print(f"session {path.name}: {m.get('mode')} started {m.get('started')}  rows={m.get('rows')}")
    print(_session_table(sess))
    if a.png:
        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError:
            print("matplotlib not installed; skipping plot")
            return
        import csv

        csv_path = path.parent / m.get("csv", path.with_suffix(".csv").name)
        cols: dict[str, list] = {"t": [], "gt": [], "v_board": [], "v_pc": []}
        with csv_path.open(newline="") as f:
            for row in csv.DictReader(f):
                for k in cols:
                    try:
                        cols[k].append(float(row[k]))
                    except (ValueError, TypeError):
                        cols[k].append(np.nan)
        t0 = m.get("t0") or (cols["t"][0] if cols["t"] else 0.0)
        t = np.asarray(cols["t"]) - t0
        fig, ax = plt.subplots(2, 1, figsize=(12, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
        ax[0].plot(t, cols["gt"], "k", lw=1, label="ground truth")
        if np.isfinite(cols["v_board"]).any():
            ax[0].plot(t, cols["v_board"], "C0", lw=0.9, label="board")
        ax[0].plot(t, cols["v_pc"], "C1", lw=0.9, label="PC preview")
        for k, s in enumerate(sess.get("segments", [])):
            for x in ax:
                x.axvspan(s["t_start"] - t0, s["t_end"] - t0, color="0.5", alpha=0.1 if k % 2 else 0.0, lw=0)
            mb = (s.get("metrics_board") or s.get("metrics_pc") or {}).get("mse")
            ax[0].text(s["t_start"] - t0, 0.98, f" p{s['pset']}" + (f"\n mse {mb:.3f}" if mb is not None else ""),
                       va="top", fontsize=7, transform=ax[0].get_xaxis_transform())
        ax[1].plot(t, np.asarray(cols["v_pc"]) - np.asarray(cols["gt"]), "C1", lw=0.7, label="PC error")
        if np.isfinite(cols["v_board"]).any():
            ax[1].plot(t, np.asarray(cols["v_board"]) - np.asarray(cols["gt"]), "C0", lw=0.7, label="board error")
        ax[0].set_ylabel("m/s"); ax[0].legend(loc="upper right"); ax[0].grid(alpha=0.3)
        ax[1].set_ylabel("error m/s"); ax[1].set_xlabel("s"); ax[1].grid(alpha=0.3); ax[1].legend(loc="upper right")
        fig.tight_layout(); fig.savefig(a.png, dpi=130)
        print(f"saved {a.png}")


def cmd_export(a):
    rec = export.load(a.json)
    params = {**models.default_params(), **rec["params"]}
    paths = export.write(params, rec.get("fit"), rec.get("metrics_before"), rec.get("metrics_after"),
                         install=a.install)
    for k, v in paths.items():
        print(f"{k:9s} {v}")


def _open_board(port, baud, **kw) -> SerialSensorSource:
    src = SerialSensorSource(port, baud, **kw)
    src.start()
    for _ in range(50):
        if src.connected:
            return src
        time.sleep(0.1)
    src.stop()
    raise SystemExit(f"could not open {port}: {src.status}")


def cmd_check(a):
    """The marking requirement: 200 Hz over 115200 baud with no drops. In debug mode the board's own
    sample counter n finds every missed tick; in velocity mode only the average rate is measurable."""
    ns, info = [], []
    src = _open_board(a.port, a.baud, debug_mode=a.debug, on_info=info.append,
                      on_sensor=lambda t, row: ns.append(row.get("n", float("nan"))))
    time.sleep(0.5)
    ns.clear()
    src.parser.bad_lines = 0
    t0 = time.monotonic()
    time.sleep(a.duration)
    elapsed, n_lines, bad = time.monotonic() - t0, len(ns), src.parser.bad_lines
    src.send_command("STATS")
    if a.debug:
        src.send_command("MODE V")
    time.sleep(0.3)
    src.stop()
    rate = n_lines / elapsed
    missing = 0
    if a.debug:
        n = np.asarray(ns, dtype=float)
        n = n[np.isfinite(n)]
        missing = int(np.sum(np.clip(np.diff(n) - 1, 0, None))) if len(n) > 1 else 0
    print(f"lines: {n_lines} in {elapsed:.1f} s = {rate:.2f} Hz (target 200)")
    print(f"unparseable lines: {bad}")
    if a.debug:
        print(f"missing samples (counter gaps): {missing}")
    for s in info:
        if s.startswith("#stats"):
            print("board:", s)
    ok = abs(rate - 200.0) < 2.0 and bad == 0 and missing == 0
    print("PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


def cmd_get(a):
    """Calibration file on demand: the board's live parameters (GET) saved as JSON."""
    cal, env = {}, []
    src = _open_board(a.port, a.baud, debug_mode=False, on_cal=cal.update, on_info=env.append)
    src.send_command("GET")
    src.send_command("ENV")
    time.sleep(1.0)
    src.stop()
    if not cal:
        raise SystemExit("no #cal reply: is the airspeed firmware flashed?")
    env_line = next((s for s in env if s.startswith("#env")), None)
    if env_line:
        cal["env_status"] = env_line[len("#env"):].strip()
    out = Path(a.out) if a.out else ROOT / "exports" / time.strftime("board_calibration_%Y%m%d_%H%M%S.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cal, indent=2) + "\n")
    print(json.dumps(cal, indent=2))
    print(f"saved {out}")


def cmd_push(a):
    rec = export.load(a.json)
    lines = export.push_lines({**models.default_params(), **rec["params"]})
    replies = []
    src = _open_board(a.port, a.baud, debug_mode=False, on_info=replies.append,
                      on_cal=lambda c: replies.append(f"#cal {json.dumps(c)}"))
    for ln in lines + ["GET"]:
        src.send_command(ln)
        print(">", ln)
        time.sleep(0.05)
    time.sleep(0.5)
    src.stop()
    for r in replies:
        print(r)


def cmd_weights(a):
    """Interactive load-cell scale calibration (board must run METHOD_DRAG_PLATE in debug mode)."""
    cal = {}
    src = SerialSensorSource(a.port, a.baud, on_cal=cal.update, on_info=lambda s: print(" ", s))
    buf: list = []
    src.on_sensor = lambda t, row: buf.append(row.get("feature", np.nan))
    src.start()
    time.sleep(2.0)
    old = a.old if a.old else cal.get("lc_n_per_count")
    if not old:
        src.stop()
        raise SystemExit("no lc_n_per_count from GET; pass --old")
    print(f"current lc_n_per_count = {old}")

    def settle(sec=3.0):
        buf.clear()
        time.sleep(sec)
        x = np.asarray(buf, dtype=float)
        return float(np.nanmedian(x)) if len(x) else float("nan"), float(np.nanstd(x)) if len(x) else float("nan")

    input("Remove all load from the plate and press Enter to TARE… ")
    src.send_command("TARE")
    time.sleep(2.0)
    z, zs = settle()
    print(f"zero reads {z:+.5f} N (σ {zs:.5f})")
    masses, forces = [], []
    while True:
        s = input("Hang a known mass and type its grams (blank to finish): ").strip()
        if not s:
            break
        try:
            m = float(s)
        except ValueError:
            print("not a number")
            continue
        f, fs = settle()
        print(f"  {m:g} g -> {f:.5f} N (σ {fs:.5f}); expected {m / 1000 * fit.G:.5f} N")
        masses.append(m)
        forces.append(f)
    src.stop()
    if not masses:
        raise SystemExit("no points")
    res = fit.solve_weights(masses, forces, old)
    print(json.dumps(res, indent=2))
    print(f"\nSET lc_n_per_count {res['lc_n_per_count_new']:.9g}")
    out = ROOT / "exports" / f"weights_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(res, indent=2))
    print(f"saved {out}")


def _stream_printer(scale: float, offset: float, span: float):
    """Print one line per batch of force samples: raw counts, force, spread and a bar."""
    print(f"scale {scale:g} N/count · offset {offset:g} counts · bar ±{span:g} counts")
    print(f"{'time':>8} {'counts':>9} {'force N':>10} {'min':>9} {'max':>9}")

    def emit(forces: list[float]):
        counts = np.asarray(forces) / scale + offset
        c = float(counts.mean())
        pos = int(np.clip(20 + 20 * c / span, 0, 40))
        bar = "·" * pos + "█" + "·" * (40 - pos)
        print(f"{time.strftime('%H:%M:%S')} {c:9.0f} {np.mean(forces):10.5f} {counts.min():9.0f} {counts.max():9.0f}  {bar}",
              flush=True)

    return emit


def _stream_server(a):
    import asyncio

    import websockets

    async def run():
        url = f"ws://{a.host}:{a.http_port}/ws"
        try:
            ws = await websockets.connect(url, max_size=None)
        except OSError as e:
            raise SystemExit(f"no dashboard at {url} ({e}): start `calib serve --port …` or pass --port")
        async with ws:
            board = json.loads(await ws.recv()).get("board", {})
            emit = _stream_printer(board.get("lc_n_per_count") or 1e-5, board.get("lc_offset") or 0.0, a.span)
            buf, last = [], time.monotonic()
            async for text in ws:
                m = json.loads(text)
                if m.get("type") != "frame" or m.get("replace"):
                    continue  # skip the history replay a new client gets
                buf += [x for x in m["raw"].get("feature", []) if x is not None]
                if buf and time.monotonic() - last >= a.interval:
                    emit(buf)
                    buf, last = [], time.monotonic()

    asyncio.run(run())


def _stream_serial(a):
    cal, buf = {}, []
    src = _open_board(a.port, a.baud, debug_mode=True, on_cal=cal.update,
                      on_sensor=lambda t, row: buf.append(row.get("feature", float("nan"))))
    src.send_command("GET")
    time.sleep(0.5)
    emit = _stream_printer(cal.get("lc_n_per_count") or 1e-5, cal.get("lc_offset") or 0.0, a.span)
    try:
        while True:
            time.sleep(a.interval)
            batch, buf[:] = [x for x in buf if np.isfinite(x)], []
            if batch:
                emit(batch)
    finally:
        src.stop()


def cmd_stream(a):
    """Live load-cell readout in the terminal: from the running dashboard, or straight off the board."""
    try:
        _stream_serial(a) if a.port else _stream_server(a)
    except KeyboardInterrupt:
        pass


def cmd_tunnel(a):
    """Score a Wind Tunnel Logger run against the tunnel and show its drift."""
    from . import tunnel

    if a.list:
        for r in tunnel.list_runs():
            print(f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(r['mtime']))}  {r['name']}  "
                  f"({', '.join(sorted(r['files']))})  {r['dir']}")
        return
    try:
        r = tunnel.load(a.run, series=False)
    except FileNotFoundError as e:
        sys.exit(str(e))
    print("\n".join(tunnel.summary_lines(r)))


def cmd_tunnel_fit(a):
    """Fit a calibration profile to dashboard recordings with noisy tunnel ground truth (calib/tunnel_fit.py)."""
    from . import tunnel_fit
    from .recorder import runs_dir

    if a.list:
        for p in tunnel_fit.all_profiles():
            mse = f"MSE {p['mse']:.3f}" if p.get("mse") is not None else ""
            print(f"{p['name']:28s} {mse:10s} {p['label']}")
        return
    if not a.runs:
        sys.exit("give the recordings to fit (calibration/runs/run_*.csv), or --list")
    paths = []
    for r in a.runs:
        p = Path(r)
        if not p.exists() and (runs_dir() / p.name).with_suffix(".csv").exists():
            p = (runs_dir() / p.name).with_suffix(".csv")
        paths.append(p)
    try:
        rec = tunnel_fit.calibrate(paths, zero=a.zero, gt_cutoff_hz=a.gt_cutoff, dyn_cutoff_hz=a.filter_cutoff)
    except (FileNotFoundError, ValueError) as e:
        sys.exit(str(e))
    rec["name"] = a.name
    print("\n".join(tunnel_fit.summary_lines(rec)))
    if a.dry_run:
        return
    for k, v in tunnel_fit.write_profile(rec, a.name, a.label or "").items():
        print(f"{k:7s} {v}")


def cmd_serve(a):
    from app.server import Engine, serve

    if a.replay:
        mode = "replay"
    elif a.sim:
        mode = "sim"
    else:
        # The board is the truth: with no --port, wait for a Nucleo rather than play simulated data.
        mode = "live"
        if not a.port:
            a.port = AUTO_PORT
            print("no --port given: waiting for a Nucleo on USB (pass --sim for the simulator)")
    _resolve_gt(a)
    eng = Engine(mode, a.window, sensor_port=a.port, baud=a.baud, gt_cmd=a.gt_cmd, gt_module=a.gt_module,
                 gt_port=a.gt_port, gt_baud=a.gt_baud, gt_pressure=a.gt_pressure, replay=a.replay,
                 replay_speed=a.speed, profile=a.profile, seed=a.seed)
    serve(eng, a.host, a.http_port, not a.no_browser and not a.reopen, live_reload=a.live_reload)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m calib", description="Airspeed sensor calibration toolkit")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ports", help="list serial ports"); p.set_defaults(fn=cmd_ports)

    p = sub.add_parser("serve", help="live dashboard (waits for the Nucleo; --sim for the simulator)")
    p.add_argument("--sim", action="store_true")
    p.add_argument("--profile", choices=PROFILES, default="steps"); p.add_argument("--seed", type=int)
    p.add_argument("--port", help="Nucleo serial port, or auto (default)"); p.add_argument("--baud", type=int, default=115200)
    _add_gt_args(p)
    p.add_argument("--replay", help="recorded CSV to play back"); p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--window", type=float, default=30.0, help="metrics/fit window, s")
    p.add_argument("--host", default="127.0.0.1"); p.add_argument("--http-port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--live-reload", action="store_true", help="dev: reload open pages when dashboard files change")
    p.add_argument("--reopen", action="store_true", help=argparse.SUPPRESS)  # restarted by the launcher: page is open
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("record", help="headless session recording (same CSV + JSON as the dashboard)")
    p.add_argument("--port"); p.add_argument("--baud", type=int, default=115200); _add_gt_args(p)
    p.add_argument("--params", help="calibration JSON for the PC preview")
    p.add_argument("--duration", type=float, default=120); p.add_argument("--label")
    p.set_defaults(fn=cmd_record)

    p = sub.add_parser("sim", help="generate a synthetic run CSV")
    p.add_argument("--profile", choices=PROFILES, default="steps")
    p.add_argument("--duration", type=float, default=90); p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out"); p.set_defaults(fn=cmd_sim)

    for name, fn, hlp in (("fit", cmd_fit, "fit parameters to a recorded run and export"),
                          ("report", cmd_report, "metrics for a run with given params")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("csv")
        p.add_argument("--params", help="calibration JSON to start from")
        p.add_argument("--window", type=float, help="only the last N seconds")
        if name == "fit":
            p.add_argument("--method", choices=fit.METHODS, default="robust_lsq")
            p.add_argument("--free", help="comma-separated params to fit (default per method)")
            p.add_argument("--lag-ms", type=float, default=0.0, help="shift sensor time before fitting")
            p.add_argument("--cv", type=int, default=0, help="blocked k-fold cross-validation")
            p.add_argument("--install", action="store_true", help="also write firmware/include/calibration_generated.h")
        else:
            p.add_argument("--png", help="save a plot")
        p.set_defaults(fn=fn)

    p = sub.add_parser("session", help="per-parameter-set results of a recorded session")
    p.add_argument("json", help="runs/run_*.json (or the matching .csv)"); p.add_argument("--png", help="save a plot")
    p.set_defaults(fn=cmd_session)

    p = sub.add_parser("export", help="regenerate header/.cmd from a calibration JSON")
    p.add_argument("json"); p.add_argument("--install", action="store_true"); p.set_defaults(fn=cmd_export)

    p = sub.add_parser("check", help="verify the 200 Hz / 115200 stream: rate, drops, bad lines (exit 1 on fail)")
    p.add_argument("--port", required=True); p.add_argument("--baud", type=int, default=115200)
    p.add_argument("-d", "--duration", type=float, default=20.0)
    p.add_argument("--debug", action="store_true", help="use the board's sample counter (MODE D) to count every drop")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("get", help="save the board's live calibration (GET) as JSON: calibration file on demand")
    p.add_argument("--port", required=True); p.add_argument("--baud", type=int, default=115200)
    p.add_argument("-o", "--out"); p.set_defaults(fn=cmd_get)

    p = sub.add_parser("push", help="send a calibration JSON to the board with SET")
    p.add_argument("json"); p.add_argument("--port", required=True); p.add_argument("--baud", type=int, default=115200)
    p.set_defaults(fn=cmd_push)

    p = sub.add_parser("weights", help="interactive load-cell calibration with known masses")
    p.add_argument("--port", required=True); p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--old", type=float, help="current lc_n_per_count if GET is unavailable")
    p.set_defaults(fn=cmd_weights)

    p = sub.add_parser("stream", help="live load-cell counts and force in the terminal (Ctrl-C to stop)")
    p.add_argument("--port", help="read the board directly (stop the dashboard first); default: the dashboard")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--host", default="127.0.0.1"); p.add_argument("--http-port", type=int, default=8765)
    p.add_argument("--interval", type=float, default=0.1, help="seconds per printed line")
    p.add_argument("--span", type=float, default=2000.0, help="counts from centre to the end of the bar")
    p.set_defaults(fn=cmd_stream)

    p = sub.add_parser("tunnel", help="score a Wind Tunnel Logger run against the tunnel: MSE, bias, σ, drift")
    p.add_argument("run", nargs="?", help="run name or any of its CSV files (default: the newest run)")
    p.add_argument("--list", action="store_true", help="list the saved runs")
    p.set_defaults(fn=cmd_tunnel)

    p = sub.add_parser("tunnel-fit", help="fit a switchable calibration profile to recordings with noisy ground truth")
    p.add_argument("runs", nargs="*", help="dashboard recordings with force and ground truth (calibration/runs/run_*.csv)")
    p.add_argument("--name", default="tunnel", help="profile name: calibration/profiles/<name>.json")
    p.add_argument("--label", help="one line shown in the dashboard's calibration picker")
    p.add_argument("--zero", choices=("fit", "tare"), default="fit",
                   help="fit: F0 is the runs' mean zero (matches that day's ground truth); tare: F0 = 0, trust TARE")
    p.add_argument("--gt-cutoff", type=float, default=0.5, help="ground-truth low-pass for the curve fit, Hz")
    p.add_argument("--filter-cutoff", type=float, default=5.0, help="ground-truth low-pass for the filter search, Hz")
    p.add_argument("--dry-run", action="store_true", help="print the fit, write nothing")
    p.add_argument("--list", action="store_true", help="list the saved profiles")
    p.set_defaults(fn=cmd_tunnel_fit)

    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
