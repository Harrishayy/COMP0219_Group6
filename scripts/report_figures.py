"""Figures 5 and 6 of the COMP0219 Part 1 report, from the Wind Tunnel Logger files of the two assessed runs.

    .venv/bin/python scripts/report_figures.py [--demo DIR] [--out DIR]

Inputs: the assessed runs' <run>_aligned.csv (each 4 Hz VDAS sample paired with our stream, as the marking script
scores them), <run>_student.csv (our 200 Hz stream) and <run>_vdas.csv (the reference), by default in
data/actual demo/ (shipped in the supplementary zip, not in the repo); the demo-morning check run in data/tunnel/;
and the 4 Nov force recording in calibration/runs/ for the unfiltered spectra. Outputs demo_runs_v2.pdf (Fig. 5)
and analysis_v2.pdf (Fig. 6), with PNG previews, and prints the scores, Table II's error shares, the step metrics,
the spectral peaks and the filter attenuation that the text quotes.

One style for every figure: reference black, Run 1 blue, Run 2 orange, check run grey; speed axes 0-11 m/s; error
axes +-3 m/s; the brief's range 2-10 m/s shaded the same way everywhere; bold panel titles; key numbers in bold.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
import numpy as np
from scipy.optimize import least_squares

# ---------------------------------------------------------------------------------------- data and maths
ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "data" / "actual demo"
TUNNEL = ROOT / "data" / "tunnel"
RUNS_DIR = ROOT / "calibration" / "runs"

BAND = (2.0, 10.0)                 # the brief's range
PLATEAU_SD = 0.12                  # m/s: reference's 2 s standard deviation below this is a plateau
PLATEAU_WIN = 8                    # VDAS samples in 2 s at 4 Hz
PLATEAU_TRIM = 0.15                # trimmed from each end of a plateau
FS = 200.0                         # our stream


def _load(path: Path, cols: list[str]) -> dict[str, np.ndarray]:
    rows = list(csv.DictReader(open(path)))
    return {c: np.array([float(r[c]) if r[c] not in ("", None) else np.nan for r in rows]) for c in cols}


@dataclass
class Run:
    name: str
    t_pair: np.ndarray      # s, VDAS sample times (pairs)
    ref_pair: np.ndarray    # m/s
    est_pair: np.ndarray    # m/s, our value paired by the logger
    t_est: np.ndarray       # s, our 200 Hz stream
    est: np.ndarray
    t_ref: np.ndarray       # s, the reference (same times as the pairs)
    ref: np.ndarray

    @property
    def err(self) -> np.ndarray:
        return self.est_pair - self.ref_pair


def load_run(stem: str, folder: Path = DEMO, name: str | None = None) -> Run:
    a = _load(folder / f"{stem}_aligned.csv", ["timestamp_epoch", "ground_truth_mps", "student_mps"])
    s = _load(folder / f"{stem}_student.csv", ["timestamp_epoch", "value_raw_mps"])
    v = _load(folder / f"{stem}_vdas.csv", ["timestamp_epoch", "value_raw_mps"])
    t0 = v["timestamp_epoch"][0]
    ok = ~np.isnan(a["student_mps"])
    return Run(name or stem, a["timestamp_epoch"][ok] - t0, a["ground_truth_mps"][ok], a["student_mps"][ok],
               s["timestamp_epoch"] - t0, s["value_raw_mps"], v["timestamp_epoch"] - t0, v["value_raw_mps"])


DEMO_RUNS = {"Run 1": "Team_6_log1_20261105_111503", "Run 2": "Team_6_log2_20261105_111838"}
CHECK_RUN = "run_20261105_091624_live_drag_plate"


def scores(err: np.ndarray) -> dict[str, float]:
    if len(err) == 0:
        return {"n": 0, "mse": np.nan, "rmse": np.nan, "bias": np.nan, "sd": np.nan}
    mse = float(np.mean(err ** 2))
    return {"n": len(err), "mse": mse, "rmse": mse ** 0.5, "bias": float(err.mean()), "sd": float(err.std())}


def plateaus(t: np.ndarray, ref: np.ndarray, trim: float = PLATEAU_TRIM) -> list[tuple[float, float]]:
    """Windows of at least 1 s where the reference's centred 2 s standard deviation stays below PLATEAU_SD (and it
    is not at rest), trimmed by `trim` at each end. Untrimmed (trim=0) they give Table II's regimes exactly."""
    n = len(ref)
    sd = np.full(n, np.inf)
    h = PLATEAU_WIN // 2
    for i in range(h, n - h):
        sd[i] = np.std(ref[i - h:i + h])
    steady = (sd < PLATEAU_SD) & (ref > 0.5)
    out, i = [], 0
    while i < n:
        if steady[i]:
            j = i
            while j + 1 < n and steady[j + 1]:
                j += 1
            t_a, t_b = t[i], t[j]
            cut = (t_b - t_a) * trim
            if t_b - t_a >= 1.0:
                out.append((t_a + cut, t_b - cut))
            i = j + 1
        else:
            i += 1
    return out


