"""Recording sessions (aligned rows, the parameters in force, a timeline of what changed mid-run)
and the index of recorded runs in calibration/runs/ that the dashboard browses."""
from __future__ import annotations

import csv
import json
import math
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from . import metrics, models

ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / "runs"

SESSION_COLS = ("t", "gt", "v_board", "v_pc", "err_board", "err_pc", "feature", "rho", "temp_c",
                "pressure_pa", "model", "pset")
EVENT_KINDS = ("start", "stop", "param", "push", "fit", "model", "tare", "command", "note", "board")


def all_param_names() -> list[str]:
    """Union of every model's parameters, in a stable order (CSV columns stay fixed across model switches)."""
    out: list[str] = []
    for mid in models.MODELS:
        for name in models.default_params(mid):
            if name not in out:
                out.append(name)
    return out


def _num(x):
    x = float(x)
    return x if math.isfinite(x) else None


def _jsonable(o):
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (float, np.floating)):
        return _num(o)
    if isinstance(o, np.integer):
        return int(o)
    return o


class Session:
    """One recording. Rows arrive already aligned (GT interpolated onto each 200 Hz sensor sample),
    each tagged with the parameter-set id (`pset`) in force. Any parameter/model change opens a new
    pset; segment metrics are computed per pset, skipping `warmup_s` after the change so filter
    transients don't get blamed on the new values.

    Files: `<stem>.csv` (rows; every parameter is its own column) and `<stem>.json`
    (meta, param_sets, events, segments, summary), rewritten every `flush_s` and on close.
    """

    def __init__(self, model: str, params: dict, meta: dict | None = None, label: str = "",
                 path: str | Path | None = None, warmup_s: float = 0.5, fs: float = 200.0, flush_s: float = 2.0,
                 t0: float | None = None):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)[:40]
        self.path = Path(path) if path else RUNS_DIR / f"run_{stamp}{'_' + safe if safe else ''}.csv"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.json_path = self.path.with_suffix(".json")
        self.param_names = all_param_names()
        self.warmup_s, self.fs, self.flush_s = warmup_s, fs, flush_s
        self._lock = threading.RLock()
        self._f = self.path.open("w", newline="")
        self._w = csv.writer(self._f)
        self._w.writerow([*SESSION_COLS, *self.param_names])
        self.rows = 0
        self.closed = False
        self.model = model
        self.params = dict(params)
        self.t_start = time.perf_counter() if t0 is None else t0
        self.t_last = self.t_start
        self.meta = {"started": datetime.now().astimezone().isoformat(timespec="seconds"), "stopped": None,
                     "fs": fs, "warmup_s": warmup_s, "label": label, "csv": self.path.name,
                     "t0": self.t_start, **(meta or {}), "model": model}
        self.param_sets: list[dict] = []
        self.events: list[dict] = []
        self.segments: list[dict] = []   # closed segments (metrics frozen)
        self._seg = None                 # current segment accumulator
        self._last_flush = 0.0
        self._open_pset(self.t_start, changed=[])
        self.event("start", detail={"model": model}, t=self.t_start)

    # ------------------------------------------------------------------------------ timeline
    @property
    def pset(self) -> int:
        return self.param_sets[-1]["pset"]

    def _open_pset(self, t: float, changed: list[str]):
        k = len(self.param_sets)
        self.param_sets.append({"pset": k, "t_start": t, "t_end": None, "model": self.model, "params": dict(self.params)})
        self._seg = {"pset": k, "t_start": t, "changed": changed, "model": self.model,
                     "t": [], "gt": [], "pc": [], "board": []}

    def _close_segment(self, t: float):
        seg = self._seg
        self.param_sets[-1]["t_end"] = t
        self.segments.append(self._summarise(seg, t, final=True))
        self._seg = None

    def _summarise(self, seg: dict, t_end: float, final: bool = False) -> dict:
        t = np.asarray(seg["t"], dtype=float)
        keep = t >= seg["t_start"] + self.warmup_s
        gt = np.asarray(seg["gt"], dtype=float)[keep]
        pc = np.asarray(seg["pc"], dtype=float)[keep]
        bd = np.asarray(seg["board"], dtype=float)[keep]
        mp = metrics.compute(pc, gt, self.fs) if np.isfinite(pc).any() else None
        mb = metrics.compute(bd, gt, self.fs) if np.isfinite(bd).any() else None
        out = {"pset": seg["pset"], "model": seg["model"], "t_start": seg["t_start"], "t_end": t_end,
               "duration_s": t_end - seg["t_start"], "n": int(len(t)), "n_scored": int(keep.sum()),
               "changed": seg["changed"], "metrics_pc": mp, "metrics_board": mb}
        if final:  # keep the raw arrays only while the segment is open
            seg.clear()
        return _jsonable(out)

    def change(self, model: str, params: dict, kind: str = "param", detail=None, t: float | None = None) -> bool:
        """Record a parameter/model change. Returns True if a new pset was opened."""
        with self._lock:
            if self.closed:
                return False
            t = time.perf_counter() if t is None else t
            changed = [k for k in sorted(set(params) | set(self.params))
                       if not _same(params.get(k), self.params.get(k))]
            if model == self.model and not changed:
                return False
            for k in changed:
                self.events.append(_jsonable({"t": t, "rel_s": t - self.t_start, "kind": kind, "name": k,
                                              "from": self.params.get(k), "to": params.get(k), "detail": detail}))
            if model != self.model:
                self.events.append({"t": t, "rel_s": t - self.t_start, "kind": "model", "name": "model",
                                    "from": self.model, "to": model, "detail": detail})
                changed = changed + ["model"]
            self._close_segment(t)
            self.model, self.params = model, dict(params)
            self._open_pset(t, changed)
            return True

    def event(self, kind: str, name=None, frm=None, to=None, detail=None, t: float | None = None):
        with self._lock:
            if self.closed:
                return
            t = time.perf_counter() if t is None else t
            self.events.append(_jsonable({"t": t, "rel_s": t - self.t_start, "kind": kind, "name": name,
                                          "from": frm, "to": to, "detail": detail}))

    def note(self, text: str, t: float | None = None):
        self.event("note", detail=str(text)[:500], t=t)

    # ------------------------------------------------------------------------------- rows
    def add_rows(self, t, gt, v_board=None, v_pc=None, feature=None, rho=None, temp_c=None, pressure_pa=None):
        t = np.asarray(t, dtype=float)
        n = len(t)
        if n == 0:
            return
        col = lambda a: np.full(n, np.nan) if a is None else np.asarray(a, dtype=float)  # noqa: E731
        gt, vb, vp = col(gt), col(v_board), col(v_pc)
        cols = [t, gt, vb, vp, vb - gt, vp - gt, col(feature), col(rho), col(temp_c), col(pressure_pa)]
        with self._lock:
            if self.closed:
                return
            pv = [_fmt(self.params.get(k, math.nan)) for k in self.param_names]
            ps = str(self.pset)
            for i in range(n):
                self._w.writerow([f"{t[i]:.6f}", *(_fmt(c[i]) for c in cols[1:]), self.model, ps, *pv])
            self.rows += n
            seg = self._seg
            seg["t"].extend(t.tolist()); seg["gt"].extend(gt.tolist())
            seg["pc"].extend(vp.tolist()); seg["board"].extend(vb.tolist())
            self.t_last = float(t[-1])
            if time.perf_counter() - self._last_flush > self.flush_s:
                self._flush()

    # ----------------------------------------------------------------------------- output
    def all_segments(self) -> list[dict]:
        with self._lock:
            segs = list(self.segments)
            if not self.closed and self._seg is not None:
                segs.append(self._summarise(self._seg, max(self.t_last, self._seg["t_start"])))
            return segs

    def to_dict(self) -> dict:
        with self._lock:
            segs = self.all_segments()
            summary = _summary(segs)
            return _jsonable({"meta": self.meta, "param_sets": self.param_sets, "events": self.events,
                              "segments": segs, "summary": summary})

    def _flush(self):
        self._f.flush()
        tmp = self.json_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=1, allow_nan=False))
        tmp.replace(self.json_path)
        self._last_flush = time.perf_counter()

    def status(self) -> dict:
        with self._lock:
            return _jsonable({"type": "session", "recording": not self.closed, "path": str(self.path),
                              "json_path": str(self.json_path), "elapsed_s": self.t_last - self.t_start,
                              "rows": self.rows, "psets": len(self.param_sets), "pset": self.pset,
                              "segments": self.all_segments()[-12:], "events": self.events[-20:]})

    def close(self, t: float | None = None) -> Path:
        with self._lock:
            if self.closed:
                return self.path
            t = self.t_last if t is None else t
            self.event("stop", t=t)
            self._close_segment(t)
            self.meta["stopped"] = datetime.now().astimezone().isoformat(timespec="seconds")
            self.meta["rows"] = self.rows
            self._flush()
            self.closed = True
            self._f.close()
        return self.path


