"""Calibration profiles fitted to tunnel recordings whose ground truth is noisy (the wood tunnel's GIGA pitot).

A profile is a named set of airspeed settings (sqrt_k, sqrt_x0, lc_n_per_count, the output filter) kept beside the
default calibration in calibration/profiles/<name>.json, with a firmware header firmware/include/profiles/<name>/calibration_profile.h
(built by its own PlatformIO env) and a SET script, so the board can be switched between them.

The fit uses dashboard recordings (calibration/runs/run_*.csv: force, ρ and ground truth on one 200 Hz time base).
The ground truth is cleaned before it is used:

1. In pressure, not speed: q = ½·ρ·v². Force is linear in q, and averaging √(noisy q) would bias the speed low.
2. Spikes out: a sample more than SPIKE_SIGMA robust deviations from its 0.25 s running median becomes the median.
3. Zero-phase low-pass (Butterworth run forwards and backwards) at GT_CUTOFF_HZ. The GIGA's noise is incoherent
   with the plate above about 0.2 Hz, while the tunnel's speed changes over seconds. Filtering both ways adds no
   delay. The force gets the same filter, so the two signals keep the same bandwidth.

Static map. The model is 2q = k·(F − F0). It is fitted on the cleaned signals, with a separate F0 for each run,
because the zero moved between runs (tare drift, or the GIGA's own zero). Regressing q on F and F on q give
different k when both signals carry error (here mostly the plate's slow creep), so k is their geometric mean
(reduced major axis). The two one-way fits are reported as its range. The profile's F0 is then the runs' mean (zero="fit": matches
the GIGA as it read that day). With zero="tare", F0 is 0 (trust a fan-off TARE) and k is refitted through the
origin, since the slope fitted beside free zeros only holds together with those zeros.

Output filter. EMA α / Butterworth cutoff and lead_tau are searched by running the firmware chain (models.apply) on
the raw force, scored against the despiked ground truth low-passed at DYN_CUTOFF_HZ (zero-phase). That cutoff is
deliberately well above the static one: scoring against a heavily smoothed reference would reward a heavily
smoothed (laggy) board. The GIGA's noise is independent of the board, so the noise left in the reference
adds about the same MSE to every candidate and does not change which candidate scores best. Candidates within
TIE_TOL of the best count as equal, and of those the least lead and the least smoothing wins.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.ndimage import median_filter
from scipy.signal import butter, filtfilt

from . import export, metrics, models
from .data import load_csv
from .recorder import load_session

ROOT = Path(__file__).resolve().parents[1]                     # calibration/
PROFILE_DIR = ROOT / "profiles"
HEADER_DIR = ROOT.parent / "firmware" / "include" / "profiles"

FS = models.FS
GT_CUTOFF_HZ = 0.5        # static fit: where the GIGA's noise takes over from the tunnel's real speed changes
DYN_CUTOFF_HZ = 5.0       # filter search: removes GIGA noise faster than any fan change, adds no delay
SPIKE_WIN_S, SPIKE_SIGMA = 0.25, 4.0
EDGE_S = 3.0              # trimmed from each end of a run (filter start-up, the recorder's warm-up)
DECIMATE = 20             # static fit on 10 Hz samples: the cleaned signals carry nothing faster

# The settings a profile owns. Everything else (thermometers, pressure, the tare) stays as the board has it.
PROFILE_KEYS = ("sqrt_k", "sqrt_x0", "lc_n_per_count", "filter_kind", "ema_alpha", "lpf_hz", "lead_tau")
EMA_GRID = (0.01, 0.02, 0.03, 0.05, 0.08, 0.12, 0.2, 0.3, 0.5)
LPF_GRID = (0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0, 10.0)
LEAD_GRID = (0.0, 0.025, 0.05, 0.1, 0.15, 0.2)
TIE_TOL = 0.01            # filter candidates this close to the best MSE count as equal


# ------------------------------------------------------------------------------------------------ loading
def load_recording(path: str | Path) -> dict:
    """A dashboard recording as 200 Hz arrays plus the settings the board ran (its first parameter set).
    Refuses a run without force and ground truth, or whose load-cell scale changed during the run."""
    path = Path(path)
    if path.suffix != ".csv":
        path = path.with_suffix(".csv")
    run = load_csv(path)
    if "feature" not in run.sensor or not len(run.t_g):
        raise ValueError(f"{path.name}: needs the force (feature) and ground truth columns of a dashboard recording")
    gt = run.gt_on_sensor()
    sidecar = path.with_suffix(".json")
    psets = load_session(sidecar)["param_sets"] if sidecar.exists() else []
    flown = {**models.default_params(), **(psets[0]["params"] if psets else {})}
    scales = {p["params"].get("lc_n_per_count") for p in psets}
    if len(scales) > 1:
        raise ValueError(f"{path.name}: lc_n_per_count changed during the run {sorted(scales)}; the force units differ")
    rho = models.density(flown, {"rho": run.sensor.get("rho", np.full(len(run.t_s), np.nan))}, len(run.t_s))
    try:
        shown = str(path.resolve().relative_to(ROOT.parent))          # repo-relative in the profile
    except ValueError:
        shown = str(path)
    return {"name": path.stem, "path": shown, "t": run.t_s - run.t_s[0], "gt": gt,
            "feature": models._ffill(run.sensor["feature"]), "rho": rho, "params": flown}


# ------------------------------------------------------------------------------------------ ground truth
def despike(x: np.ndarray, fs: float = FS, win_s: float = SPIKE_WIN_S, nsig: float = SPIKE_SIGMA):
    """Hampel filter: samples far from the running median (in robust σ) are replaced by it. Returns (x, n)."""
    x = models._ffill(x)
    n = max(3, int(win_s * fs) | 1)
    med = median_filter(x, size=n, mode="nearest")
    mad = median_filter(np.abs(x - med), size=n, mode="nearest") * 1.4826
    bad = np.abs(x - med) > nsig * np.maximum(mad, 1e-9)
    return np.where(bad, med, x), int(bad.sum())


def zero_phase(x: np.ndarray, cutoff_hz: float, fs: float = FS) -> np.ndarray:
    """2nd-order Butterworth run forwards and backwards: no delay, 4th-order roll-off."""
    b, a = butter(2, cutoff_hz / (fs / 2))
    return filtfilt(b, a, x, padlen=min(len(x) - 1, int(3 * fs / cutoff_hz)))


def clean_gt(rec: dict, cutoff_hz: float = GT_CUTOFF_HZ) -> dict:
    """The recording's ground truth in pressure, despiked, and low-passed without delay."""
    q_raw = 0.5 * rec["rho"] * models._ffill(rec["gt"]) ** 2
    q, spikes = despike(q_raw)
    q_clean = np.maximum(zero_phase(q, cutoff_hz), 0.0)
    return {"q_raw": q_raw, "q": q, "q_clean": q_clean, "v_clean": np.sqrt(2 * q_clean / rec["rho"]),
            "v_despiked": np.sqrt(2 * q / rec["rho"]), "spikes": spikes}


