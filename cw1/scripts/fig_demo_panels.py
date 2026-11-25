#!/usr/bin/env python3
"""
fig_demo_panels.py -- Fig. 5 of the report as five separate panels, so that
each panel can carry its own sub-caption in LaTeX (\\subfloat).

    python scripts/fig_demo_panels.py --tunnel <repo>/tunnel --team <repo>/team6

Writes figures/demo_legend.pdf, figures/demo_{a,b,c,d,e}.pdf and
results/demo_panels.tex (numbers quoted in the sub-captions and the text).

Error strips (panels a, b): one marker per logged pair (4 Hz).
  filled, run colour : reference inside the brief's 2-10 m/s range
  hollow, grey       : reference outside 2-10 m/s (at rest, or above 10 m/s)
  black line         : 5 s moving RMSE over all pairs
"""
import argparse
import glob
import os
import sys

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyse_team6 import Run, plateau_table  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FIG, RES = os.path.join(ROOT, "figures"), os.path.join(ROOT, "results")
plt.rcParams.update({"font.size": 8, "font.family": "serif", "axes.grid": True,
                     "grid.alpha": 0.3, "legend.frameon": False,
                     "axes.titlesize": 8, "axes.labelsize": 8})
LO, HI = 2.0, 10.0
BAND = dict(color="#cfe8c4", alpha=0.6, lw=0)
C_REF, C1, C2, C_OUT = "black", "#1f5fbf", "#e8731a", "0.55"
BOX = dict(boxstyle="round,pad=0.25", fc="white", ec="0.6", lw=0.6)
MAC = {}


def mac(k, v, fmt="{:.2f}"):
    MAC[k] = fmt.format(v)


def moving_rmse(t, r, win=5.0):
    out = np.empty_like(r)
    for i, ti in enumerate(t):
        m = (t > ti - win / 2) & (t <= ti + win / 2)
        out[i] = np.sqrt(np.mean(r[m] ** 2))
    return out