def _same(a, b) -> bool:
    if a is None or b is None:
        return a is b
    try:
        return float(a) == float(b) or (math.isnan(float(a)) and math.isnan(float(b)))
    except (TypeError, ValueError):
        return a == b


def _fmt(x) -> str:
    x = float(x)
    return "" if not math.isfinite(x) else f"{x:.7g}"


def _summary(segs: list[dict]) -> dict:
    """Scored-sample-weighted totals over all segments, plus the best segment by PC and board MSE."""
    out: dict = {"segments": len(segs)}
    for key in ("metrics_pc", "metrics_board"):
        rows = [(s["n_scored"], s[key]) for s in segs if s.get(key) and s[key].get("mse") is not None]
        if not rows:
            continue
        n = sum(r[0] for r in rows)
        if n == 0:
            continue
        mse = sum(r[0] * r[1]["mse"] for r in rows) / n
        bias = sum(r[0] * r[1]["bias"] for r in rows) / n
        best = min((s for s in segs if s.get(key) and s[key].get("mse") is not None), key=lambda s: s[key]["mse"])
        out[key.replace("metrics_", "")] = {"n": n, "mse": mse, "rmse": mse ** 0.5, "bias": bias,
                                            "best_pset": best["pset"], "best_mse": best[key]["mse"]}
    return out