def _trim(n: int) -> slice:
    e = int(EDGE_S * FS)
    return slice(e, max(e + 1, n - e))


# ----------------------------------------------------------------------------------------------- static
def _static_rows(recs, cutoff_hz):
    rows = []
    for i, r in enumerate(recs):
        c = clean_gt(r, cutoff_hz)
        sl = _trim(len(r["t"]))
        q = c["q_clean"][sl][::DECIMATE]
        f = zero_phase(r["feature"], cutoff_hz)[sl][::DECIMATE]
        rows.append((i, q, f))
    return rows


def fit_static(recs: list[dict], cutoff_hz: float = GT_CUTOFF_HZ, zero: str = "fit") -> dict:
    """sqrt_k from 2q = k·(F − F0_run) on the cleaned signals, F0 per run (see module doc)."""
    if zero not in ("fit", "tare"):
        raise ValueError("zero is 'fit' or 'tare'")
    rows = _static_rows(recs, cutoff_hz)
    n = len(recs)

    def design(xcol):
        a, y = [], []
        for i, q, f in rows:
            x, yy = (f, 2 * q) if xcol == "F" else (2 * q, f)
            block = np.zeros((len(x), 1 + n))
            block[:, 0], block[:, 1 + i] = x, 1.0
            a.append(block), y.append(yy)
        return np.vstack(a), np.concatenate(y)

    a, y = design("F")                  # 2q = k·F + c_i      -> k
    c_qf, *_ = np.linalg.lstsq(a, y, rcond=None)
    a, y = design("q")                  # F = 2q/k + F0_i     -> 1/k
    c_fq, *_ = np.linalg.lstsq(a, y, rcond=None)
    k_qf, k_fq = float(c_qf[0]), float(1.0 / c_fq[0])
    if not (k_qf > 0 and k_fq > 0):
        raise ValueError("force does not rise with the tunnel's pressure in these runs: no fit")
    if zero == "tare":                  # F0 = 0 for every run: k through the origin, both ways
        f_all = np.concatenate([f for _, _, f in rows])
        q2_all = np.concatenate([2 * q for _, q, _ in rows])
        qf, ff, qq = float(np.sum(q2_all * f_all)), float(np.sum(f_all * f_all)), float(np.sum(q2_all * q2_all))
        k_qf, k_fq = qf / ff, qq / qf
    k = math.sqrt(k_qf * k_fq)
    x0_runs = [float(np.mean(f - 2 * q / k)) for _, q, f in rows]
    weights = [len(q) for _, q, _ in rows]
    x0_mean = float(np.average(x0_runs, weights=weights))
    return {"sqrt_k": k, "sqrt_x0": x0_mean if zero == "fit" else 0.0, "zero": zero,
            "k_range": sorted((k_qf, k_fq)), "x0_runs": dict(zip((r["name"] for r in recs), x0_runs)),
            "x0_runs_pa": {r["name"]: x0 * k / 2 for r, x0 in zip(recs, x0_runs)},
            "still_air_mps": math.sqrt(max(k * -x0_mean / models.RHO_REF, 0.0)) if zero == "fit" else 0.0,
            "q_range_pa": [float(min(q.min() for _, q, _ in rows)), float(max(q.max() for _, q, _ in rows))],
            "cutoff_hz": cutoff_hz}


