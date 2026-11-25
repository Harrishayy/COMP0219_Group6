#!/usr/bin/env python3
"""
analyse_team6.py -- every data figure and number in the COMP0219 Part 1 report.

Input: the logger output in the sensors repository. Each run is a triplet
    <run>_student.csv   our 200 Hz stream (host timestamps)
    <run>_vdas.csv      tunnel reference (VDAS, ~4 Hz; or GIGA, 100 Hz, Oct 29)
    <run>_aligned.csv   reference + nearest student sample (what is scored)

Usage (from the report folder):
    python scripts/analyse_team6.py --tunnel ../sensors/tunnel --team ../sensors/team6

Outputs: figures/{calibration,demo_runs,error_analysis,psd}.pdf,
         results/metrics.tex (macros read by main.tex via \\R{name}),
         results/summary.txt

Conventions
  * Scored metrics use the *_aligned.csv pairs, i.e. exactly what the logger
    compares. "Scored range" = reference in [2, 10] m/s; whole-run figures are
    also reported because the marking script's window is not documented.
  * Host timestamps of the 200 Hz stream arrive in USB bursts. Where the stream
    must be evaluated at arbitrary times (lag tests, spectra) it is placed on a
    uniform 200 Hz grid anchored at the last sample; this reproduces the
    aligned pairs to 0.002 m/s mean absolute difference.
  * Retrospective corrections are fitted on one demo run and scored on the
    other (two-fold cross-validation); in-sample numbers are shown only to
    illustrate over-fitting.
"""
import argparse
import glob
import os

import numpy as np
import pandas as pd
from scipy import signal
from scipy.optimize import least_squares
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FIG, RES = os.path.join(ROOT, "figures"), os.path.join(ROOT, "results")
plt.rcParams.update({"font.size": 7.5, "font.family": "serif", "axes.grid": True,
                     "grid.alpha": 0.3, "lines.linewidth": 0.8, "legend.frameon": False,
                     "legend.fontsize": 6.3, "axes.titlesize": 7.5})
COL, DCOL = 3.45, 7.1
SCORED = (2.0, 10.0)
LOG, MAC = [], {}