REGIMES = ["Speed changes", "Steady ≥9.5 m/s", "Steady 6–9.5 m/s", "Reference = 0"]


def regime_of(run: Run) -> np.ndarray:
    """Label every scored pair with its regime (Table II)."""
    lab = np.full(len(run.ref_pair), REGIMES[0], dtype=object)
    for a, b in plateaus(run.t_ref, run.ref, trim=0.0):
        m = (run.t_pair >= a) & (run.t_pair <= b)
        hi = m & (run.ref_pair >= 9.5)
        mid = m & (run.ref_pair >= 6) & (run.ref_pair < 9.5)
        lab[hi] = REGIMES[1]
        lab[mid] = REGIMES[2]
    lab[run.ref_pair == 0] = REGIMES[3]
    return lab


def rolling_rmse(t: np.ndarray, err: np.ndarray, win_s: float = 5.0) -> np.ndarray:
    out = np.empty_like(err)
    for i, ti in enumerate(t):
        m = np.abs(t - ti) <= win_s / 2
        out[i] = np.sqrt(np.mean(err[m] ** 2))
    return out


def butterworth_mag(f: np.ndarray, fc: float, fs: float = FS) -> np.ndarray:
    """|H| of the firmware's RBJ biquad (Q = 1/sqrt 2) at frequencies f (Hz)."""
    w0 = 2 * np.pi * fc / fs
    cw, al = np.cos(w0), np.sin(w0) / (2 / np.sqrt(2))
    a0 = 1 + al
    b = np.array([(1 - cw) / 2, 1 - cw, (1 - cw) / 2]) / a0
    a = np.array([1, -2 * cw / a0, (1 - al) / a0])
    z = np.exp(-1j * 2 * np.pi * f / fs)
    return np.abs((b[0] + b[1] * z + b[2] * z ** 2) / (a[0] + a[1] * z + a[2] * z ** 2))


def welch(x: np.ndarray, fs: float = FS, nper: int = 1024):
    from scipy.signal import welch as _w
    return _w(x - np.mean(x), fs=fs, nperseg=min(nper, len(x)), detrend="linear")


# ---------------------------------------------------------------------------------------------- figures
OUT = ROOT / "docs" / "report_figures" / "figures"   # local; --out to change

C = {"Run 1": "#1f5fa8", "Run 2": "#d9661a", "check": "#8c8c8c", "ref": "black", "band": "#e3eedb"}
CUTOFF = {"Run 1": 0.75, "Run 2": 0.51}   # firmware Butterworth cut-off per run (calibration 1 / 2)
SPEED_LIM, ERR_LIM = (0, 11), (-3, 3)
EXP_N = 1.82                               # drag exponent from calibration 1 (report Sec. IV-C)
RES_HZ = 15.6                              # resonance quoted in the report (\R{ResFreq}); our peaks are 15.1-15.9 Hz
TARGET_D = 0.086                           # m, target diameter for the vortex-shedding check (CHECK with CAD)
FULL_W = 7.16                              # in, IEEE two-column text width
BOX = dict(boxstyle="round,pad=0.25", fc="white", ec="0.7", lw=0.5)