# -------------------------------------------------------------------------------------------- dynamics
def reference(rec: dict, cutoff_hz: float = DYN_CUTOFF_HZ) -> np.ndarray:
    """Ground truth to score the board against: despiked, then low-passed without delay."""
    c = clean_gt(rec, cutoff_hz)
    return c["v_clean"]


def score(params: dict, rec: dict, gt: np.ndarray) -> dict:
    v = models.apply({**rec["params"], **params}, {"feature": rec["feature"], "rho": rec["rho"]})
    sl = _trim(len(v))
    return metrics.compute(v[sl], gt[sl], FS, with_lag=False)


def tune_dynamics(recs: list[dict], static: dict, cutoff_hz: float = DYN_CUTOFF_HZ) -> dict:
    """Grid search of filter and lead on all runs together (mean MSE); returns the best and the table."""
    refs = [reference(r, cutoff_hz) for r in recs]
    cands = [{"filter_kind": float(models.FILTER_EMA), "ema_alpha": a} for a in EMA_GRID]
    cands += [{"filter_kind": float(models.FILTER_BUTTERWORTH), "lpf_hz": f} for f in LPF_GRID]
    table = []
    for c in cands:
        for lead in LEAD_GRID:
            p = {"sqrt_k": static["sqrt_k"], "sqrt_x0": static["sqrt_x0"], "lead_tau": lead, **c}
            mse = float(np.mean([score(p, r, g)["mse"] for r, g in zip(recs, refs)]))
            table.append({**p, "mse": mse})
    table.sort(key=lambda row: row["mse"])
    # The surface is flat near the top: among candidates within TIE_TOL of the best, take the least lead
    # (it amplifies noise and overshoots steps), then the least smoothing (the least lag on a step).
    near = [row for row in table if row["mse"] <= table[0]["mse"] * (1 + TIE_TOL)]
    pick = min(near, key=lambda row: (row["lead_tau"], -_bandwidth_hz(row)))
    best = {k: v for k, v in pick.items() if k in ("filter_kind", "ema_alpha", "lpf_hz", "lead_tau")}
    return {"params": best, "mse": pick["mse"], "best_mse": table[0]["mse"], "table": table, "cutoff_hz": cutoff_hz}


def _bandwidth_hz(p: dict) -> float:
    """-3 dB frequency of the output smoother, to compare an EMA with a Butterworth."""
    if models.filter_kind_of(p) == models.FILTER_BUTTERWORTH:
        return float(p["lpf_hz"])
    a = float(p["ema_alpha"])
    return -math.log(1 - a) * FS / (2 * math.pi) if a < 1 else FS / 2


# ----------------------------------------------------------------------------------------- calibrate
def _scores(params: dict, recs: list[dict]) -> dict:
    out = {}
    for r in recs:
        raw = models._ffill(r["gt"])
        m_raw, m_ref = score(params, r, raw), score(params, r, reference(r))
        out[r["name"]] = {"mse": m_raw["mse"], "bias": m_raw["bias"], "sigma": m_raw["sigma"],
                          "mse_clean": m_ref["mse"], "bias_clean": m_ref["bias"]}
    return out


def noise_floor(rec: dict) -> float:
    """MSE between the raw ground truth and its cleaned self: what even a perfect sensor would score."""
    sl = _trim(len(rec["t"]))
    c = clean_gt(rec, GT_CUTOFF_HZ)
    raw = models._ffill(rec["gt"])
    return float(np.mean((raw[sl] - c["v_clean"][sl]) ** 2))