def say(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    LOG.append(s)


def mac(name, val, fmt="{:.3f}"):
    MAC[name] = fmt.format(val)


# ------------------------------------------------------------------ data
class Run:
    def __init__(self, prefix, label):
        self.label = label
        self.s = pd.read_csv(prefix + "_student.csv")
        self.v = pd.read_csv(prefix + "_vdas.csv")
        self.a = pd.read_csv(prefix + "_aligned.csv")
        self.t0 = self.v.timestamp_epoch.values[0]
        ts = self.s.timestamp_epoch.values.astype(float)
        n = len(ts)
        self.tu = ts[-1] - (n - 1 - np.arange(n)) / 200.0 - self.t0   # uniform grid
        self.ys = self.s.value_raw_mps.values.astype(float)
        self.tv = self.v.timestamp_epoch.values - self.t0
        self.yv = self.v.value_raw_mps.values.astype(float)
        self.err = self.v.expected_error_pct.values.astype(float)
        self.ya = self.a.student_mps.values.astype(float)   # scored student values
        self.fs_ref = (len(self.tv) - 1) / (self.tv[-1] - self.tv[0])
        dur = ts[-1] - ts[0]
        self.n_stream, self.dur, self.n_expected = n, dur, int(round(dur * 200)) + 1

    def est_at(self, t, shift=0.0):
        return np.interp(t - shift, self.tu, self.ys)


def plateaus(t, y, win=8, thr=0.12, minlen=12, trim=0.15):
    s = pd.Series(y).rolling(win, center=True).std().to_numpy()
    st = np.nan_to_num(s, nan=9) < thr
    out, i = [], 0
    while i < len(st):
        if st[i]:
            j = i
            while j < len(st) and st[j]:
                j += 1
            if j - i >= minlen:
                k = int(trim * (j - i))
                out.append((i + k, j - k))
            i = j
        else:
            i += 1
    return out


def metrics(r):
    return dict(mse=np.mean(r ** 2), rmse=np.sqrt(np.mean(r ** 2)), bias=r.mean(), sd=r.std())


# ------------------------------------------------------------------ analyses
def stream_check(runs):
    say("\n== Streaming (200 Hz, 115200 baud)")
    for r in runs:
        gaps = np.diff(r.s.timestamp_epoch.values)
        say(f"{r.label}: {r.n_stream} samples in {r.dur:.2f} s "
            f"(expected {r.n_expected}); max host gap {gaps.max()*1e3:.0f} ms")


def reference_props(r):
    pa = r.v.pa_raw.dropna()
    v = r.yv
    m = r.v.pa_corrected.abs() > 2
    rho = (2 * r.v.pa_corrected[m].abs() / r.v.value_raw_mps[m] ** 2).median()
    levels = sorted(set(np.round(v[(v > 0) & (v < 3)], 3)))
    say(f"\n== Reference ({r.label}): {r.fs_ref:.2f} Hz, pa_raw integer={np.allclose(pa, np.round(pa))}, "
        f"rho used = {rho:.4f} kg/m^3, levels below 3 m/s: {levels}")
    mac("RefRate", r.fs_ref, "{:.1f}")
    mac("Rho", rho, "{:.4f}")
    for vv in (2.0, 10.0):
        mac(f"RefStep{int(vv)}", 1.0 / (rho * vv), "{:.2f}")


def calibration_exponent(cal_runs):
    """Fit est^2 = a * vref^n + b_run on Nov-4 plateaus (pre-calibration stream)."""
    rows = []
    for r in cal_runs:
        for i0, i1 in plateaus(r.tv, r.yv):
            m = (r.tu >= r.tv[i0]) & (r.tu <= r.tv[i1 - 1])
            if m.sum() < 50:
                continue
            vref = r.yv[i0:i1].mean()
            rows.append((r.label, 0.0 if vref < 0.5 else vref, r.ys[m].mean()))
    d = pd.DataFrame(rows, columns=["run", "vref", "est"])
    runs = sorted(d.run.unique())
    ri = {k: i for i, k in enumerate(runs)}
    bidx = np.array([ri[k] for k in d.run])

    def resid(p, n_fixed=None, dd=d, bi=bidx):
        a, n = p[0], (p[1] if n_fixed is None else n_fixed)
        b = p[2:] if n_fixed is None else p[1:]
        return a * dd.vref.values ** n + b[bi] - dd.est.values ** 2

    fit = least_squares(resid, [1.5, 1.8] + [0] * len(runs))
    fit2 = least_squares(resid, [1.5] + [0] * len(runs), args=(2.0,))
    jack = []
    for k in range(len(d)):
        keep = np.arange(len(d)) != k
        jack.append(least_squares(lambda p: resid(p, None, d[keep], bidx[keep]),
                                  fit.x).x[1])
    jack = np.array(jack)
    se = np.sqrt((len(jack) - 1) / len(jack) * np.sum((jack - jack.mean()) ** 2))
    n_hat = fit.x[1]
    say(f"\n== Response exponent from Nov-4 calibration plateaus ({len(d)} plateaus, {len(runs)} runs)")
    say(f"n = {n_hat:.3f} +/- {se:.3f} (jackknife); RMS resid {np.sqrt(np.mean(fit.fun**2)):.2f} "
        f"vs {np.sqrt(np.mean(fit2.fun**2)):.2f} (m/s)^2 with n = 2")
    mac("ExpN", n_hat, "{:.2f}")
    mac("ExpSE", se, "{:.2f}")
    mac("CalPlateaus", len(d), "{:d}")

    fig, ax = plt.subplots(1, 2, figsize=(COL, 1.85))
    for k, run in enumerate(runs):
        dd = d[d.run == run]
        b = fit.x[2 + k]
        ax[0].plot(dd.vref, dd.est, "o", ms=3, label=run.replace("_", " "))
        y = dd.est.values ** 2 - b
        ok = (dd.vref > 0.5) & (y > 0)
        ax[1].loglog(dd.vref[ok], y[ok], "o", ms=3)
    vv = np.linspace(2, 10.5, 50)
    ax[0].plot([0, 10.5], [0, 10.5], "k:", lw=0.6, label="$y=x$")
    ax[0].set_xlabel("$v^{\\mathrm{ref}}$ (m/s)")
    ax[0].set_ylabel("Uncalibrated output (m/s)")
    ax[0].legend(fontsize=5.5)
    ax[1].loglog(vv, fit.x[0] * vv ** n_hat, "k-", lw=0.8, label=f"$n={n_hat:.2f}$")
    ax[1].loglog(vv, fit.x[0] * vv ** 2 * 10 ** ((n_hat - 2) * np.log10(6)), "k--", lw=0.8,
                 label="$n=2$ (slope)")
    ax[1].set_xlabel("$v^{\\mathrm{ref}}$ (m/s)")
    ax[1].set_ylabel("Output$^2$ $-$ offset (m$^2$/s$^2$)")
    from matplotlib.ticker import ScalarFormatter, NullFormatter
    for axis in (ax[1].xaxis, ax[1].yaxis):
        axis.set_major_formatter(ScalarFormatter())
        axis.set_minor_formatter(NullFormatter())
    ax[1].set_xticks([2, 5, 10])
    ax[1].set_yticks([10, 30, 100])
    ax[1].legend(fontsize=5.5)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "calibration.pdf"), bbox_inches="tight")
    plt.close(fig)
    return n_hat


def scored_metrics(runs, cal1):
    say("\n== Scored metrics (aligned pairs)")
    out = {}
    for r in runs + [cal1]:
        res = r.ya - r.yv
        m = (r.yv >= SCORED[0]) & (r.yv <= SCORED[1])
        a, s = metrics(res), metrics(res[m])
        err = r.err[m]
        sref = np.where(np.isfinite(err), err / 100 * r.yv[m], np.nan) / np.sqrt(3)
        var = s["sd"] ** 2
        adj = np.sqrt(s["bias"] ** 2 + max(0, var - np.nanmean(sref ** 2))) if np.isfinite(sref).any() else np.nan
        out[r.label] = (a, s, adj)
        say(f"{r.label}: all N={len(res)} MSE {a['mse']:.4f} RMSE {a['rmse']:.3f} bias {a['bias']:+.3f} sd {a['sd']:.3f} | "
            f"2-10 N={m.sum()} MSE {s['mse']:.4f} RMSE {s['rmse']:.3f} bias {s['bias']:+.3f} sd {s['sd']:.3f} RMSEadj {adj:.3f}")
    return out


def regimes(r):
    res = r.ya - r.yv
    steady = pd.Series(r.yv).rolling(8, center=True).std().fillna(9).to_numpy() < 0.12
    cats = {"Ref.$=$0": r.yv == 0,
            "Steady\n$\\geq$9.5": steady & (r.yv >= 9.5),
            "Steady\n6-9.5": steady & (r.yv >= 6) & (r.yv < 9.5),
            "Transient": (~steady) & (r.yv > 0)}
    sse = np.sum(res ** 2)
    rows = {k: (m.sum(), np.mean(res[m] ** 2) if m.any() else 0, np.sum(res[m] ** 2) / sse) for k, m in cats.items()}
    dv = np.gradient(r.yv, r.tv)
    m = (~steady) & (r.yv > 0.5)
    k = np.polyfit(dv[m], res[m], 1)[0]
    return rows, k