plt.rcParams.update({
    "font.family": "serif", "font.serif": ["Times New Roman", "Times", "STIXGeneral", "DejaVu Serif"],
    "mathtext.fontset": "stix", "font.size": 8, "axes.titlesize": 8, "axes.titleweight": "bold",
    "axes.labelsize": 8, "legend.fontsize": 6.5, "xtick.labelsize": 7, "ytick.labelsize": 7, "axes.linewidth": 0.6,
    "lines.linewidth": 1.0, "xtick.major.width": 0.6, "ytick.major.width": 0.6, "legend.frameon": True,
    "legend.framealpha": 0.92, "legend.edgecolor": "0.7", "legend.fancybox": True, "savefig.dpi": 300,
})


def bold(x: str) -> str:
    """A number set in bold inside normal text."""
    return rf"$\mathbf{{{x}}}$"


def band(ax, horizontal=True):
    (ax.axhspan if horizontal else ax.axvspan)(*BAND, color=C["band"], zorder=0, lw=0)


def label(ax, x, y, text, ha="left", va="top", size=6.5, color="black"):
    ax.text(x, y, text, transform=ax.transAxes, ha=ha, va=va, fontsize=size, color=color, bbox=BOX, zorder=5)


def window_scores(r, a, b):
    m = (r.t_pair >= a) & (r.t_pair <= b)
    return scores(r.err[m])


def step_metrics(r, t_step, before=(-4, -0.5), after=(4, 9)):
    """10-90 % rise times of reference and estimate, overshoot and steady error for one step."""
    def mean_in(t, x, a, b):
        return float(np.mean(x[(t >= t_step + a) & (t <= t_step + b)]))
    lo, hi = mean_in(r.t_ref, r.ref, *before), mean_in(r.t_ref, r.ref, *after)
    e_lo, e_hi = mean_in(r.t_est, r.est, *before), mean_in(r.t_est, r.est, *after)

    def cross(t, x, level):
        m = (t >= t_step - 2) & (t <= t_step + after[1])
        idx = np.nonzero(x[m] >= level)[0]
        return float(t[m][idx[0]]) if len(idx) else np.nan
    span, e_span = hi - lo, e_hi - e_lo
    rt_ref = cross(r.t_ref, r.ref, lo + 0.9 * span) - cross(r.t_ref, r.ref, lo + 0.1 * span)
    rt_est = cross(r.t_est, r.est, e_lo + 0.9 * e_span) - cross(r.t_est, r.est, e_lo + 0.1 * e_span)
    m = (r.t_est >= t_step) & (r.t_est <= t_step + after[1])
    overshoot = (float(np.max(r.est[m])) - e_hi) / e_span
    return dict(lo=lo, hi=hi, e_hi=e_hi, rt_ref=rt_ref, rt_est=rt_est, overshoot=overshoot, steady_err=e_hi - hi)


def zoom_score(ax, s, x=0.97, y=0.96, ha="right", va="top"):
    label(ax, x, y, f"RMSE {bold(f'{s['rmse']:.2f}')} m/s\nMSE {bold(f'{s['mse']:.3f}')} m²/s²", ha=ha, va=va)


# ------------------------------------------------------------------------------------------------ Fig. 5
ZOOMS = {"c": ("both", 0, 13), "d": ("Run 2", 64, 78), "e": ("Run 2", 80, 111)}


