// Canvas charts: TimeChart (live traces with a hover readout) and XYChart (scatter, curves, bars).
// Colours are CSS custom properties ('--s-sensor'), resolved once per draw so the tokens stay the source.

const styles = new Map();
function cssColor(c) {
  if (!c || !c.startsWith('--')) return c;
  if (!styles.has(c)) styles.set(c, getComputedStyle(document.documentElement).getPropertyValue(c).trim());
  return styles.get(c);
}

function niceTicks(lo, hi, want) {
  if (!(hi > lo)) { hi = lo + 1; lo -= 1; }
  const raw = (hi - lo) / Math.max(1, want);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Number(v.toFixed(10)));
  return out;
}

function tickLabel(v) {
  const a = Math.abs(v);
  if (a >= 10000) return (v / 1000).toFixed(0) + 'k';
  if (a >= 100) return v.toFixed(0);
  if (a >= 10) return String(Number(v.toFixed(1)));
  if (a >= 1) return String(Number(v.toFixed(2)));
  return String(Number(v.toPrecision(2)));
}

class Base {
  constructor(host) {
    this.host = host;
    this.canvas = document.createElement('canvas');
    this.tip = document.createElement('div');
    this.tip.className = 'tip';
    this.tip.hidden = true;
    host.append(this.canvas, this.tip);
    this.hoverX = null;
    this.canvas.addEventListener('pointermove', (e) => {
      this.hoverX = e.clientX - this.canvas.getBoundingClientRect().left;
      this.onHover?.();
    });
    this.canvas.addEventListener('pointerleave', () => { this.hoverX = null; this.tip.hidden = true; this.onHover?.(); });
  }

  begin() {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    if (this.canvas.width !== Math.round(w * dpr) || this.canvas.height !== Math.round(h * dpr)) {
      this.canvas.width = Math.round(w * dpr);
      this.canvas.height = Math.round(h * dpr);
    }
    const ctx = this.canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.font = '12px ' + getComputedStyle(document.body).fontFamily;
    return { ctx, w, h };
  }

  /** Axes and grid. Returns scale functions and the plot rectangle. */
  frame(ctx, w, h, xr, yr, o = {}) {
    const pad = { l: o.left ?? 52, r: 12, t: 12, b: o.noX ? 8 : 28 };
    const X = (v) => pad.l + ((v - xr[0]) / (xr[1] - xr[0])) * (w - pad.l - pad.r);
    const Y = (v) => h - pad.b - ((v - yr[0]) / (yr[1] - yr[0])) * (h - pad.t - pad.b);
    ctx.lineWidth = 1;
    ctx.strokeStyle = cssColor('--grid');
    ctx.fillStyle = cssColor('--ink-3');
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    for (const v of niceTicks(yr[0], yr[1], Math.max(2, Math.floor((h - pad.t - pad.b) / 48)))) {
      if (v < yr[0] || v > yr[1]) continue;
      const y = Math.round(Y(v)) + 0.5;
      ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke();
      ctx.fillText((o.yFormat || tickLabel)(v), pad.l - 10, y);
    }
    if (!o.noX) {
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      for (const v of niceTicks(xr[0], xr[1], Math.max(3, Math.floor((w - pad.l) / 90)))) {
        if (v < xr[0] || v > xr[1]) continue;
        const x = Math.round(X(v)) + 0.5;
        ctx.fillText((o.xFormat || tickLabel)(v), x, h - pad.b + 9);
      }
    }
    if (o.yUnit) {
      ctx.textAlign = 'left';
      ctx.textBaseline = 'top';
      ctx.fillText(o.yUnit, pad.l + 8, pad.t);
    }
    if (o.xUnit) {
      ctx.textAlign = 'right';
      ctx.textBaseline = 'bottom';
      ctx.fillText(o.xUnit, w - pad.r, h - pad.b - 6);
    }
    return { X, Y, pad };
  }

  empty(msg) {
    const { ctx, w, h } = this.begin();
    ctx.fillStyle = cssColor('--ink-3');
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(msg, w / 2, h / 2);
    this.tip.hidden = true;
  }
}

/**
 * Live traces against time. draw({ t, i0, window, lines: [{ label, color, data, width?, dash?, digits? }],
 * unit, yMin?, yMax?, zero? }). Lines are min/max decimated per pixel so 30 k points stay cheap.
 */