def plateau_table(r):
    rows = []
    for i0, i1 in plateaus(r.tv, r.yv):
        if r.yv[i0:i1].mean() < 1:
            continue
        res = r.ya[i0:i1] - r.yv[i0:i1]
        m = (r.tu >= r.tv[i0]) & (r.tu <= r.tv[i1 - 1])
        rows.append(dict(v=r.yv[i0:i1].mean(), bias=res.mean(), sd200=r.ys[m].std(),
                         t0=r.tv[i0], t1=r.tv[i1 - 1]))
    return pd.DataFrame(rows)


def run_to_run(p1, p2):
    n = min(len(p1), len(p2))
    d = (p2.bias.values[:n] - p1.bias.values[:n])
    v = (p1.v.values[:n] + p2.v.values[:n]) / 2
    delta, gain = 2 * v * d, d / v
    say("\n== Change between run 1 (cal. 1) and run 2 (cal. 2) on matched plateaus")
    say(f"zero-offset model delta = 2 v dv: {np.round(delta,2)} mean {delta.mean():.2f} CV {abs(delta.std(ddof=1)/delta.mean()):.2f}")
    say(f"gain model dv/v: {np.round(gain,4)} mean {gain.mean():.4f} CV {abs(gain.std(ddof=1)/gain.mean()):.2f}")
    mac("DeltaZero", delta.mean(), "{:.1f}")
    mac("DeltaCV", abs(delta.std(ddof=1) / delta.mean()), "{:.2f}")
    mac("GainCV", abs(gain.std(ddof=1) / gain.mean()), "{:.2f}")
    mac("DeltaEquivV", np.sqrt(abs(delta.mean())), "{:.1f}")
    return delta.mean()


def spectra(cal_run, demo_runs):
    """(a) PSD of the unfiltered Nov-4 stream on plateaus; (b) filter magnitude
    estimated as sqrt(PSD_demo / PSD_nov4) at ~10 m/s, with a 2nd-order fit."""
    say("\n== Spectra and inferred output filter")
    # ADC update rate: an unfiltered 200 Hz stream with no repeated consecutive
    # values means a new conversion every tick, i.e. NAU7802 at >= 200 SPS.
    rep = np.mean(np.diff(cal_run.ys[(cal_run.tu > 43) & (cal_run.tu < 61)]) == 0)
    say(f"Nov-4 stream: {rep*100:.1f}% repeated consecutive values -> ADC rate >= 200 SPS (320 SPS)")
    mac("RepeatPct", rep * 100, "{:.1f}")
    fig, ax = plt.subplots(1, 2, figsize=(COL, 1.95))
    peaks, hi = [], None
    for i0, i1 in plateaus(cal_run.tv, cal_run.yv):
        v = cal_run.yv[i0:i1].mean()
        m = (cal_run.tu >= cal_run.tv[i0]) & (cal_run.tu <= cal_run.tv[i1 - 1])
        x = cal_run.ys[m]
        if v < 2 or len(x) < 1000 or any(abs(v - q[0]) < 0.6 for q in peaks):
            continue
        f, P = signal.welch(x - x.mean(), fs=200, nperseg=512)
        sel = f > 5
        peaks.append((v, f[sel][np.argmax(P[sel])], x.std()))
        ax[0].semilogy(f, P, label=f"{v:.1f} m/s")
        if v > 9.5:
            hi = P
    for v, fp, sd in peaks:
        say(f"unfiltered stream at {v:.1f} m/s: peak {fp:.1f} Hz, sd {sd:.3f} m/s")
    mac("ResFreq", np.mean([p[1] for p in peaks]), "{:.1f}")
    ax[0].axvline(np.mean([p[1] for p in peaks]), color="C3", ls="--", lw=0.5)
    ax[0].set_title("(a) Unfiltered stream (Nov 4)", fontsize=6.5)
    ax[0].set_ylabel("PSD ((m/s)$^2$/Hz)")
    ax[0].set_xlabel("Frequency (Hz)")
    ax[0].legend(fontsize=5.3)
    # filter magnitude estimate
    Pd = []
    for r in demo_runs:
        for i0, i1 in plateaus(r.tv, r.yv):
            if r.yv[i0:i1].mean() > 9.5:
                m = (r.tu >= r.tv[i0]) & (r.tu <= r.tv[i1 - 1])
                x = r.ys[m]
                f, P = signal.welch(x - x.mean(), fs=200, nperseg=512)
                Pd.append(P)
    H = np.sqrt(np.mean(Pd, axis=0) / hi)
    band = (f > 0.3) & (f < 8)                     # above 8 Hz the output quantisation floor dominates
    fcs = np.linspace(0.2, 3, 561)
    def err(fc, order):
        return np.sum((20 * np.log10(H[band]) + 10 * np.log10(1 + (f[band] / fc) ** (2 * order))) ** 2)
    fit = {o: min(fcs, key=lambda c: err(c, o)) for o in (1, 2)}
    rms = {o: np.sqrt(err(fit[o], o) / band.sum()) for o in (1, 2)}
    best = min(rms, key=rms.get)
    say(f"filter magnitude fit: order 1 fc {fit[1]:.2f} Hz (rms {rms[1]:.1f} dB), "
        f"order 2 fc {fit[2]:.2f} Hz (rms {rms[2]:.1f} dB) -> order {best}")
    mac("FiltOrder", best, "{:d}")
    mac("FiltFc", fit[best], "{:.1f}")
    mac("FiltRes", 20 * np.log10(H[np.argmin(abs(f - peaks[0][1]))]), "{:.0f}")
    gd = (np.sqrt(2) if best == 2 else 1) / (2 * np.pi * fit[best])
    mac("FiltDelay", gd * 1e3, "{:.0f}")
    say(f"low-frequency group delay of fitted filter: {gd*1e3:.0f} ms")
    sel = (f > 0) & (f < 8)
    ax[1].semilogx(f[sel], 20 * np.log10(H[sel]), "k.", ms=2.5, label="demo / unfiltered")
    ff = np.logspace(-1, 2, 200)
    ax[1].semilogx(ff, -10 * np.log10(1 + (ff / fit[best]) ** (2 * best)), "C3",
                   label=f"order {best}, $f_c={fit[best]:.1f}$ Hz")
    ax[1].axvspan(8, 100, color="0.9", lw=0)
    ax[1].text(9, -12, "output\nquantisation\nfloor", fontsize=5, color="0.4")
    ax[1].set_ylim(-70, 5)
    ax[1].set_title("(b) Inferred filter magnitude", fontsize=6.5)
    ax[1].set_xlabel("Frequency (Hz)")
    ax[1].set_ylabel("$|H|$ (dB)")
    ax[1].legend(fontsize=5.3, loc="lower left")
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "psd.pdf"), bbox_inches="tight")
    plt.close(fig)