def calibrate(paths: list[str | Path], zero: str = "fit", gt_cutoff_hz: float = GT_CUTOFF_HZ,
              dyn_cutoff_hz: float = DYN_CUTOFF_HZ) -> dict:
    """Fit a profile to the recordings. Returns the profile record (not yet written)."""
    recs = [load_recording(p) for p in paths]
    scales = {r["params"]["lc_n_per_count"] for r in recs}
    if len(scales) > 1:
        raise ValueError(f"the runs used different lc_n_per_count {sorted(scales)}: fit them separately")
    static = fit_static(recs, gt_cutoff_hz, zero)
    dyn = tune_dynamics(recs, static, dyn_cutoff_hz)
    params = {"sqrt_k": static["sqrt_k"], "sqrt_x0": static["sqrt_x0"], "lc_n_per_count": scales.pop(),
              **{"ema_alpha": recs[0]["params"]["ema_alpha"], "lpf_hz": recs[0]["params"]["lpf_hz"]}, **dyn["params"]}
    before = {r["name"]: _scores({}, [r])[r["name"]] for r in recs}   # each run with the settings it flew
    after = _scores(params, recs)
    holdout = {}
    if len(recs) > 1:                     # static map fitted on the other runs, scored on this one
        for i, r in enumerate(recs):
            others = recs[:i] + recs[i + 1:]
            st = fit_static(others, gt_cutoff_hz, zero)
            holdout[r["name"]] = _scores({**params, "sqrt_k": st["sqrt_k"], "sqrt_x0": st["sqrt_x0"]}, [r])[r["name"]]
    return {
        "model": models.MODEL_ID, "method": models.METHOD_MACRO,
        "params": {k: float(params[k]) for k in PROFILE_KEYS},
        "fit": {"static": static, "dynamics": {k: v for k, v in dyn.items() if k != "table"},
                "dynamics_top": dyn["table"][:8],
                "gt_cleaning": {"pressure_domain": True, "spike_window_s": SPIKE_WIN_S, "spike_sigma": SPIKE_SIGMA,
                                "static_cutoff_hz": gt_cutoff_hz, "filter_search_cutoff_hz": dyn_cutoff_hz,
                                "zero_phase": True,
                                "spikes_replaced": {r["name"]: clean_gt(r)["spikes"] for r in recs},
                                "noise_floor_mse": {r["name"]: noise_floor(r) for r in recs}},
                "sources": [r["path"] for r in recs]},
        "metrics_before": before, "metrics_after": after, "metrics_holdout": holdout,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


# ------------------------------------------------------------------------------------------- profiles
def _slug(name: str) -> str:
    s = "".join(ch if ch.isalnum() else "_" for ch in name.strip().lower()).strip("_")
    if not s:
        raise ValueError("a profile needs a name")
    return s


def header_text(record: dict) -> str:
    p = record["params"]
    active = "lpf_hz" if models.filter_kind_of(p) == models.FILTER_BUTTERWORTH else "ema_alpha"
    st = record["fit"]["static"]
    lines = [f"// Calibration profile \"{record['name']}\": generated by `python -m calib tunnel-fit`. Do not edit.",
             f"// {record.get('label', '')}".rstrip(),
             f"// Fitted {record['created']} from {', '.join(Path(s).stem for s in record['fit']['sources'])}.",
             f"// Zero: {'fitted to the runs' if st['zero'] == 'fit' else 'from TARE (F0 = 0)'}; "
             f"sqrt_k range {st['k_range'][0]:.1f}..{st['k_range'][1]:.1f}.",
             "// Built by its own env (firmware/platformio.ini); replaces calibration_generated.h, and every",
             "// setting not listed keeps its calibration.h default.",
             "#pragma once", "", f"#define AIRSPEED_METHOD {models.METHOD_MACRO}", ""]
    for k in PROFILE_KEYS:
        if k in ("ema_alpha", "lpf_hz") and k != active:
            continue
        lines.append(f"#define CAL_{k.upper()} {export._c_float(p[k])}")
    return "\n".join(lines) + "\n"


def push_lines(params: dict) -> list[str]:
    return [f"SET {k} {export._num(params[k])}" for k in PROFILE_KEYS if k in params]


def write_profile(record: dict, name: str, label: str = "", profile_dir: Path | None = None,
                  header_dir: Path | None = None) -> dict:
    slug = _slug(name)
    record = {**record, "name": slug, "label": label or slug}
    profile_dir, header_dir = Path(profile_dir or PROFILE_DIR), Path(header_dir or HEADER_DIR)
    profile_dir.mkdir(parents=True, exist_ok=True)
    header_dir.mkdir(parents=True, exist_ok=True)
    paths = {"json": profile_dir / f"{slug}.json", "cmd": profile_dir / f"{slug}.cmd",
             "header": header_dir / slug / "calibration_profile.h"}
    paths["header"].parent.mkdir(exist_ok=True)
    paths["json"].write_text(json.dumps(record, indent=1, default=float) + "\n")
    paths["cmd"].write_text("\n".join(push_lines(record["params"]) + ["GET"]) + "\n")
    paths["header"].write_text(header_text(record))
    return {k: str(v) for k, v in paths.items()}


def list_profiles(profile_dir: Path | None = None) -> list[dict]:
    """Every saved profile, by name, as the dashboard shows them: name, label, params and the scores."""
    out = []
    for f in sorted(Path(profile_dir or PROFILE_DIR).glob("*.json")):
        try:
            rec = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if rec.get("model") != models.MODEL_ID or "params" not in rec:
            continue
        after = rec.get("metrics_after") or {}
        mses = [m["mse"] for m in after.values() if isinstance(m, dict) and "mse" in m]
        out.append({"name": rec.get("name", f.stem), "label": rec.get("label", f.stem), "created": rec.get("created"),
                    "params": {k: rec["params"][k] for k in PROFILE_KEYS if k in rec["params"]},
                    "zero": rec.get("fit", {}).get("static", {}).get("zero"),
                    "still_air_mps": rec.get("fit", {}).get("static", {}).get("still_air_mps"),
                    "mse": float(np.mean(mses)) if mses else None})
    return out


def default_profile() -> dict:
    """The settings the board boots with when no profile is chosen (the toolkit's defaults)."""
    d = models.default_params()
    return {"name": "default", "label": "Default (theory Cd·A, F₀ from TARE)", "params": {k: d[k] for k in PROFILE_KEYS}}


def summary_lines(record: dict) -> list[str]:
    st, dyn, gc = record["fit"]["static"], record["fit"]["dynamics"], record["fit"]["gt_cleaning"]
    p = record["params"]
    filt = (f"EMA α {p['ema_alpha']:g}" if models.filter_kind_of(p) == models.FILTER_EMA
            else f"Butterworth {p['lpf_hz']:g} Hz")
    lines = [f"ground truth: pressure domain, {sum(gc['spikes_replaced'].values())} spikes replaced, "
             f"zero-phase low-pass {gc['static_cutoff_hz']:g} Hz (fit) / {gc['filter_search_cutoff_hz']:g} Hz (filter search)",
             f"sqrt_k {st['sqrt_k']:.1f}  (one-way fits {st['k_range'][0]:.1f}..{st['k_range'][1]:.1f})   "
             f"Cd·A {2e4 / st['sqrt_k']:.1f} cm² in these force units",
             "zero per run: " + ", ".join(f"{n[4:19]} {x:+.4f} ({st['x0_runs_pa'][n]:+.1f} Pa)" for n, x in st["x0_runs"].items()),
             f"sqrt_x0 {st['sqrt_x0']:+.4f}  ({'mean of the runs' if st['zero'] == 'fit' else 'TARE is the zero'})"
             + (f": still air would read {st['still_air_mps']:.1f} m/s" if st["zero"] == "fit" and st["still_air_mps"] > 0 else ""),
             f"filter {filt}, lead_tau {p['lead_tau']:g} s  (searched against GT at {dyn['cutoff_hz']:g} Hz)",
             f"fitted on q {st['q_range_pa'][0]:.0f}..{st['q_range_pa'][1]:.0f} Pa: outside that it is extrapolated",
             "", f"{'run':18s} {'floor':>6s} {'before':>8s} {'after':>8s} {'holdout':>8s} {'clean GT':>9s} {'bias':>7s}"]
    for n, a in record["metrics_after"].items():
        b = record["metrics_before"][n]
        h = record.get("metrics_holdout", {}).get(n, {})
        lines.append(f"{n[4:19]:18s} {gc['noise_floor_mse'][n]:6.3f} {b['mse']:8.3f} {a['mse']:8.3f} "
                     f"{h.get('mse', float('nan')):8.3f} {a['mse_clean']:9.3f} {a['bias']:+7.3f}")
    lines.append("MSE against the raw GIGA (as scored); floor = the GIGA's own noise; holdout = static map fitted "
                 "on the other run(s); clean GT = against the despiked 5 Hz ground truth")
    return lines