export class TimeChart extends Base {
  draw(m) {
    const { t, lines } = m;
    const n = t.length, i0 = Math.max(0, m.i0 ?? 0);
    if (n - i0 < 2) return this.empty(m.emptyText || 'Waiting for data…');
    const tEnd = t[n - 1], t0 = tEnd - m.window;
    let lo = Infinity, hi = -Infinity;
    for (const L of lines) {
      for (let i = i0; i < n; i++) {
        const v = L.data[i];
        if (Number.isFinite(v)) { if (v < lo) lo = v; if (v > hi) hi = v; }
      }
    }
    if (lo === Infinity) return this.empty(m.emptyText || 'No readings in this window');
    if (m.yMin != null) lo = Math.min(lo, m.yMin);
    if (m.yMax != null) hi = Math.max(hi, m.yMax);
    const span = hi - lo || Math.max(Math.abs(hi) * 0.01, m.minSpan || 1);
    if (m.minSpan && span < m.minSpan) { const c = (hi + lo) / 2; lo = c - m.minSpan / 2; hi = c + m.minSpan / 2; }
    else { lo -= span * 0.08; hi += span * 0.08; }
    if (m.yMin != null && lo < m.yMin) lo = m.yMin;

    const { ctx, w, h } = this.begin();
    const F = this.frame(ctx, w, h, [t0, tEnd], [lo, hi], {
      yUnit: m.unit, xFormat: (v) => (v - tEnd === 0 ? 'now' : `${Math.round(v - tEnd)} s`), yFormat: m.yFormat,
    });
    ctx.save();
    ctx.beginPath();
    ctx.rect(F.pad.l, 0, w - F.pad.l - F.pad.r, h);
    ctx.clip();
    if (m.zero && lo < 0 && hi > 0) {
      ctx.strokeStyle = cssColor('--line-strong');
      ctx.beginPath(); ctx.moveTo(F.pad.l, Math.round(F.Y(0)) + 0.5); ctx.lineTo(w, Math.round(F.Y(0)) + 0.5); ctx.stroke();
    }
    for (const L of lines) this.trace(ctx, F, t, L, i0, n, w);
    ctx.restore();
    this.hover(ctx, F, w, h, m, i0, n, tEnd);
  }

  trace(ctx, F, t, L, i0, i1, w) {
    ctx.strokeStyle = cssColor(L.color);
    ctx.lineWidth = L.width || 1.75;
    ctx.lineJoin = 'round';
    ctx.setLineDash(L.dash ? [5, 4] : []);
    ctx.beginPath();
    const per = Math.max(1, Math.floor((i1 - i0) / (w * 1.5)));
    let pen = false;
    for (let i = i0; i < i1; i += per) {
      let lo = Infinity, hi = -Infinity, ilo = i, ihi = i;
      const end = Math.min(i1, i + per);
      for (let j = i; j < end; j++) {
        const v = L.data[j];
        if (!Number.isFinite(v)) continue;
        if (v < lo) { lo = v; ilo = j; }
        if (v > hi) { hi = v; ihi = j; }
      }
      if (lo === Infinity) { pen = false; continue; }
      const pts = per === 1 ? [i] : ilo < ihi ? [ilo, ihi] : [ihi, ilo];
      for (const k of pts) {
        const x = F.X(t[k]), y = F.Y(L.data[k]);
        if (pen) ctx.lineTo(x, y); else { ctx.moveTo(x, y); pen = true; }
      }
    }
    ctx.stroke();
    ctx.setLineDash([]);
  }

  hover(ctx, F, w, h, m, i0, n, tEnd) {
    const x = this.hoverX;
    if (x == null || x < F.pad.l || x > w - F.pad.r) { this.tip.hidden = true; return; }
    const t = m.t, tq = tEnd - m.window + ((x - F.pad.l) / (w - F.pad.l - F.pad.r)) * m.window;
    let lo = i0, hi = n - 1;
    while (lo < hi) { const k = (lo + hi) >> 1; if (t[k] < tq) lo = k + 1; else hi = k; }
    const xi = F.X(t[lo]);
    ctx.strokeStyle = cssColor('--ink-3');
    ctx.beginPath(); ctx.moveTo(Math.round(xi) + 0.5, F.pad.t); ctx.lineTo(Math.round(xi) + 0.5, h - F.pad.b); ctx.stroke();
    const rows = [];
    for (const L of m.lines) {
      const v = L.data[lo];
      if (!Number.isFinite(v)) continue;
      ctx.fillStyle = cssColor(L.color);
      ctx.beginPath(); ctx.arc(xi, F.Y(v), 3.5, 0, 7); ctx.fill();
      rows.push([L.label, v.toFixed(L.digits ?? 3)]);
    }
    this.tip.replaceChildren();
    const head = document.createElement('b');
    head.textContent = `${(t[lo] - tEnd).toFixed(2)} s`;
    this.tip.append(head);
    for (const [a, b] of rows) {
      const s1 = document.createElement('span'), s2 = document.createElement('span');
      s1.textContent = a; s2.textContent = b + (m.unit ? ' ' + m.unit : '');
      this.tip.append(s1, s2);
    }
    this.tip.hidden = false;
    const tw = this.tip.offsetWidth;
    this.tip.style.left = `${xi + 14 + tw > w ? xi - 14 - tw : xi + 14}px`;
  }
}