def step_metrics(runs):
    """Transitions between consecutive plateaus: 10-90 % times, 50 % delay,
    overshoot; written as LaTeX table rows."""
    rows = []
    for r in runs:
        p = plateau_table(r)
        for k in range(len(p) - 1):
            a, b = p.iloc[k], p.iloc[k + 1]
            if abs(b.v - a.v) < 0.8:
                continue
            ma = (r.tu >= a.t0) & (r.tu <= a.t1)
            mb = (r.tu >= b.t0) & (r.tu <= b.t1)
            e0, e1 = r.ys[ma].mean(), r.ys[mb].mean()

            def cross(t, y, y0, y1, fr):
                lvl, sg = y0 + fr * (y1 - y0), np.sign(y1 - y0)
                m = (t > a.t1) & (t < b.t0 + 1)
                idx = np.flatnonzero(sg * (y[m] - lvl) >= 0)
                return t[m][idx[0]] if len(idx) else np.nan
            tr = [cross(r.tv, r.yv, a.v, b.v, q) for q in (0.1, 0.5, 0.9)]
            te = [cross(r.tu, r.ys, e0, e1, q) for q in (0.1, 0.5, 0.9)]
            m = (r.tu > a.t1) & (r.tu < b.t0 + 1)
            sg = np.sign(e1 - e0)
            ov = max(0.0, (sg * r.ys[m]).max() - sg * e1) / abs(e1 - e0) * 100
            rows.append(dict(run=r.label[-1], v0=a.v, v1=b.v, tr_ref=tr[2] - tr[0],
                             tr_est=te[2] - te[0], delay=(te[1] - tr[1]) * 1e3, ov=ov,
                             ss=b.bias))
    d = pd.DataFrame(rows)
    say("\n== Transitions between plateaus\n" + d.round(3).to_string())
    with open(os.path.join(RES, "steps.tex"), "w") as fh:
        fh.write("\\begin{tabular}{@{}clccccc@{}}\n\\toprule\n"
                 "Run & Step & $t_r^{\\mathrm{ref}}$ & $t_r^{\\mathrm{est}}$ & "
                 "Delay & Ovs. & Error \\\\\n & (m/s) & (s) & (s) & (ms) & (\\%) & (m/s) \\\\\n\\midrule\n")
        for _, q in d[d.v1 > 0.5].iterrows():
            fh.write(f"{q.run} & {q.v0:.1f}$\\to${q.v1:.1f} & {q.tr_ref:.2f} & {q.tr_est:.2f} & "
                     f"{q.delay:+.0f} & {q.ov:.0f} & {q.ss:+.2f} \\\\\n")
        fh.write("\\bottomrule\n\\end{tabular}\n")
    s = d[d.v1 > 0.5]
    mac("StepDelayMed", s.delay.median(), "{:+.0f}")
    mac("StepOvMax", s.ov.max(), "{:.0f}")
    mac("StepOvMed", s.ov.median(), "{:.0f}")
    return d


def hysteresis(runs, shift):
    """Mean error on the long up-ramp vs the down-ramp, 1 m/s bins over 2-9 m/s,
    as logged and after delaying our output by `shift` s."""
    say("\n== Hysteresis (up-ramp minus down-ramp error, mean over 2-9 m/s bins)")
    out = {}
    for sh in (0.0, shift):
        diffs = []
        for r in runs:
            e = r.est_at(r.tv, sh) - r.yv
            dv = np.gradient(pd.Series(r.yv).rolling(5, center=True, min_periods=1).mean().to_numpy(), r.tv)
            for lo in range(2, 9):
                b = (r.yv >= lo) & (r.yv < lo + 1)
                up, dn = b & (dv > 0.3), b & (dv < -0.3)
                if up.sum() >= 2 and dn.sum() >= 2:
                    diffs.append(e[up].mean() - e[dn].mean())
        out[sh] = np.mean(diffs)
        say(f"delay {sh:.2f} s: up-down error difference {out[sh]:+.3f} m/s ({len(diffs)} bins)")
    mac("HystRaw", out[0.0], "{:+.2f}")
    mac("HystShift", out[shift], "{:+.2f}")


