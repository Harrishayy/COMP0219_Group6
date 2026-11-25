#!/usr/bin/env python3
"""
report_figures.py -- regenerate every data figure and number in the
COMP0219 Part 1 report from the Wind Tunnel Logger files.

    python scripts/report_figures.py --tunnel <repo>/tunnel --team <repo>/team6

Steps (each can also be run on its own):
  1. analyse_team6.py    -> results/metrics.tex, results/steps.tex, results/summary.txt
                            (numbers quoted in the text and Tables II-III)
  2. fig_demo_panels.py  -> figures/demo_{legend,a,b,c,d,e}.pdf, results/demo_panels.tex
                            (Fig. 4, assessed runs)
  3. this file           -> figures/analysis_{a,b,c,d}.pdf (Fig. 5, frequency and
                            error analysis)

Fig. 5 panels
  (a) Welch PSD of the output stream recorded with the 4 Nov firmware, before the
      output low-pass was added, on steady plateaus, with the vortex-shedding band f = St v / D (St 0.12-0.2,
      D = target diameter) drawn for each speed.
  (b) At ~10 m/s: each assessed run's output PSD (solid) against the unfiltered
      PSD passed through that run's 2nd-order Butterworth low-pass (dashed).
  (c) Mean error (+-1 sigma) on each steady plateau for the calibration-1 check
      run and both assessed runs; dashed: K v^(n/2) - v fitted per run, i.e. the
      error shape of a square-root map when drag grows as v^n.
  (d) MSE and RMSE by part of the run (same split as Table III) and whole run.
"""
import argparse
import glob
import os
import re
import subprocess
import sys

import numpy as np
import pandas as pd
from scipy import signal
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from analyse_team6 import Run, plateaus, plateau_table, regimes  # noqa: E402

ROOT = os.path.join(HERE, "..")
FIG, RES = os.path.join(ROOT, "figures"), os.path.join(ROOT, "results")
plt.rcParams.update({"font.size": 7.5, "font.family": "serif", "axes.grid": True,
                     "grid.alpha": 0.3, "legend.frameon": False, "legend.fontsize": 6})
C1, C2, CC = "#1f5fbf", "#e8731a", "0.45"
SIZE = (1.75, 1.75)
TARGET_D = 0.086          # m, hemisphere diameter used for the shedding band
CUTOFF = {1: 0.75, 2: 0.51}   # Hz, firmware Butterworth cut-off per assessed run
FS = 200.0


def exponent_from_metrics(default=1.82):
    path = os.path.join(RES, "metrics.tex")
    if os.path.exists(path):
        m = re.search(r"rExpN\\endcsname\{([0-9.]+)\}", open(path).read())
        if m:
            return float(m.group(1))
    return default


def plateau_signal(r, vmin=None):
    """(mean speed, 200 Hz samples) for every steady plateau of a run."""
    out = []
    for i0, i1 in plateaus(r.tv, r.yv):
        v = r.yv[i0:i1].mean()
        m = (r.tu >= r.tv[i0]) & (r.tu <= r.tv[i1 - 1])
        if (vmin is None or v >= vmin) and m.sum() >= 512:
            out.append((v, r.ys[m]))
    return out


def welch(x):
    return signal.welch(x - x.mean(), fs=FS, nperseg=512)


