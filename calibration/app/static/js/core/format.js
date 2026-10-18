// Number formatting shared by every page. All return '–' for missing values.

export const DASH = '–';
export const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

export function fixed(v, digits = 2) {
  return isNum(v) ? v.toFixed(digits) : DASH;
}

export function signed(v, digits = 2) {
  if (!isNum(v)) return DASH;
  const s = v.toFixed(digits);
  return v > 0 && Number(s) !== 0 ? '+' + s : s;
}

/** Engineering-friendly: 1.234e-5 for tiny values, grouped thousands for large ones. */
export function smart(v, sig = 4) {
  if (!isNum(v)) return DASH;
  const a = Math.abs(v);
  if (a !== 0 && (a < 1e-3 || a >= 1e6)) return v.toExponential(sig - 1);
  if (a >= 1000) return Math.round(v).toLocaleString('en-GB');
  return String(Number(v.toPrecision(sig)));
}

export function percent(v, digits = 1) {
  return isNum(v) ? `${signed(v * 100, digits)} %` : DASH;
}

export function duration(s) {
  if (!isNum(s)) return DASH;
  const m = Math.floor(s / 60), r = Math.floor(s % 60);
  return `${m}:${String(r).padStart(2, '0')}`;
}

export function clock(d = new Date()) {
  return d.toTimeString().slice(0, 8);
}

export function dateTime(iso) {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}