def retrospective(r1, r2, n_hat, delta):
    """Re-calibration on the assessed logs, scored out of sample.
    Run 2's output is mapped back to calibration 1's domain (u = v^2 - delta),
    so both runs share one raw feature. A map is fitted on one run and scored on
    the other (two folds); clamped zeros stay zero."""
    say("\n== Retrospective re-calibration (two-fold, fit on one run, score the other)")

    def feats(r, sh, back):
        e = r.est_at(r.tv, sh)
        u = np.where(e > 0.02, e ** 2 - back, np.nan)
        return e, u

    def model(p, u, expo):
        return p[0] * np.clip(u + p[1], 0, None) ** expo

    def predict(p, u, expo):
        return np.where(np.isnan(u), 0.0, model(p, np.nan_to_num(u, nan=-1e9), expo))

    rows = {}
    for sh in (0.0, 0.10):
        data = [feats(r1, sh, 0.0), feats(r2, sh, delta)]
        refs = [r1.yv, r2.yv]
        base = np.mean([np.mean((data[k][0] - refs[k]) ** 2) for k in (0, 1)])
        rows[("deployed", sh)] = base
        for name, expo in (("square root", 0.5), ("exponent", 1.0 / n_hat)):
            mses = []
            for tr, te in ((0, 1), (1, 0)):
                u, y = data[tr][1], refs[tr]
                m = (y > 1.5) & np.isfinite(u)
                f = least_squares(lambda p: model(p, u[m], expo) - y[m], [1.0, 0.0])
                mses.append(np.mean((predict(f.x, data[te][1], expo) - refs[te]) ** 2))
            rows[(name, sh)] = np.mean(mses)
    for k, v in rows.items():
        say(f"{k[0]:12s} delay {k[1]:.2f} s: held-out MSE {v:.4f}")
    b0 = rows[("deployed", 0.0)]
    mac("RetroBase", b0, "{:.3f}")
    mac("RetroDelay", rows[("deployed", 0.10)], "{:.3f}")
    mac("RetroSqrt", rows[("square root", 0.0)], "{:.3f}")
    mac("RetroExp", rows[("exponent", 0.0)], "{:.3f}")
    mac("RetroExpDelay", rows[("exponent", 0.10)], "{:.3f}")
    mac("RetroExpGain", 100 * (1 - rows[("exponent", 0.0)] / b0), "{:.0f}")
    mac("RetroAllGain", 100 * (1 - rows[("exponent", 0.10)] / b0), "{:.0f}")
    mac("RetroDelayGain", 100 * (1 - rows[("deployed", 0.10)] / b0), "{:.0f}")