def save(fig, name):
    fig.savefig(os.path.join(FIG, name), bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


def panel_a(unfilt_runs):
    fig, ax = plt.subplots(figsize=SIZE)
    picked, colours = [], plt.cm.viridis(np.linspace(0.1, 0.85, 4))
    for r in unfilt_runs:
        for v, x in plateau_signal(r, vmin=2.0):
            if all(abs(v - p) > 1.0 for p, _ in picked):
                picked.append((v, x))
    picked = sorted(picked)[:4]
    peaks = []
    for k, (v, x) in enumerate(picked):
        f, P = welch(x)
        ax.loglog(f[1:], P[1:], color=colours[k], lw=0.8, label=f"{v:.1f} m/s")
        sel = f > 5
        peaks.append(f[sel][np.argmax(P[sel])])
        lo, hi = 0.12 * v / TARGET_D, 0.2 * v / TARGET_D
        ax.plot([lo, hi], [2e-7 * 3 ** k] * 2, color=colours[k], lw=2.5, solid_capstyle="butt")
    fp = float(np.mean(peaks))
    ax.axvline(fp, color="k", ls=":", lw=0.7)
    ax.text(fp * 1.1, 3e-1, f"{fp:.1f} Hz", fontsize=6.5)
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("PSD ((m/s)$^2$/Hz)")
    ax.set_ylim(1e-8, 1)
    ax.legend(loc="lower left", fontsize=5, title="bars: shedding band", title_fontsize=5,
              handlelength=1.2, borderaxespad=0.2)
    save(fig, "analysis_a.pdf")
    return fp


def panel_b(unfilt_runs, r1, r2):
    fig, ax = plt.subplots(figsize=SIZE)
    ref = None
    for r in unfilt_runs:
        for v, x in plateau_signal(r, vmin=9.5):
            ref = (v, x)
            break
        if ref:
            break
    f, P0 = welch(ref[1])
    ax.loglog(f[1:], P0[1:], color="k", lw=0.8, label=f"unfiltered, {ref[0]:.1f} m/s")
    for r, k, c in ((r1, 1, C1), (r2, 2, C2)):
        hi = [x for v, x in plateau_signal(r, vmin=9.5)]
        if not hi:
            continue
        _, P = welch(hi[0])
        b, a = signal.butter(2, CUTOFF[k], fs=FS)
        _, H = signal.freqz(b, a, worN=f, fs=FS)
        ax.loglog(f[1:], P[1:], color=c, lw=0.8, label=f"run {k} output, {CUTOFF[k]} Hz")
        ax.loglog(f[1:], (P0 * np.abs(H) ** 2)[1:], color=c, lw=0.8, ls="--")
    ax.plot([], [], "k--", lw=0.8, label="predicted by the filter")
    floor = (0.001 ** 2 / 12) / (FS / 2)
    ax.axhline(floor, color="0.5", ls=":", lw=0.6)
    ax.text(0.25, floor * 4, "rounding floor", fontsize=5.5, color="0.4")
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("PSD ((m/s)$^2$/Hz)")
    ax.set_ylim(1e-18, 1)
    ax.legend(loc="lower left", fontsize=5.2)
    save(fig, "analysis_b.pdf")


def panel_c(cal1, r1, r2, n):
    fig, ax = plt.subplots(figsize=SIZE)
    ax.axvspan(2, 10, color="#cfe8c4", alpha=0.6, lw=0)
    vv = np.linspace(2.5, 10.5, 50)
    for r, c, mk, lab in ((cal1, CC, "s", "check run"), (r1, C1, "o", "run 1"),
                          (r2, C2, "^", "run 2")):
        p = plateau_table(r)
        if r is cal1:
            # the check run's slow descent through 3-4 m/s (56-67 s) is not a strict
            # plateau but is the only low-speed steady segment; include it
            m = (r.tv > 56) & (r.tv < 67)
            p = pd.concat([p, pd.DataFrame([dict(v=r.yv[m].mean(), bias=(r.ya[m] - r.yv[m]).mean(),
                                                 t0=56.0, t1=67.0)])], ignore_index=True)
        sd = []
        for _, q in p.iterrows():
            m = (r.tv >= q.t0) & (r.tv <= q.t1)
            sd.append(np.std(r.ya[m] - r.yv[m]))
        ax.errorbar(p.v, p.bias, yerr=sd, fmt=mk, ms=3, color=c, capsize=1.5, lw=0.7,
                    label=lab)
        # error shape of a square-root map when drag grows as v^n: K v^(n/2) - v
        K = np.sum((p.bias + p.v) * p.v ** (n / 2)) / np.sum(p.v ** n)
        ax.plot(vv, K * vv ** (n / 2) - vv, color=c, ls="--", lw=0.7)
    ax.axhline(0, color="k", lw=0.5)
    ax.plot([], [], "k--", lw=0.7, label=f"$v^{{{n:.2f}}}$ drag model")
    ax.set_xlabel("Plateau reference speed (m/s)")
    ax.set_ylabel("Mean error (m/s)")
    ax.legend(loc="lower left", fontsize=5.5)
    save(fig, "analysis_c.pdf")


def panel_d(r1, r2):
    labels = ["speed\nchange", "steady\n$\\geq$9.5", "steady\n6-9.5", "ref.\n$=0$", "whole\nrun"]
    keys = ["Transient", "Steady\n$\\geq$9.5", "Steady\n6-9.5", "Ref.$=$0"]
    vals = []
    for r in (r1, r2):
        reg, _ = regimes(r)
        mse = [reg[k][1] for k in keys] + [np.mean((r.ya - r.yv) ** 2)]
        vals.append(np.array(mse))
    fig, (am, ar) = plt.subplots(2, 1, figsize=SIZE, sharex=True,
                                 gridspec_kw={"hspace": 0.12})
    x = np.arange(len(labels))
    for k, (v, c) in enumerate(zip(vals, (C1, C2))):
        am.bar(x + (k - 0.5) * 0.38, v, 0.38, color=c, label=f"Run {k + 1}")
        ar.bar(x + (k - 0.5) * 0.38, np.sqrt(v), 0.38, color=c)
    for a in (am, ar):
        a.axvspan(3.5, 4.5, color="0.9", lw=0, zorder=0)
    am.set_ylabel("MSE (m$^2$/s$^2$)")
    ar.set_ylabel("RMSE (m/s)")
    ar.set_xticks(x, labels, fontsize=4.8)
    am.legend(loc="upper left", fontsize=5.5, ncol=2)
    save(fig, "analysis_d.pdf")
    return vals


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tunnel", required=True, help="logger folder with the calibration runs")
    ap.add_argument("--team", required=True, help="logger folder with the two assessed runs")
    ap.add_argument("--only-fig5", action="store_true", help="skip steps 1 and 2")
    a = ap.parse_args()
    os.makedirs(FIG, exist_ok=True)
    os.makedirs(RES, exist_ok=True)

    if not a.only_fig5:
        for script in ("analyse_team6.py", "fig_demo_panels.py"):
            print(f"== running {script}")
            subprocess.run([sys.executable, os.path.join(HERE, script),
                            "--tunnel", a.tunnel, "--team", a.team], check=True)

    T = lambda p: os.path.join(a.tunnel, p)
    unfilt = [Run(T(p), p) for p in ("run_20261104_151708", "run_20261104_152041",
                                     "run_20261104_152520")]
    cal1 = Run(T("run_20261105_091624_live_drag_plate"), "check run")
    team = sorted(glob.glob(os.path.join(a.team, "*_student.csv")))
    r1 = Run(team[0].replace("_student.csv", ""), "Run 1")
    r2 = Run(team[1].replace("_student.csv", ""), "Run 2")
    n = exponent_from_metrics()

    fp = panel_a(unfilt)
    panel_b(unfilt, r1, r2)
    panel_c(cal1, r1, r2, n)
    vals = panel_d(r1, r2)
    print(f"== Fig. 5 written (figures/analysis_a-d.pdf); resonance {fp:.1f} Hz, n = {n:.2f}")
    for k, v in enumerate(vals, 1):
        print(f"   run {k} RMSE by part: " + ", ".join(f"{x:.2f}" for x in np.sqrt(v)))


if __name__ == "__main__":
    main()