def load_session(json_path: str | Path) -> dict:
    return json.loads(Path(json_path).read_text())


# ---------- index of recorded runs (dashboard: list_runs, run_summary) ----------
def runs_dir() -> Path:
    return Path(RUNS_DIR)  # looked up at call time so tests can redirect it


def _count_rows(path: Path) -> int:
    n = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            n += chunk.count(b"\n")
    return max(n - 1, 0)  # minus the header


def _csv_only(csv_path: Path) -> dict:
    st = csv_path.stat()
    model = None
    try:
        with csv_path.open(newline="") as f:
            r = csv.reader(f)
            head = next(r, [])
            first = next(r, None)
        if first and "model" in head:
            model = first[head.index("model")] or None
    except (OSError, csv.Error):
        pass
    return {"started": datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds"),
            "model": model, "mode": None, "rows": _count_rows(csv_path), "duration_s": None,
            "psets": None, "best_mse_pc": None}


def _from_sidecar(doc: dict) -> dict:
    meta, summ, segs = doc.get("meta", {}), doc.get("summary", {}), doc.get("segments", [])
    dur = None
    if segs:
        t0, t1 = segs[0].get("t_start"), segs[-1].get("t_end")
        if t0 is not None and t1 is not None:
            dur = t1 - t0
    return {"started": meta.get("started"), "model": meta.get("model"), "mode": meta.get("mode"),
            "rows": meta.get("rows"), "duration_s": dur, "psets": len(doc.get("param_sets", [])),
            "best_mse_pc": (summ.get("pc") or {}).get("best_mse")}


def list_runs() -> list[dict]:
    """Newest first. Session runs come from their JSON sidecar; bare CSVs get what is cheap to infer."""
    d = runs_dir()
    if not d.is_dir():
        return []
    out = []
    for csv_path in d.glob("*.csv"):
        js = csv_path.with_suffix(".json")
        info = None
        if js.is_file():
            try:
                info = _from_sidecar(json.loads(js.read_text()))
            except (OSError, ValueError):
                info = None
        if info is None:
            info = _csv_only(csv_path)
        out.append({"name": csv_path.stem, "csv": csv_path.name, "json": js.name if js.is_file() else None,
                    "size_bytes": csv_path.stat().st_size, **info, "_mtime": csv_path.stat().st_mtime})
    out.sort(key=lambda r: r.pop("_mtime"), reverse=True)
    return out


def resolve(name: str, suffix: str) -> Path:
    """A run file by stem (or file name) inside runs/; refuses anything that escapes the folder."""
    d = runs_dir().resolve()
    stem = Path(str(name)).name
    for ext in (".csv", ".json"):
        if stem.endswith(ext):
            stem = stem[: -len(ext)]
    p = (d / (stem + suffix)).resolve()
    if p.parent != d:
        raise ValueError(f"bad run name {name!r}")
    if not p.is_file():
        raise FileNotFoundError(f"no {suffix} file for run {stem!r} in {d}")
    return p


def run_summary(name: str) -> dict:
    doc = json.loads(resolve(name, ".json").read_text())
    return {"name": Path(str(name)).stem, "meta": doc.get("meta", {}), "summary": doc.get("summary", {}),
            "segments": doc.get("segments", []), "param_sets": doc.get("param_sets", []),
            "events": doc.get("events", [])}