# ------------------------------------------------------------------ figures
def fig_demo(r1, r2):
    fig = plt.figure(figsize=(DCOL, 4.0))
    gs = fig.add_gridspec(3, 2, height_ratios=[1.35, 0.5, 1.15], hspace=0.75, wspace=0.18)
    for k, r in enumerate((r1, r2)):
        ax = fig.add_subplot(gs[0, k])
        ax.axhspan(*SCORED, color="#2b7bba", alpha=0.07, lw=0)
        ax.plot(r.tu, r.ys, "C3", lw=0.6, label="Our estimate (200 Hz)")
        ax.step(r.tv, r.yv, "k", where="post", lw=0.7, label="VDAS reference (4 Hz)")
        ax.set_xlim(0, r.tv[-1])
        ax.set_title(f"({'ab'[k]}) Run {k+1}, calibration {k+1}")
        plt.setp(ax.get_xticklabels(), visible=False)
        if k == 0:
            ax.set_ylabel("Airspeed (m/s)")
            ax.legend(loc="lower center", ncol=1, fontsize=5.8)
        axr = fig.add_subplot(gs[1, k], sharex=ax)
        res = r.ya - r.yv
        inr = (r.yv >= SCORED[0]) & (r.yv <= SCORED[1])
        axr.plot(r.tv[inr], res[inr], ".", ms=1.6, color="C0", label="ref. in 2\u201310 m/s")
        axr.plot(r.tv[~inr], res[~inr], ".", ms=1.6, color="0.6", label="ref. outside 2\u201310 m/s")
        axr.axhline(0, color="k", lw=0.5)
        axr.set_ylim(-2.5, 2.5)
        if k == 0:
            axr.set_ylabel("Error (m/s)")
            axr.legend(fontsize=5.3, ncol=2, loc="lower right", markerscale=3)
        axr.set_xlabel("Time (s)", labelpad=1)
    # zooms: (c) step 8 -> 10 m/s, (d) ramp down with instantaneous error, (e) gusts
    sub = gs[2, :].subgridspec(1, 3, wspace=0.42)
    p2 = plateau_table(r2)
    k = int(np.argmax(p2.v.values[1:] - p2.v.values[:-1] > 1.5)) + 1   # first up-step into ~10
    k = len(p2) - 1 if p2.v.iloc[-1] > 9.5 else k
    up = [j for j in range(1, len(p2)) if p2.v.iloc[j] - p2.v.iloc[j - 1] > 1.5]
    k = up[-1] if up else len(p2) - 1
    a0, a1 = p2.t1.iloc[k - 1] - 1.5, p2.t0.iloc[k] + 4
    z = fig.add_subplot(sub[0, 0])
    m, mv = (r2.tu > a0) & (r2.tu < a1), (r2.tv > a0) & (r2.tv < a1)
    z.plot(r2.tu[m], r2.ys[m], "C3", lw=0.8)
    z.step(r2.tv[mv], r2.yv[mv], "k", where="post", lw=0.7)
    v0, v1 = p2.v.iloc[k - 1], p2.v.iloc[k]
    for fr in (0.1, 0.9):
        z.axhline(v0 + fr * (v1 - v0), color="0.6", ls=":", lw=0.6)
    z.annotate(f"steady error {p2.bias.iloc[k]:+.2f} m/s", xy=(a1 - 0.3, v1 + p2.bias.iloc[k]),
               xytext=(a0 + 0.2, v1 - 1.1), fontsize=5.5, arrowprops=dict(arrowstyle="->", lw=0.5))
    z.set_title(f"(c) Run 2 step {v0:.1f}$\\to${v1:.1f} m/s", fontsize=6.5)
    z.set_xlabel("Time (s)")
    z.set_ylabel("Airspeed (m/s)")
    # ramp down: airspeed on top, error underneath (separate axes, shared time)
    j = max(i for i in range(len(p2)) if p2.v.iloc[i] > 9.5)
    a0, a1 = p2.t1.iloc[j] - 1, p2.t1.iloc[j] + 11
    dsub = sub[0, 1].subgridspec(2, 1, height_ratios=[2, 1], hspace=0.08)
    z = fig.add_subplot(dsub[0])
    m, mv = (r2.tu > a0) & (r2.tu < a1), (r2.tv > a0) & (r2.tv < a1)
    z.plot(r2.tu[m], r2.ys[m], "C3", lw=0.8)
    z.step(r2.tv[mv], r2.yv[mv], "k", where="post", lw=0.7)
    z.set_title("(d) Run 2 ramp to rest", fontsize=6.5)
    plt.setp(z.get_xticklabels(), visible=False)
    ze = fig.add_subplot(dsub[1], sharex=z)
    ze.axhline(0, color="k", lw=0.5)
    ze.plot(r2.tv[mv], (r2.ya - r2.yv)[mv], "o", ms=1.8, color="C0")
    ze.set_ylim(-2.5, 1)
    ze.set_ylabel("Error\n(m/s)", fontsize=5.5)
    ze.tick_params(labelsize=5.5)
    ze.set_xlabel("Time (s)")
    # gusts
    z = fig.add_subplot(sub[0, 2])
    a0, a1 = 94, r2.tv[-1] - 2
    m, mv = (r2.tu > a0) & (r2.tu < a1), (r2.tv > a0) & (r2.tv < a1)
    z.axhspan(*SCORED, color="#2b7bba", alpha=0.07, lw=0)
    z.plot(r2.tu[m], r2.ys[m], "C3", lw=0.8)
    z.step(r2.tv[mv], r2.yv[mv], "k", where="post", lw=0.7)
    z.set_title("(e) Run 2 gust sequence", fontsize=6.5)
    z.set_xlabel("Time (s)")
    fig.savefig(os.path.join(FIG, "demo_runs.pdf"), bbox_inches="tight")
    plt.close(fig)


def fig_errors(pc, p1, p2, n_hat, reg1, reg2):
    """(a) mean error on each steady plateau (plateau tables from plateau_table);
    (b) MSE and RMSE by regime (regime dicts from regimes)."""
    fig, ax = plt.subplots(1, 2, figsize=(COL, 2.0), gridspec_kw={"width_ratios": [1.15, 1]})
    for p, mk, lab in ((pc, "s", "Cal. 1, check run"), (p1, "o", "Run 1 (cal. 1)"),
                       (p2, "^", "Run 2 (cal. 2)")):
        ax[0].plot(p.v, p.bias, mk, ms=3.5, label=lab)
    vv = np.linspace(2, 10.5, 100)
    ax[0].plot(vv, 7.5 * (vv / 7.5) ** (n_hat / 2) - vv, "k-", lw=0.7,
               label=f"expected if $F\\propto v^{{{n_hat:.2f}}}$")
    ax[0].axhline(0, color="k", lw=0.4)
    ax[0].set_xlabel("Plateau $v^{\\mathrm{ref}}$ (m/s)")
    ax[0].set_ylabel("Mean error (m/s)")
    ax[0].legend(fontsize=5.2, loc="lower left")
    cats = list(reg1.keys())
    x = np.arange(len(cats))
    w = 0.2
    for k, (reg, lab) in enumerate(((reg1, "run 1"), (reg2, "run 2"))):
        ax[1].bar(x + (2 * k - 1.5) * w, [reg[c][1] for c in cats], w, label=f"MSE, {lab}",
                  color=f"C{k}")
        ax[1].bar(x + (2 * k - 0.5) * w, [np.sqrt(reg[c][1]) for c in cats], w,
                  label=f"RMSE, {lab}", color=f"C{k}", alpha=0.45)
    ax[1].set_xticks(x, ["ref.\n= 0", "steady\n$\\geq$9.5", "steady\n6\u20139.5", "trans-\nient"],
                     fontsize=4.9)
    ax[1].set_ylabel("MSE (m$^2$/s$^2$), RMSE (m/s)", fontsize=6)
    ax[1].legend(fontsize=4.8, ncol=2, loc="lower center", bbox_to_anchor=(0.5, 0.98), columnspacing=0.6,
                 handlelength=1.2)
    fig.tight_layout(w_pad=0.6)
    fig.savefig(os.path.join(FIG, "error_analysis.pdf"), bbox_inches="tight")
    plt.close(fig)