def fig_demo_runs(runs):
    fig = plt.figure(figsize=(FULL_W, 4.0))
    outer = fig.add_gridspec(2, 1, height_ratios=[2.05, 1.55], hspace=0.42, left=0.065, right=0.995,
                             top=0.87, bottom=0.095)
    upper = outer[0].subgridspec(2, 2, height_ratios=[1.35, 0.7], hspace=0.12, wspace=0.12)
    top, mid = [upper[0, 0], upper[0, 1]], [upper[1, 0], upper[1, 1]]
    bot = outer[1].subgridspec(1, 3, wspace=0.22, width_ratios=[1.2, 1, 1.25])

    for col, name in enumerate(runs):
        r = runs[name]
        cal = "calibration 1" if name == "Run 1" else "calibration 2"
        ax = fig.add_subplot(top[col])
        band(ax)
        ax.step(r.t_ref, r.ref, where="post", color=C["ref"], lw=0.9)
        ax.plot(r.t_est, r.est, color=C[name], lw=0.8)
        ax.set(xlim=(0, 112), ylim=SPEED_LIM, yticks=range(0, 11, 2))
        ax.tick_params(labelbottom=False)
        if col == 0:
            ax.set_ylabel("Airspeed (m/s)")
        else:
            ax.set_yticklabels([])
        ax.set_title(f"({'ab'[col]}) {name}, {cal}", loc="left")
        s_all = scores(r.err)
        s_band = scores(r.err[(r.ref_pair >= BAND[0]) & (r.ref_pair <= BAND[1])])
        ax.text(0.42, 0.04,
                f"All scored pairs: MSE {bold(f'{s_all['mse']:.3f}')} m²/s², RMSE {bold(f'{s_all['rmse']:.3f}')} m/s\n"
                f"Reference in 2–10 m/s: MSE {s_band['mse']:.3f} m²/s², RMSE {s_band['rmse']:.3f} m/s",
                transform=ax.transAxes, ha="center", va="bottom", fontsize=6.5, bbox=BOX, zorder=5)
        for letter, (which, a, b) in ZOOMS.items():
            if which in (name, "both"):
                ax.add_patch(Rectangle((a, 0.15), b - a, 10.75, fill=False, ec="0.35", lw=0.6, ls="--", zorder=4))
                ax.text(a + 0.6, 10.8, letter, fontsize=7.5, fontweight="bold", va="top", color="0.2", zorder=5,
                        bbox=dict(fc="white", ec="none", pad=0.4))

        ax2 = fig.add_subplot(mid[col])
        inb = (r.ref_pair >= BAND[0]) & (r.ref_pair <= BAND[1])
        ax2.axhline(0, color="0.6", lw=0.5)
        ax2.scatter(r.t_pair[inb], r.err[inb], s=3, color=C[name], lw=0, zorder=3)
        ax2.scatter(r.t_pair[~inb], r.err[~inb], s=4, facecolor="none", edgecolor="0.55", lw=0.4, zorder=3)
        ax2.plot(r.t_pair, rolling_rmse(r.t_pair, r.err), color="black", lw=0.9, zorder=4)
        ax2.set(xlim=(0, 112), ylim=ERR_LIM, xlabel="Time (s)")
        if col == 0:
            ax2.set_ylabel("Error (m/s)")
        else:
            ax2.set_yticklabels([])

    # one key for the whole figure, centred over the plots: only what the colours mean
    centre = (0.065 + 0.995) / 2   # middle of the plotting area (gridspec left/right)
    fig.legend([Line2D([], [], color=C["ref"], lw=1.0), Line2D([], [], color=C["Run 1"], lw=1.2),
                Line2D([], [], color=C["Run 2"], lw=1.2), Rectangle((0, 0), 1, 1, color=C["band"])],
               ["Reference (pitot, 4 Hz)", "Our estimate, Run 1", "Our estimate, Run 2", "Brief's range, 2–10 m/s"],
               loc="upper center", ncol=4, bbox_to_anchor=(centre, 0.995), handlelength=1.8, columnspacing=2.4,
               fontsize=7, frameon=False)

    # (c) spin-up from rest: one small panel per run on the same time axis, error between the curves shaded
    spin = bot[0].subgridspec(2, 1, hspace=0.1)
    for i, name in enumerate(runs):
        r = runs[name]
        t0 = r.t_ref[np.nonzero(r.ref > 0)[0][0]]
        ax = fig.add_subplot(spin[i])
        band(ax)
        te = r.t_est - t0
        m = (te >= -2) & (te <= 5)
        ref_hold = r.ref[np.clip(np.searchsorted(r.t_ref - t0, te[m], side="right") - 1, 0, len(r.ref) - 1)]
        ax.fill_between(te[m], r.est[m], ref_hold, color=C[name], alpha=0.25, lw=0, zorder=1)
        ax.step(r.t_ref - t0, r.ref, where="post", color=C["ref"], lw=0.9, zorder=2)
        ax.plot(te, r.est, color=C[name], lw=1.1, zorder=3)
        t_leave = te[np.nonzero((r.est > 0.2) & (te > -3))[0][0]]
        s = window_scores(r, t0 - 3, t0 + 5)
        cal = "calibration 1" if name == "Run 1" else "calibration 2"
        ax.text(0.03, 0.94, f"{name}, {cal}", transform=ax.transAxes, fontsize=6.5, fontweight="bold", va="top",
                color=C[name], zorder=5)
        ax.text(0.97, 0.07, f"{bold(f'{-t_leave:.1f}')} s early,  RMSE {bold(f'{s['rmse']:.2f}')} m/s",
                transform=ax.transAxes, fontsize=6.5, ha="right", va="bottom", zorder=5,
                bbox=dict(fc="white", ec="none", alpha=0.9, pad=0.6))
        ax.set(xlim=(-2, 5), ylim=SPEED_LIM, yticks=[0, 5, 10])
        if i == 0:
            ax.tick_params(labelbottom=False)
            ax.set_title("(c) Spin-up from rest", loc="left")
        else:
            ax.set_xlabel("Time from first non-zero reference (s)")
    fig.text(0.012, 0.255, "Airspeed (m/s)", rotation=90, va="center", fontsize=8)

    # (d) step, annotated with the template's rise time, overshoot and steady-state error
    r = runs["Run 2"]
    ax = fig.add_subplot(bot[1])
    band(ax)
    ax.step(r.t_ref, r.ref, where="post", color=C["ref"], lw=0.9)
    ax.plot(r.t_est, r.est, color=C["Run 2"], lw=1.0)
    sm = step_metrics(r, 69.5)
    for frac in (0.1, 0.9):
        ax.axhline(sm["lo"] + frac * (sm["hi"] - sm["lo"]), color="0.5", lw=0.5, ls=":")
    ax.annotate("", xy=(76.9, sm["e_hi"]), xytext=(76.9, sm["hi"]),
                arrowprops=dict(arrowstyle="<->", lw=0.7, mutation_scale=6, shrinkA=0, shrinkB=0))
    ax.text(76.6, sm["e_hi"] - 0.2, f"steady error {bold(f'{sm['steady_err']:+.2f}')} m/s", fontsize=6.5,
            ha="right", va="top")
    s = window_scores(r, 64, 78)
    label(ax, 0.97, 0.04, f"10–90 % rise: estimate {bold(f'{sm['rt_est']:.1f}')} s, reference {sm['rt_ref']:.1f} s\n"
                          f"overshoot {bold(f'{100 * sm['overshoot']:.0f}')} %,  RMSE {bold(f'{s['rmse']:.2f}')} m/s",
          ha="right", va="bottom")
    ax.set(xlim=(64, 78), ylim=(6.9, 10.9), xlabel="Time (s)")
    ax.set_title(f"(d) Step {sm['lo']:.1f} → {sm['hi']:.1f} m/s, Run 2", loc="left")

    # (e) ramp down to rest, then the gusts
    ax = fig.add_subplot(bot[2])
    band(ax)
    ax.step(r.t_ref, r.ref, where="post", color=C["ref"], lw=0.9)
    ax.plot(r.t_est, r.est, color=C["Run 2"], lw=1.0)
    s_ramp, s_gust = window_scores(r, 80, 93), window_scores(r, 93, 111)
    label(ax, 0.43, 0.97, f"RMSE ramp {bold(f'{s_ramp['rmse']:.2f}')}, gusts {bold(f'{s_gust['rmse']:.2f}')} m/s",
          ha="center")
    ax.set(xlim=(80, 111), ylim=SPEED_LIM, yticks=range(0, 11, 2), xlabel="Time (s)")
    ax.set_title("(e) Ramp to rest, then gusts, Run 2", loc="left")

    fig.savefig(OUT / "demo_runs_v2.pdf")
    fig.savefig(OUT / "demo_runs_v2.png", dpi=200)
    plt.close(fig)
    return sm, s_ramp, s_gust