def run_panel(r, colour, name, zooms):
    """Airspeed (top) and error strip (bottom) for one run."""
    fig, (ax, ae) = plt.subplots(2, 1, figsize=(3.5, 2.1), sharex=True,
                                 gridspec_kw={"height_ratios": [2.1, 1], "hspace": 0.08})
    ax.axhspan(LO, HI, **BAND)
    ax.step(r.tv, r.yv, color=C_REF, where="post", lw=0.9)
    ax.plot(r.tu, r.ys, color=colour, lw=1.0)
    for lab, a0, a1, y0, y1 in zooms:
        ax.add_patch(plt.Rectangle((a0, y0), a1 - a0, y1 - y0, fill=False, ls="--",
                                   lw=0.6, ec="0.3"))
        ax.text(a0 + 0.5, y1 - 0.2, lab, fontsize=6.5, va="top")
    res = r.ya - r.yv
    m = (r.yv >= LO) & (r.yv <= HI)
    a_all = np.mean(res ** 2)
    a_in = np.mean(res[m] ** 2)
    ax.text(0.5, 0.06, f"all pairs: MSE {a_all:.3f} m$^2$/s$^2$, RMSE {np.sqrt(a_all):.3f} m/s\n"
            f"reference in 2–10 m/s: MSE {a_in:.3f}, RMSE {np.sqrt(a_in):.3f}",
            transform=ax.transAxes, ha="center", va="bottom", fontsize=6.3, bbox=BOX)
    ax.set_ylabel("Airspeed (m/s)")
    ax.set_ylim(-0.3, 11.2)
    ae.axhline(0, color="0.4", lw=0.5)
    ae.plot(r.tv[m], res[m], "o", ms=2.0, color=colour, mec=colour, lw=0)
    ae.plot(r.tv[~m], res[~m], "o", ms=2.4, mfc="none", mec=C_OUT, mew=0.6, lw=0)
    ae.plot(r.tv, moving_rmse(r.tv, res), color="black", lw=0.9)
    ae.set_ylim(-2.4, 3.6)
    ae.set_ylabel("Error (m/s)")
    ae.set_xlabel("Time (s)")
    ae.set_xlim(0, r.tv[-1])
    ae.legend(handles=[
        Line2D([], [], marker="o", ls="", ms=3, color=colour, label="in 2–10 m/s"),
        Line2D([], [], marker="o", ls="", ms=3, mfc="none", mec=C_OUT, label="outside 2–10 m/s"),
        Line2D([], [], color="black", lw=0.9, label="5 s moving RMSE")],
        loc="upper center", ncol=3, fontsize=5.8, handletextpad=0.2, columnspacing=0.8,
        bbox_to_anchor=(0.5, 1.0), frameon=True, framealpha=0.9, edgecolor="none")
    fig.savefig(os.path.join(FIG, f"demo_{name}.pdf"), bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    return a_all, a_in


def spin_up(r1, r2):
    fig, axs = plt.subplots(2, 1, figsize=(2.3, 1.7), sharex=True,
                            gridspec_kw={"hspace": 0.1})
    for ax, r, colour, k in ((axs[0], r1, C1, 1), (axs[1], r2, C2, 2)):
        t_ref0 = r.tv[np.argmax(r.yv > 0)]                     # first non-zero reference
        t_est0 = r.tu[np.argmax((r.ys > 0.2) & (r.tu > t_ref0 - 5))]   # leaves zero: > 0.2 m/s
        early = t_ref0 - t_est0
        a0, a1 = t_ref0 - 2, t_ref0 + 5
        m, mv = (r.tu > a0) & (r.tu < a1), (r.tv > a0) & (r.tv < a1)
        ax.axhspan(LO, HI, **BAND)
        ax.step(r.tv[mv] - t_ref0, r.yv[mv], color=C_REF, where="post", lw=0.9)
        ax.plot(r.tu[m] - t_ref0, r.ys[m], color=colour, lw=1.0)
        ref_on = np.interp(r.tu[m], r.tv, r.yv)
        ax.fill_between(r.tu[m] - t_ref0, ref_on, r.ys[m], color=colour, alpha=0.18, lw=0)
        sel = mv                                   # all pairs in the plotted window
        rm = np.sqrt(np.mean((r.ya[sel] - r.yv[sel]) ** 2))
        ax.text(0.97, 0.06, f"{early:.1f} s early, RMSE {rm:.2f} m/s", transform=ax.transAxes,
                ha="right", va="bottom", fontsize=6.3, bbox=BOX)
        ax.text(0.03, 0.92, f"Run {k}, calibration {k}", transform=ax.transAxes,
                fontsize=6.5, va="top", color=colour, fontweight="bold")
        ax.set_ylim(-0.3, 11)
        ax.set_xlim(-2, 5)
        ax.set_ylabel("m/s")
        mac(f"SpinEarly{k}", early, "{:.1f}")
        mac(f"SpinRMSE{k}", rm, "{:.2f}")
    axs[1].set_xlabel("Time from first non-zero reference (s)")
    fig.savefig(os.path.join(FIG, "demo_c.pdf"), bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


def step(r2):
    p = plateau_table(r2)
    up = [j for j in range(1, len(p)) if p.v.iloc[j] - p.v.iloc[j - 1] > 1.5]
    k = up[-1]
    v0, v1 = p.v.iloc[k - 1], p.v.iloc[k]
    a0, a1 = p.t1.iloc[k - 1] - 2, p.t0.iloc[k] + 5
    m, mv = (r2.tu > a0) & (r2.tu < a1), (r2.tv > a0) & (r2.tv < a1)
    ma = (r2.tu >= p.t0.iloc[k - 1]) & (r2.tu <= p.t1.iloc[k - 1])
    mb = (r2.tu >= p.t0.iloc[k]) & (r2.tu <= p.t1.iloc[k])
    e0, e1 = r2.ys[ma].mean(), r2.ys[mb].mean()

    def rise(t, y, y0, y1):
        w = (t > p.t1.iloc[k - 1]) & (t < p.t0.iloc[k] + 1)
        t10 = t[w][np.argmax(y[w] >= y0 + 0.1 * (y1 - y0))]
        t90 = t[w][np.argmax(y[w] >= y0 + 0.9 * (y1 - y0))]
        return t90 - t10
    tr_ref, tr_est = rise(r2.tv, r2.yv, v0, v1), rise(r2.tu, r2.ys, e0, e1)
    w = (r2.tu > p.t1.iloc[k - 1]) & (r2.tu < p.t0.iloc[k] + 1)
    ov = max(0.0, r2.ys[w].max() - e1) / (e1 - e0) * 100
    ss = p.bias.iloc[k]
    rm = np.sqrt(np.mean((r2.ya[mv] - r2.yv[mv]) ** 2))
    fig, ax = plt.subplots(figsize=(2.3, 1.7))
    ax.axhspan(LO, HI, **BAND)
    ax.step(r2.tv[mv], r2.yv[mv], color=C_REF, where="post", lw=0.9)
    ax.plot(r2.tu[m], r2.ys[m], color=C2, lw=1.0)
    for fr in (0.1, 0.9):
        ax.axhline(v0 + fr * (v1 - v0), color="0.45", ls=":", lw=0.7)
    ax.annotate("", xy=(a1 - 0.6, e1), xytext=(a1 - 0.6, v1),
                arrowprops=dict(arrowstyle="<->", lw=0.6))
    ax.text(a1 - 0.8, v1 + 0.42, f"steady error {ss:+.2f} m/s", ha="right", va="top", fontsize=6.0)
    ax.text(0.97, 0.04, f"10–90 % rise: estimate {tr_est:.1f} s,\nreference {tr_ref:.1f} s; "
            f"overshoot {ov:.0f} %\nRMSE {rm:.2f} m/s", transform=ax.transAxes, ha="right",
            va="bottom", fontsize=6.0, bbox=BOX)
    ax.set_ylim(v0 - 1.4, v1 + 0.55)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Airspeed (m/s)")
    fig.savefig(os.path.join(FIG, "demo_d.pdf"), bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    mac("StepFrom", v0, "{:.1f}"); mac("StepTo", v1, "{:.1f}")
    mac("StepRiseEst", tr_est, "{:.1f}"); mac("StepRiseRef", tr_ref, "{:.1f}")
    mac("StepOv", ov, "{:.0f}"); mac("StepErr", ss, "{:+.2f}"); mac("StepRMSE", rm, "{:.2f}")
    return (a0, a1, v0 - 1.4, v1 + 0.5)


def ramp_gust(r2):
    p = plateau_table(r2)
    j = max(i for i in range(len(p)) if p.v.iloc[i] > 9.5)
    a0, a1 = p.t1.iloc[j] - 2, r2.tv[-1] - 1
    t_rest = r2.tv[(r2.tv > p.t1.iloc[j]) & (r2.yv == 0)][0]
    t_gust = r2.tv[(r2.tv > t_rest) & (r2.yv > 0)][0]
    m, mv = (r2.tu > a0) & (r2.tu < a1), (r2.tv > a0) & (r2.tv < a1)
    sel_r = (r2.tv >= p.t1.iloc[j]) & (r2.tv <= t_rest)
    sel_g = (r2.tv >= t_gust) & (r2.tv <= a1)
    rm_r = np.sqrt(np.mean((r2.ya[sel_r] - r2.yv[sel_r]) ** 2))
    rm_g = np.sqrt(np.mean((r2.ya[sel_g] - r2.yv[sel_g]) ** 2))
    fig, ax = plt.subplots(figsize=(2.3, 1.7))
    ax.axhspan(LO, HI, **BAND)
    ax.step(r2.tv[mv], r2.yv[mv], color=C_REF, where="post", lw=0.9)
    ax.plot(r2.tu[m], r2.ys[m], color=C2, lw=1.0)
    ax.text(0.03, 0.97, f"RMSE: ramp {rm_r:.2f}, gusts {rm_g:.2f} m/s", transform=ax.transAxes,
            ha="left", va="top", fontsize=6.0, bbox=BOX)
    ax.set_ylim(-0.3, 11.5)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Airspeed (m/s)")
    fig.savefig(os.path.join(FIG, "demo_e.pdf"), bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    mac("RampRMSE", rm_r); mac("GustRMSE", rm_g)
    return (a0, a1, -0.3, 11.2)


def legend():
    fig = plt.figure(figsize=(7.0, 0.22))
    fig.legend(handles=[Line2D([], [], color=C_REF, lw=1.0, label="Reference (pitot, 4 Hz)"),
                        Line2D([], [], color=C1, lw=1.4, label="Sensor estimate, run 1 (200 Hz)"),
                        Line2D([], [], color=C2, lw=1.4, label="Sensor estimate, run 2 (200 Hz)"),
                        Patch(**{k: v for k, v in BAND.items() if k != "lw"},
                              label="Brief's range, 2–10 m/s")],
               loc="center", ncol=4, fontsize=7.5, frameon=False)
    fig.savefig(os.path.join(FIG, "demo_legend.pdf"), bbox_inches="tight", pad_inches=0.01)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tunnel", required=True)
    ap.add_argument("--team", required=True)
    a = ap.parse_args()
    team = sorted(glob.glob(os.path.join(a.team, "*_student.csv")))
    r1 = Run(team[0].replace("_student.csv", ""), "Run 1")
    r2 = Run(team[1].replace("_student.csv", ""), "Run 2")
    legend()
    spin_up(r1, r2)
    zd = step(r2)
    ze = ramp_gust(r2)
    t1 = r1.tv[np.argmax(r1.yv > 0)]
    t2 = r2.tv[np.argmax(r2.yv > 0)]
    run_panel(r1, C1, "a", [("c", t1 - 2, t1 + 5, -0.3, 11.0)])
    run_panel(r2, C2, "b", [("c", t2 - 2, t2 + 5, -0.3, 11.0),
                            ("d", zd[0], zd[1], zd[2], zd[3]), ("e", ze[0], ze[1], -0.3, 11.0)])
    with open(os.path.join(RES, "demo_panels.tex"), "w") as fh:
        fh.write("% generated by scripts/fig_demo_panels.py -- do not edit\n")
        for k, v in MAC.items():
            fh.write(f"\\expandafter\\def\\csname r{k}\\endcsname{{{v}}}\n")
    print("\n".join(f"{k} = {v}" for k, v in MAC.items()))


if __name__ == "__main__":
    main()