# ------------------------------------------------------------------ main
# Firmware low-pass cut-offs used in the assessed runs (calibration profiles):
# calibration 1 -> run 1, calibration 2 -> run 2. Second-order Butterworth at 200 Hz.
CUTOFF = {"A": 0.75, "B": 0.51}


def extra_numbers(r1, r2, reg1, reg2, sm, steps, k1, k2, cal_runs, cal1):
    """Remaining numbers quoted in the text, so that every logger-derived value in
    the paper comes from this script."""
    say("\n== Further numbers quoted in the text")
    # streaming completeness (Section VII-2)
    for r, nm in ((r1, "A"), (r2, "B")):
        mac(f"StreamN{nm}", r.n_stream, "{:d}")
        mac(f"StreamExp{nm}", r.n_expected, "{:d}")
    # RMSE by part of the run (Section V-3, Fig. 5d)
    for reg, nm in ((reg1, "A"), (reg2, "B")):
        mac(f"RmseTrans{nm}", np.sqrt(reg["Transient"][1]), "{:.2f}")
        mac(f"RmseHi{nm}", np.sqrt(reg["Steady\n$\\geq$9.5"][1]), "{:.2f}")
        mac(f"RmseZero{nm}", np.sqrt(reg["Ref.$=$0"][1]), "{:.2f}")
    # small sources as a share of the MSE (Section V-3)
    scat = np.mean([float(MAC["HiSD"]) ** 2 / sm[k][0]["mse"] for k in ("Run 1", "Run 2")])
    refsh = max((sm[k][1]["rmse"] ** 2 - sm[k][2] ** 2) / sm[k][1]["rmse"] ** 2
                for k in ("Run 1", "Run 2"))
    mac("ScatterPct", 100 * scat, "{:.0f}")
    mac("RefSharePct", np.ceil(100 * refsh), "{:.0f}")
    say(f"plateau scatter ~{100*scat:.1f}% of MSE; reference resolution up to {100*refsh:.1f}% (2-10 m/s)")
    # absolute values for "read 0.24-0.51 m/s low"
    mac("HiLowAbs", abs(float(MAC["HiBiasMax"])), "{:.2f}")
    mac("HiHighAbs", abs(float(MAC["HiBiasMin"])), "{:.2f}")
    mac("DeltaCVPct", 100 * float(MAC["DeltaCV"]), "{:.0f}")
    # steps (Section VI-1)
    s = steps[steps.v1 > 0.5]
    mac("StepLeadCount", int((s.delay < 0).sum()), "{:d}")
    mac("StepLeadMs", abs(s.delay.median()), "{:.0f}")
    mac("HystRawAbs", abs(float(MAC["HystRaw"])), "{:.2f}")
    mac("HystShiftAbs", abs(float(MAC["HystShift"])), "{:.2f}")
    mac("LowBiasAbs", abs(float(MAC["LowBias"])), "{:.2f}")
    mac("StepCount", len(s), "{:d}")
    # calibration-1 data: reading at rest per Nov-4 run; check run at ~10 m/s
    rest = []
    for r in cal_runs:
        for i0, i1 in plateaus(r.tv, r.yv):
            m = (r.tu >= r.tv[i0]) & (r.tu <= r.tv[i1 - 1])
            if r.yv[i0:i1].mean() < 0.5 and m.sum() > 50:
                rest.append(r.ys[m].mean())
                break
    if rest:
        mac("RestMin", min(rest), "{:.1f}")
        mac("RestMax", max(rest), "{:.1f}")
        say(f"Nov-4 reading at rest per run: {', '.join(f'{x:.2f}' for x in rest)} m/s")
    pc = plateau_table(cal1)
    mac("CheckHiAbs", abs(pc[pc.v > 9.5].bias.mean()), "{:.1f}")
    # filter attenuation at the resonance, low-frequency delay, implied reference lag
    fres = float(MAC["ResFreq"])
    for nm, lead in (("A", k1), ("B", k2)):
        b, a = signal.butter(2, CUTOFF[nm], fs=200.0)
        _, h = signal.freqz(b, a, worN=[fres], fs=200.0)
        _, gd = signal.group_delay((b, a), w=[0.05], fs=200.0)
        delay = gd[0] / 200.0
        mac(f"FiltAtt{nm}", -20 * np.log10(abs(h[0])), "{:.0f}")
        mac(f"FiltDel{nm}", delay, "{:.2f}")
        mac(f"RefLag{nm}", delay + lead, "{:.2f}")
        say(f"run {nm}: {CUTOFF[nm]} Hz -> {-20*np.log10(abs(h[0])):.1f} dB at {fres} Hz, "
            f"delay {delay:.3f} s, implied reference lag {delay + lead:.2f} s")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tunnel", required=True)
    ap.add_argument("--team", required=True)
    a = ap.parse_args()
    os.makedirs(FIG, exist_ok=True)
    os.makedirs(RES, exist_ok=True)
    T = lambda p: os.path.join(a.tunnel, p)
    cal_runs = [Run(T("run_20261104_151708"), "Nov4 A"), Run(T("run_20261104_152041"), "Nov4 B"),
                Run(T("run_20261104_152520"), "Nov4 C")]
    cal1 = Run(T("run_20261105_091624_live_drag_plate"), "Cal1 run")
    team = sorted(glob.glob(os.path.join(a.team, "*_student.csv")))
    r1 = Run(team[0].replace("_student.csv", ""), "Run 1")
    r2 = Run(team[1].replace("_student.csv", ""), "Run 2")
    old = Run(T("run_20261029_123519_live_drag_plate_giga"), "Old tunnel")

    stream_check([r1, r2] + cal_runs)
    reference_props(r1)
    x = old.yv - pd.Series(old.yv).rolling(200, center=True, min_periods=1).mean().to_numpy()
    say(f"\nOld tunnel (GIGA, {old.fs_ref:.0f} Hz) reference noise about 2-s mean: sd {x.std():.2f} m/s")
    mac("OldRefSD", x.std(), "{:.2f}")

    n_hat = calibration_exponent(cal_runs)
    sm = scored_metrics([r1, r2], cal1)
    for key, nm in (("Run 1", "A"), ("Run 2", "B"), ("Cal1 run", "C")):
        al, sc, adj = sm[key]
        mac(f"MSEall{nm}", al["mse"], "{:.3f}"); mac(f"RMSEall{nm}", al["rmse"], "{:.3f}")
        mac(f"Biasall{nm}", al["bias"], "{:+.3f}"); mac(f"SDall{nm}", al["sd"], "{:.3f}")
        mac(f"MSE{nm}", sc["mse"], "{:.3f}"); mac(f"RMSE{nm}", sc["rmse"], "{:.3f}")
        mac(f"Bias{nm}", sc["bias"], "{:+.3f}"); mac(f"SD{nm}", sc["sd"], "{:.3f}")
        mac(f"RMSEadj{nm}", adj, "{:.3f}")

    for r, nm in ((r1, "A"), (r2, "B")):
        above = r.yv > SCORED[1]
        res = r.ya - r.yv
        say(f"{r.label}: {above.mean()*100:.0f}% of pairs have v_ref > 10 m/s, "
            f"carrying {np.sum(res[above]**2)/np.sum(res**2)*100:.0f}% of squared error")
        mac(f"AboveFrac{nm}", above.mean() * 100, "{:.0f}")
        mac(f"AboveShare{nm}", np.sum(res[above] ** 2) / np.sum(res ** 2) * 100, "{:.0f}")
    reg1, k1 = regimes(r1)
    reg2, k2 = regimes(r2)
    say("\n== Error by regime (share of squared error)")
    for lab, reg, k in (("run 1", reg1, k1), ("run 2", reg2, k2)):
        say(lab + ": " + "; ".join(f"{c}: n={v[0]}, {v[2]*100:.0f}%" for c, v in reg.items())
            + f"; transient error slope {k*1e3:.0f} ms")
    mac("LeadA", k1 * 1e3, "{:.0f}"); mac("LeadB", k2 * 1e3, "{:.0f}")
    mac("TransShareA", reg1["Transient"][2] * 100, "{:.0f}")
    mac("TransShareB", reg2["Transient"][2] * 100, "{:.0f}")
    mac("ZeroShareA", reg1["Ref.$=$0"][2] * 100, "{:.0f}")
    mac("ZeroShareB", reg2["Ref.$=$0"][2] * 100, "{:.0f}")
    mac("HiShareA", reg1["Steady\n$\\geq$9.5"][2] * 100, "{:.0f}")
    mac("HiShareB", reg2["Steady\n$\\geq$9.5"][2] * 100, "{:.0f}")
    mac("MidShareA", reg1["Steady\n6-9.5"][2] * 100, "{:.0f}")
    mac("MidShareB", reg2["Steady\n6-9.5"][2] * 100, "{:.0f}")

    m = (cal1.tv > 56) & (cal1.tv < 67)          # slow descent through 3-4 m/s, morning run
    say(f"\nCal-1 morning run 56-67 s: ref mean {cal1.yv[m].mean():.2f}, mean error "
        f"{(cal1.ya[m]-cal1.yv[m]).mean():+.2f} m/s")
    mac("LowV", cal1.yv[m].mean(), "{:.1f}")
    mac("LowBias", (cal1.ya[m] - cal1.yv[m]).mean(), "{:+.2f}")
    p1, p2, pc = plateau_table(r1), plateau_table(r2), plateau_table(cal1)
    say("\n== Plateaus\n" + "\n".join(f"{lab}\n{p.round(3).to_string()}" for lab, p in
                                      (("run 1", p1), ("run 2", p2), ("cal1", pc))))
    hi1, hi2 = p1[p1.v > 9.5].bias, p2[p2.v > 9.5].bias
    mac("HiBiasMin", min(hi1.min(), hi2.min()), "{:+.2f}")
    mac("HiBiasMax", max(hi1.max(), hi2.max()), "{:+.2f}")
    mac("HiSD", np.mean(np.r_[p1[p1.v > 9.5].sd200, p2[p2.v > 9.5].sd200]), "{:.3f}")
    delta = run_to_run(p1[p1.v > 5].reset_index(drop=True), p2[p2.v > 5].reset_index(drop=True))

    spectra(cal_runs[0], [r1, r2])
    steps = step_metrics([r1, r2])
    hysteresis([r1, r2], 0.10)
    retrospective(r1, r2, n_hat, delta)
    extra_numbers(r1, r2, reg1, reg2, sm, steps, k1, k2, cal_runs, cal1)

    with open(os.path.join(RES, "metrics.tex"), "w") as fh:
        fh.write("% generated by scripts/analyse_team6.py -- do not edit\n")
        for k, v in MAC.items():
            v = "--" if v.strip("+") == "nan" else v
            fh.write(f"\\expandafter\\def\\csname r{k}\\endcsname{{{v}}}\n")
    with open(os.path.join(RES, "summary.txt"), "w") as fh:
        fh.write("\n".join(LOG) + "\n")
    say("\nwrote figures/*.pdf, results/metrics.tex, results/summary.txt")


if __name__ == "__main__":
    main()