# ------------------------------------------------------------------------------------------------ Fig. 6
def unfiltered_speed(stem="run_20261104_151732_live_drag_plate", k_f=145.1):
    """Unfiltered airspeed from a 200 Hz force recording: the calibration-1 square-root map, no filter."""
    rows = list(csv.DictReader(open(RUNS_DIR / f"{stem}.csv")))
    F = np.array([float(r["feature"]) for r in rows])
    rho = np.array([float(r["rho"]) if r["rho"] else 1.2 for r in rows])
    return np.sqrt(np.clip(k_f * F / rho, 0, None))


def steady_segments(v, min_s=8.0):
    mm = np.convolve(v, np.ones(200) / 200, mode="same")
    idx = np.arange(0, len(v), 50)
    st = np.array([np.ptp(mm[max(0, j - 300):j + 300]) < 0.35 and mm[j] > 2 for j in idx])
    out, j = [], 0
    while j < len(st):
        if st[j]:
            q = j
            while q + 1 < len(st) and st[q + 1]:
                q += 1
            a, b = idx[j] + 300, idx[q] - 300
            if (b - a) / 200 >= min_s:
                out.append((a, b))
            j = q + 1
        else:
            j += 1
    return out


def plateau_means(r):
    out = []
    for a, b in plateaus(r.t_ref, r.ref):      # trimmed by 15 % at each end, as the report states
        m = (r.t_pair >= a) & (r.t_pair <= b)
        if m.sum() >= 3:
            out.append((float(np.mean(r.ref_pair[m])), float(np.mean(r.err[m])), float(np.std(r.err[m]))))
    return np.array(out)


