// The board's calibration and output response, in the Connection popover (pages/connection.js).
//
// Calibration: the profiles from /api/calibrations (calib/tunnel_fit.py): the built-in ones (the 4 Nov tunnel
// calibration, lag-matched 0.75 Hz and fast 2 Hz, and the uncalibrated theory fallback), then each saved
// `calib tunnel-fit` profile. "Use on board" sets the profile's values on the PC and pushes them; the tare and the
// thermometers are left alone. A reset puts the board back on its flashed default.
//
// Output response: the Butterworth cutoff of the board's smoother, set on the board at once. "Lag-matched" (the
// default) is ~0.3 s behind the true speed, under the VDAS's 0.40–0.48 s lag that the logger scores against; "Fast"
// is the true speed, ~0.1 s behind. docs/calibration-2026-11-04.md has the scores.
import { h, fill } from './dom.js';
import { fixed, isNum } from './format.js';
import { button, numberInput, toast } from './ui.js';

const RESPONSES = [
  { hz: 0.75, label: 'Lag-matched 0.75 Hz', title: 'Default. About 0.3 s behind the true speed, so it lines up with the tunnel VDAS, which lags 0.4–0.5 s. Best logger score.' },
  { hz: 2, label: 'Fast 2 Hz', title: 'The true speed, about 0.1 s behind. Still removes the plate\'s 15.7 Hz ringing. Reads ahead of the VDAS on speed changes.' },
];

export function profileMatches(p, values) {
  const ema = Number(p.params.filter_kind) >= 0.5;
  return Object.entries(p.params).every(([k, v]) => {
    if ((k === 'ema_alpha' && !ema) || (k === 'lpf_hz' && ema)) return true;
    const b = values[k];
    return isNum(b) && Math.abs(b - v) <= 1e-4 * Math.max(1, Math.abs(v));
  });
}

/** The Calibration field: pick a profile, push it. Call load() to (re)read the list, sync() on every update. */
export function calibrationField(store, actions) {
  let profiles = [];
  const pick = h('select.select', { 'aria-label': 'Calibration profile', style: { flex: '1', minWidth: '0' } });
  const status = h('span.help');
  const use = button('Use on board', () => {
    const p = profiles.find((x) => x.name === pick.value);
    if (!p) return;
    actions.setParams(p.params);
    actions.push();
    toast(`${p.label}: sent to the board.`);
  }, { cls: '.sm' });
  const el = h('div.field', h('label', 'Calibration'), h('div.row', pick, use), status);
  const load = async () => {
    try {
      profiles = (await (await fetch('/api/calibrations')).json()).profiles;
      const keep = pick.value;
      fill(pick, profiles.map((p) => h('option', { value: p.name, title: p.label },
        isNum(p.mse) ? `${p.label} · MSE ${fixed(p.mse, 3)}` : p.label)));
      const on = profiles.find((p) => profileMatches(p, store.board));
      pick.value = keep && profiles.some((p) => p.name === keep) ? keep : on ? on.name : profiles[0]?.name;
      use.disabled = false;
    } catch {
      fill(pick, h('option', 'Profiles unavailable: is the server running?'));
      use.disabled = true;
    }
  };
  const sync = () => {
    const onBoard = profiles.find((p) => profileMatches(p, store.board));
    const onPc = profiles.find((p) => profileMatches(p, store.params));
    status.textContent = !profiles.length ? ''
      : !isNum(store.board.sqrt_k) ? 'Board: no GET reply yet'
        : onBoard ? `Board: ${onBoard.label}`
          : onPc ? `PC: ${onPc.label}, not pushed` : 'Board: custom settings';
  };
  return { el, load, sync };
}

/** The Output response field: two presets and any cutoff, each set on the board at once. */
export function responseField(store, actions) {
  const setResponse = (hz) => {
    if (!(hz > 0 && hz < 100)) return toast('Type a cutoff between 0.1 and 99 Hz.', 'warn');
    actions.setOnBoard('filter_kind', 0);
    actions.setOnBoard('lpf_hz', hz);
    toast(`Output response: Butterworth ${hz} Hz, sent to the board.`);
  };
  const seg = h('div.segmented', { role: 'group', 'aria-label': 'Output response' },
    RESPONSES.map((r) => h('button', { type: 'button', title: r.title, 'aria-pressed': 'false', onclick: () => setResponse(r.hz) }, r.label)));
  const custom = numberInput({ step: 0.05, min: 0.1, max: 99, width: '5.5rem', label: 'Cutoff in Hz', onEnter: (el) => setResponse(Number(el.value)) });
  const status = h('span.help');
  const el = h('div.field', h('label', 'Output response'), seg,
    h('div.row', custom, h('span.faint', 'Hz'), button('Set', () => setResponse(Number(custom.value)), { cls: '.quiet.sm' })), status);
  const sync = () => {
    const b = store.board, ema = isNum(b.filter_kind) && b.filter_kind >= 0.5;
    [...seg.children].forEach((btn, i) => btn.setAttribute('aria-pressed', String(!ema && isNum(b.lpf_hz) && Math.abs(b.lpf_hz - RESPONSES[i].hz) < 1e-3)));
    status.textContent = !isNum(b.filter_kind) ? 'Board: no GET reply yet'
      : ema ? `Board: EMA α ${fixed(b.ema_alpha, 3)}` : `Board: Butterworth ${fixed(b.lpf_hz, 2)} Hz`;
    if (document.activeElement !== custom && isNum(b.lpf_hz) && !custom.value) custom.value = String(b.lpf_hz);
  };
  return { el, sync };
}