/**
 * Scatter / curves / bars. draw({ xRange, yRange, xUnit, yUnit, points: [{ xs, ys, color, step? }],
 * curves: [{ fn, color, dash?, width? }], bars: [{ x, y, err?, color, width }], diagonal?, zero? }).
 */
export class XYChart extends Base {
  draw(m) {
    const { ctx, w, h } = this.begin();
    const F = this.frame(ctx, w, h, m.xRange, m.yRange, { xUnit: m.xUnit, yUnit: m.yUnit });
    ctx.save();
    ctx.beginPath();
    ctx.rect(F.pad.l, F.pad.t, w - F.pad.l - F.pad.r, h - F.pad.t - F.pad.b);
    ctx.clip();
    if (m.zero) {
      ctx.strokeStyle = cssColor('--line-strong');
      ctx.beginPath(); ctx.moveTo(F.pad.l, Math.round(F.Y(0)) + 0.5); ctx.lineTo(w, Math.round(F.Y(0)) + 0.5); ctx.stroke();
    }
    if (m.diagonal) {
      ctx.strokeStyle = cssColor('--line-strong');
      ctx.setLineDash([5, 4]);
      ctx.beginPath(); ctx.moveTo(F.X(m.xRange[0]), F.Y(m.xRange[0])); ctx.lineTo(F.X(m.xRange[1]), F.Y(m.xRange[1])); ctx.stroke();
      ctx.setLineDash([]);
    }
    for (const P of m.points || []) {
      ctx.fillStyle = cssColor(P.color);
      ctx.globalAlpha = P.alpha ?? 0.28;
      const step = Math.max(1, Math.floor(P.xs.length / 3000));
      for (let i = 0; i < P.xs.length; i += step) {
        const x = P.xs[i], y = P.ys[i];
        if (Number.isFinite(x) && Number.isFinite(y)) ctx.fillRect(F.X(x) - 1.25, F.Y(y) - 1.25, 2.5, 2.5);
      }
      ctx.globalAlpha = 1;
    }
    for (const C of m.curves || []) {
      ctx.strokeStyle = cssColor(C.color);
      ctx.lineWidth = C.width || 2.25;
      ctx.setLineDash(C.dash ? [6, 5] : []);
      ctx.beginPath();
      let pen = false;
      for (let k = 0; k <= 240; k++) {
        const x = m.xRange[0] + ((m.xRange[1] - m.xRange[0]) * k) / 240, y = C.fn(x);
        if (!Number.isFinite(y)) { pen = false; continue; }
        if (pen) ctx.lineTo(F.X(x), F.Y(y)); else { ctx.moveTo(F.X(x), F.Y(y)); pen = true; }
      }
      ctx.stroke();
      ctx.setLineDash([]);
    }
    for (const B of m.bars || []) {
      const bw = Math.abs(F.X(B.width) - F.X(0)), x = F.X(B.x), y0 = F.Y(0), y1 = F.Y(B.y);
      ctx.fillStyle = cssColor(B.color);
      ctx.fillRect(x - bw / 2, Math.min(y0, y1), bw, Math.max(1.5, Math.abs(y1 - y0)));
      if (Number.isFinite(B.err)) {
        ctx.strokeStyle = cssColor('--ink-2');
        ctx.lineWidth = 1.25;
        ctx.beginPath();
        ctx.moveTo(x, F.Y(B.y - B.err)); ctx.lineTo(x, F.Y(B.y + B.err));
        ctx.moveTo(x - 4, F.Y(B.y - B.err)); ctx.lineTo(x + 4, F.Y(B.y - B.err));
        ctx.moveTo(x - 4, F.Y(B.y + B.err)); ctx.lineTo(x + 4, F.Y(B.y + B.err));
        ctx.stroke();
      }
    }
    ctx.restore();
    this.tip.hidden = true;
  }
}