def fig_analysis(runs, check):
    fig = plt.figure(figsize=(FULL_W, 2.3))
    gs = fig.add_gridspec(2, 4, width_ratios=[1, 1, 1, 1.15], wspace=0.42, hspace=0.12,
                          left=0.065, right=0.99, top=0.9, bottom=0.2)

    # (a) unfiltered spectra: one fixed peak, whereas vortex shedding would move with speed
    ax = fig.add_subplot(gs[:, 0])
    v = unfiltered_speed()
    segs = sorted(steady_segments(v), key=lambda s: v[s[0]:s[1]].mean())
    cols = plt.cm.viridis(np.linspace(0.1, 0.85, len(segs)))
    peaks, handles, names = [], [], []
    for i, ((a, b), col) in enumerate(zip(segs, cols)):
        f, P = welch(v[a:b], nper=2048)
        sel = (f > 5) & (f < 40)
        peaks.append(float(f[sel][np.argmax(P[sel])]))
        mu = v[a:b].mean()
        handles.append(ax.loglog(f[1:], P[1:], color=col, lw=0.7)[0]); names.append(f"{mu:.1f} m/s")
        ax.plot([0.12 * mu / TARGET_D, 0.2 * mu / TARGET_D], [2.5e-8 * 2.6 ** i] * 2, color=col, lw=2.4,
                solid_capstyle="butt")
    handles.append(Line2D([], [], color="0.45", lw=2.4)); names.append("shedding")
    ax.axvline(RES_HZ, color="0.3", lw=0.6, ls=":")
    ax.text(RES_HZ * 1.12, 0.5, bold(f"{RES_HZ}") + " Hz", fontsize=7, va="top")
    ax.legend(handles, names, loc="lower left", handlelength=0.9, labelspacing=0.1, fontsize=5.5, borderpad=0.3,
              handletextpad=0.4)
    ax.set(xlim=(0.2, 100), ylim=(1e-8, 1), xlabel="Frequency (Hz)", ylabel="PSD ((m/s)²/Hz)")
    ax.set_title("(a) Unfiltered: resonance", loc="left")

    # (b) the filter at 10 m/s: measured output against the design applied to the unfiltered spectrum
    ax = fig.add_subplot(gs[:, 1])
    a, b = max(steady_segments(v), key=lambda s: v[s[0]:s[1]].mean())
    fu, Pu = welch(v[a:b], nper=1024)
    ax.loglog(fu[1:], Pu[1:], color="black", lw=0.7)
    atten = {}
    for name, r in runs.items():
        pa, pb = next(p for p in plateaus(r.t_ref, r.ref) if np.mean(r.ref[(r.t_ref >= p[0]) & (r.t_ref <= p[1])]) > 9.5)
        m = (r.t_est >= pa) & (r.t_est <= pb)
        f, P = welch(r.est[m], nper=1024)
        ax.loglog(f[1:], P[1:], color=C[name], lw=0.7)
        ax.loglog(fu[1:], Pu[1:] * butterworth_mag(fu[1:], CUTOFF[name]) ** 2, color=C[name], lw=0.7, ls="--")
        atten[name] = -20 * np.log10(butterworth_mag(np.array([RES_HZ]), CUTOFF[name])[0])
    floor = (0.001 ** 2 / 12) / 100
    ax.axhline(floor, color="0.5", lw=0.6, ls=":")
    ax.text(0.25, floor * 2, "rounding floor", fontsize=6, va="bottom", color="0.35")
    ax.axvline(RES_HZ, color="0.3", lw=0.6, ls=":")
    label(ax, 0.03, 0.97, f"at {RES_HZ} Hz: {bold(f'-{atten['Run 1']:.0f}')} dB (Run 1)\n"
                          f"{' ' * 14}{bold(f'-{atten['Run 2']:.0f}')} dB (Run 2)", size=6)
    ax.legend([Line2D([], [], color="black", lw=0.7), Line2D([], [], color=C["Run 1"], lw=0.7),
               Line2D([], [], color=C["Run 2"], lw=0.7), Line2D([], [], color="0.4", lw=0.7, ls="--")],
              [f"unfiltered, {v[a:b].mean():.1f} m/s", "Run 1 output, 0.75 Hz", "Run 2 output, 0.51 Hz",
               "predicted by the design"], loc="lower left", handlelength=1.4, labelspacing=0.15, fontsize=6)
    ax.set(xlim=(0.2, 100), ylim=(1e-18, 10), xlabel="Frequency (Hz)")
    ax.set_title("(b) Filter at 10 m/s", loc="left")

    # (c) mean error on each steady plateau
    ax = fig.add_subplot(gs[:, 2])
    band(ax, horizontal=False)
    ax.axhline(0, color="0.6", lw=0.5)
    vv = np.linspace(1.5, 11, 100)
    for name, r, mk in (("check", check, "s"), ("Run 1", runs["Run 1"], "o"), ("Run 2", runs["Run 2"], "^")):
        p = plateau_means(r)
        ax.errorbar(p[:, 0], p[:, 1], yerr=p[:, 2], fmt=mk, ms=3, color=C[name], elinewidth=0.6, capsize=1.5,
                    label="check run" if name == "check" else name, zorder=3)
        if name != "check":   # the shape a v^1.82 force gives through a square-root map with an offset
            est = p[:, 0] + p[:, 1]
            fit = least_squares(lambda q: np.sqrt(np.clip(q[0] * p[:, 0] ** EXP_N + q[1], 0, None)) - est,
                                x0=[1.0, 0.0])
            ax.plot(vv, np.sqrt(np.clip(fit.x[0] * vv ** EXP_N + fit.x[1], 0, None)) - vv, color=C[name],
                    lw=0.7, ls="--", zorder=2)
    ax.plot([], [], color="0.4", ls="--", lw=0.7, label=f"$v^{{{EXP_N}}}$ drag model")
    ax.set(xlim=(1.5, 11), ylim=(-0.75, 0.75), xlabel="Plateau reference speed (m/s)", ylabel="Mean error (m/s)")
    ax.legend(loc="lower left", handlelength=1.4, labelspacing=0.15)
    ax.set_title("(c) Error on steady plateaus", loc="left")

    # (d) MSE and RMSE by regime, on their own axes so the units never mix
    labels = ["Speed\nchange", "Steady\n≥9.5", "Steady\n6–9.5", "Ref.\n= 0", "Whole\nrun"]
    x = np.arange(len(labels))
    axm, axr = fig.add_subplot(gs[0, 3]), fig.add_subplot(gs[1, 3])
    for i, name in enumerate(runs):
        r = runs[name]
        lab = regime_of(r)
        sc = [scores(r.err[lab == g]) for g in REGIMES] + [scores(r.err)]
        axm.bar(x + (i - 0.5) * 0.38, [s["mse"] for s in sc], 0.38, color=C[name], label=name)
        axr.bar(x + (i - 0.5) * 0.38, [s["rmse"] for s in sc], 0.38, color=C[name])
    axm.axvspan(3.5, 4.5, color="0.93", zorder=0)
    axr.axvspan(3.5, 4.5, color="0.93", zorder=0)
    axm.set(ylabel="MSE (m²/s²)", xticks=x, xticklabels=[], ylim=(0, 0.4), xlim=(-0.5, 4.5))
    axr.set(ylabel="RMSE (m/s)", xticks=x, ylim=(0, 0.65), xlim=(-0.5, 4.5))
    axr.set_xticklabels(labels, fontsize=6)
    axm.legend(loc="upper left", ncol=2, handlelength=1.0, columnspacing=0.8, fontsize=6)
    axm.set_title("(d) MSE and RMSE by regime", loc="left")

    fig.savefig(OUT / "analysis_v2.pdf")
    fig.savefig(OUT / "analysis_v2.png", dpi=200)
    plt.close(fig)
    return peaks, atten


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--demo", type=Path, default=DEMO, help="folder with the two assessed runs' logger CSVs")
    ap.add_argument("--out", type=Path, default=OUT, help="where to write the figures")
    args = ap.parse_args()
    DEMO, OUT = args.demo, args.out
    OUT.mkdir(parents=True, exist_ok=True)
    runs = {name: load_run(stem, DEMO, name) for name, stem in DEMO_RUNS.items()}
    check = load_run(CHECK_RUN, TUNNEL, "check")
    sm, s_ramp, s_gust = fig_demo_runs(runs)
    peaks, atten = fig_analysis(runs, check)
    for name, r in runs.items():
        lab = regime_of(r)
        tot = float(np.sum(r.err ** 2))
        print(name, {k: round(v, 4) for k, v in scores(r.err).items()},
              "Table II shares %:", [round(100 * float(np.sum(r.err[lab == g] ** 2)) / tot) for g in REGIMES])
    print("step (Run 2):", {k: round(v, 3) for k, v in sm.items()})
    print("ramp/gust RMSE:", round(s_ramp["rmse"], 3), round(s_gust["rmse"], 3))
    print("spectral peaks (Hz):", [round(p, 2) for p in peaks], "attenuation at", RES_HZ, "Hz:",
          {k: round(v, 1) for k, v in atten.items()})
    print("wrote", OUT / "demo_runs_v2.pdf", "and", OUT / "analysis_v2.pdf")
