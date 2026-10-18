// Airspeed: the whole drag-plate chain. The board computes
// v = √(2·(F − F₀) / (ρ·Cd·A)) and then median-3 → lead → EMA | Butterworth (calibration/docs/SPEC.md §3).
// The server runs the same chain on the PC as a preview with unpushed settings, fits it and diagnoses it.
// Rig-agnostic: on the open bench (no fan, no pitot) the chain is checked with a known force on the plate;
// in the tunnel the pitot adds the sweep, the fits and the score. Tunnel steps skip themselves without it.
import { h } from '../core/dom.js';
import { fixed, signed, smart, isNum } from '../core/format.js';
import { asyncButton, button, numberInput, table, kv } from '../core/ui.js';

const BANDS = [2, 3, 4, 5, 6, 7, 8, 9, 10];   // 1 m/s bands from 2 to 11 m/s
const BAND_FULL_S = 3;
const CURVE_WINDOW_S = 120;                   // the server keeps 120 s, enough for one sweep
const RESPONSE_WINDOW_S = 60;
const MSE_GOOD = 0.05, MSE_OK = 0.2;
const CD_FLAT_PLATE = [1.1, 1.3];

const FORCE_TOL = 0.01, SPEED_TOL = 0.05;   // known-force check: 1 % on force, 0.05 m/s (or 1 %) on speed
const SUGGESTED_G = [10, 20, 50];             // ≈ 3.7, 5.3 and 8.3 m/s on a 118 cm² plate

const S = { f0: null, force: null, curve: null, response: null, plateCm2: loadPlate() };

function loadPlate() {
  try { return Number(localStorage.getItem('calib.plateAreaCm2')) || 100; } catch { return 100; }
}
function savePlate(v) {
  try { localStorage.setItem('calib.plateAreaCm2', String(v)); } catch { /* storage blocked: keep in memory */ }
}

const cdaCm2 = (k) => 2e4 / k;     // firmware stores k = 2/(Cd·A) in 1/m²
const isEma = (p) => p.filter_kind >= 0.5;
const smoother = (p) => (isEma(p) ? 'ema_alpha' : 'lpf_hz');
const pitot = (s) => s.buf.stats('gt', 0.5).mean;
const tunnel = (s) => s.hasPitot;
const fanOn = (s) => tunnel(s) && Math.abs(s.buf.last('gt')) > 0.3;
const boardParams = (s) => ({ ...s.params, ...Object.fromEntries(Object.entries(s.board).filter(([, v]) => isNum(v))) });
const rhoNow = (s) => (s.buf.last('rho') > 0.5 ? s.buf.last('rho') : 1.2);
const NEEDS_TUNNEL = { tone: 'skip', text: 'Needs airflow and the pitot' };
const tunnelNote = () => h('p.faint', 'Runs in the tunnel. Connect the pitot in Connection (top right) to enable it; the bench steps do not need it.');
const boardScore = (s) => s.metrics?.dev?.mse;
const previewScore = (s) => s.metrics?.est?.mse;
const hasForce = (s) => isNum(s.buf.last('feature'));
const speedOf = (p, f, rho) => { const a = (p.sqrt_k * (f - p.sqrt_x0)) / rho; return a > 0 ? Math.sqrt(a) : 0; };
const NAMES = { sqrt_x0: ['F₀', 'N'], ema_alpha: ['α', ''], lpf_hz: ['cutoff', 'Hz'], lead_tau: ['lag compensation', 's'] };
const applyLabel = (k, v) => (k === 'sqrt_k' ? `Cd·A → ${fixed(cdaCm2(v), 1)} cm²` : `${NAMES[k]?.[0] || k} → ${smart(v, 4)}${NAMES[k]?.[1] ? ' ' + NAMES[k][1] : ''}`);
const tone = (mse) => (!isNum(mse) ? null : mse < MSE_GOOD ? 'ok' : mse < MSE_OK ? 'warn' : 'bad');

function coverage(s) {
  const b = s.buf, gt = b.col('gt'), i0 = b.since(CURVE_WINDOW_S), secs = BANDS.map(() => 0);
  for (let i = i0; i < b.n; i++) {
    const k = Math.floor(gt[i]) - BANDS[0];
    if (k >= 0 && k < BANDS.length) secs[k] += 1 / 200;
  }
  return secs;
}
const bandsCovered = (s) => coverage(s).filter((x) => x >= BAND_FULL_S).length;

function streamProblem(s) {
  if (s.conn !== 'open') return { title: 'Calibration server not reachable', detail: 'Start it with `python -m calib serve` in calibration/, then reload this page.' };
  if (!s.streaming) return { title: 'No readings from the board', detail: 'Open Connection (top right) and connect the Nucleo, or start the simulator.' };
  if (!hasForce(s)) return { title: 'The stream has no force column', detail: 'Send MODE D; if that does not help, flash the airspeed_drag_plate build.', modeD: true };
  return null;
}

// ------------------------------------------------------------------------------------------- views
function drawLive(chart, s, hidden) {
  const b = s.buf, windowS = 20;
  const lines = [
    { key: 'gt', label: 'Pitot', color: '--s-truth', data: b.col('gt'), width: 2 },
    { key: 'dev', label: 'Sensor', color: '--s-sensor', data: b.col('dev'), width: 2 },
    { key: 'est', label: 'Preview', color: '--s-preview', data: b.col('est'), dash: true, width: 1.75 },
  ].filter((l) => !hidden.has(l.key));
  chart.draw({ t: b.col('t'), i0: b.since(windowS), window: windowS, unit: 'm/s', yMin: 0, yMax: 4, lines });
}

function drawCurve(chart, s, hidden) {
  const b = s.buf, i0 = b.since(CURVE_WINDOW_S);
  const f = b.col('feature').subarray(i0), gt = b.col('gt').subarray(i0);
  let lo = Infinity, hi = -Infinity;
  for (const v of f) if (Number.isFinite(v)) { if (v < lo) lo = v; if (v > hi) hi = v; }
  if (lo === Infinity) return chart.empty('No force readings yet');
  if (!tunnel(s)) return chart.empty('Needs the pitot: the curve is fitted in the tunnel');
  const span = hi - lo || 0.01, rho = b.last('rho') > 0.5 ? b.last('rho') : 1.2;
  const curves = [];
  if (!hidden.has('board') && isNum(s.board.sqrt_k)) curves.push({ fn: (x) => speedOf({ ...s.params, ...s.board }, x, rho), color: '--s-sensor', dash: true });
  if (!hidden.has('pc')) curves.push({ fn: (x) => speedOf(s.params, x, rho), color: '--s-preview' });
  chart.draw({
    xRange: [lo - span * 0.05, hi + span * 0.05], yRange: [0, 12], xUnit: 'force N', yUnit: 'pitot m/s',
    points: hidden.has('pts') ? [] : [{ xs: f, ys: gt, color: '--s-truth', alpha: 0.22 }], curves,
  });
}

function drawAccuracy(chart, s, hidden) {
  if (!tunnel(s)) return chart.empty('Needs the pitot');
  const b = s.buf, i0 = b.since(60), gt = b.col('gt').subarray(i0);
  const points = [];
  if (!hidden.has('dev')) points.push({ xs: gt, ys: b.col('dev').subarray(i0), color: '--s-sensor', alpha: 0.3 });
  if (!hidden.has('est')) points.push({ xs: gt, ys: b.col('est').subarray(i0), color: '--s-preview', alpha: 0.22 });
  chart.draw({ xRange: [0, 12], yRange: [0, 12], xUnit: 'pitot m/s', yUnit: 'sensor m/s', diagonal: true, points });
}

function drawBands(chart, s, hidden) {
  const b = s.buf, i0 = b.since(60), gt = b.col('gt'), series = [['dev', '--s-sensor', -0.2], ['est', '--s-preview', 0.2]].filter(([k]) => !hidden.has(k));
  const bars = [];
  let m = 0.2;
  for (const [key, color, off] of series) {
    const est = b.col(key);
    for (let k = 1; k < 12; k++) {
      let n = 0, sum = 0, sum2 = 0;
      for (let i = i0; i < b.n; i++) {
        if (Math.floor(gt[i]) !== k || !Number.isFinite(est[i])) continue;
        const d = est[i] - gt[i];
        n++; sum += d; sum2 += d * d;
      }
      if (n < 40) continue;
      const bias = sum / n, sd = Math.sqrt(Math.max(0, sum2 / n - bias * bias));
      m = Math.max(m, Math.abs(bias) + sd);
      bars.push({ x: k + 0.5 + off, y: bias, err: sd, color, width: 0.34 });
    }
  }
  if (!bars.length) return chart.empty(tunnel(s) ? 'Needs at least 0.2 s at each speed in the last minute' : 'Needs the pitot');
  m = Math.min(4, m * 1.15);
  chart.draw({ xRange: [1, 12], yRange: [-m, m], xUnit: 'pitot m/s', yUnit: 'error m/s (reads high ↑)', zero: true, bars });
}

// ------------------------------------------------------------------------------------------ steps
function fitSummary(r, rows) {
  const better = r.after?.mse < r.before?.mse;
  return h(`div.result${better ? '.ok' : '.warn'}`,
    h('strong', better ? `MSE ${fixed(r.before.mse, 4)} → ${fixed(r.after.mse, 4)} m²/s²` : `No improvement (MSE ${fixed(r.before?.mse, 4)} → ${fixed(r.after?.mse, 4)})`),
    kv(rows));
}

const zeroStep = {
  id: 'zero',
  title: 'Measure the zero-flow force F₀',
  why: 'With no airflow the plate still reports a small force (preload in the mount, a slightly old tare). Subtracting it keeps the low speeds right, where a few millinewtons matter most.',
  key: (s) => `${s.isSim}|${!!S.f0}|${tunnel(s)}|${fanOn(s)}`,
  state(s) {
    if (!s.streaming || !hasForce(s)) return { tone: 'blocked', text: 'No readings' };
    if (S.f0) return { tone: 'done', text: `F₀ ${signed(S.f0.value, 4)} N` };
    if (fanOn(s)) return { tone: 'blocked', text: 'Fan is on' };
    return { tone: 'todo', text: '' };
  },
  body(ctx) {
    const s = ctx.store;
    return h('div.stack',
      h('p.num', { dataset: { live: '' } }),
      h('div.row',
        s.isSim && fanOn(s) && button('Stop the simulated fan', () => ctx.actions.sim({ fan: 'off' })),
        asyncButton('Measure F₀ (3 s)', async () => {
          const m = await ctx.actions.measure('feature', 3);
          if (tunnel(s) && Math.abs(m.gt) > 0.3) ctx.toast(`The pitot reads ${fixed(m.gt, 2)} m/s: F₀ includes some drag. Stop the fan and measure again.`, 'warn', 6000);
          ctx.actions.setParam('sqrt_x0', Number(m.mean.toPrecision(5)));
          S.f0 = { value: m.mean, sd: m.sd };
          ctx.toast(`F₀ = ${signed(m.mean, 4)} N set on the PC. Push it in the last step.`);
          ctx.refresh();
        }, { primary: true, busy: 'Measuring…' })),
      h('p.faint', tunnel(s) ? 'Stop the fan and wait until the pitot reads about 0 m/s.' : 'No pitot connected: make sure nothing is blowing on the plate.'));
  },
  update(body, s) {
    const el = body.querySelector('[data-live]');
    if (el) el.textContent = `${tunnel(s) ? `Pitot ${fixed(pitot(s), 2)} m/s · ` : ''}force ${signed(s.buf.stats('feature', 0.5).mean, 4)} N`;
  },
};

const forceStep = {
  id: 'force',
  title: 'Check with a known force',
  why: 'Pull the plate along the drag direction with a known weight (a thread over a pulley works). The board should read that force, and turn it into the speed the formula gives. This checks the load cell, the conversion and the filters without any airflow.',
  key: (s) => `${s.isSim}|${S.force?.at || ''}|${fanOn(s)}`,
  state(s) {
    if (!s.streaming || !hasForce(s)) return { tone: 'blocked', text: 'No readings' };
    if (S.force) return S.force.ok ? { tone: 'done', text: `${signed(S.force.forceErr * 100, 1)} % at ${S.force.massG} g` } : { tone: 'blocked', text: `Off by ${signed(S.force.forceErr * 100, 1)} %` };
    if (fanOn(s)) return { tone: 'blocked', text: 'Fan is on' };
    return { tone: 'todo', text: '' };
  },
  body(ctx) {
    const s = ctx.store;
    const mass = numberInput({ value: S.force?.massG ?? SUGGESTED_G[1], min: 1, max: 200, label: 'Mass in grams', width: '100px' });
    const r = S.force;
    let out = null;
    if (r) {
      out = h(`div.result${r.ok ? '.ok' : '.warn'}`,
        h('strong', r.ok ? 'The chain reads the force and its speed correctly' : 'Outside tolerance'),
        kv([
          ['Force', `${fixed(r.readN * 1000, 1)} mN for ${fixed(r.trueN * 1000, 1)} mN (${signed(r.forceErr * 100, 2)} %)`],
          ['Speed', `board ${fixed(r.boardV, 2)} m/s, formula ${fixed(r.expectV, 2)} m/s (${signed(r.boardV - r.expectV, 3)})`],
        ]),
        !r.ok && h('p', Math.abs(r.forceErr) >= FORCE_TOL
          ? 'The force is off: scale the load cell (Load cell tab), then measure F₀ again.'
          : 'The force is right but the speed is not: push the PC settings to the board, or check the stream is in MODE D.'));
    }
    return h('div.stack',
      h('div.row.end',
        h('div.field', h('label', 'Mass (g)'), mass),
        asyncButton('Measure (3 s)', async () => {
          const g = Number(mass.value);
          if (!(g > 0 && g <= 200)) throw new Error('Enter a mass between 1 and 200 g.');
          if (s.isSim) { ctx.actions.sim({ load_g: g }); await new Promise((res) => setTimeout(res, 1500)); }
          try {
            const m = await ctx.actions.measure('feature', 3);
            // force against the F₀ just measured (PC); speed against what the board itself runs
            const f0 = S.f0?.value ?? (isNum(s.params.sqrt_x0) ? s.params.sqrt_x0 : 0);
            const trueN = (g / 1000) * 9.80665, readN = m.mean - f0;
            const expectV = speedOf(boardParams(s), m.mean, rhoNow(s)), boardV = s.buf.stats('dev', 3).mean;
            const forceErr = (readN - trueN) / trueN;
            const ok = Math.abs(forceErr) < FORCE_TOL && Math.abs(boardV - expectV) < Math.max(SPEED_TOL, 0.01 * expectV);
            S.force = { massG: g, trueN, readN, forceErr, expectV, boardV, ok, at: Date.now() };
            ctx.toast(ok ? 'Known-force check passed.' : 'Known-force check is outside tolerance.', ok ? 'ok' : 'warn');
          } finally {
            if (s.isSim) ctx.actions.sim({ load_g: 0 });
          }
          ctx.refresh();
        }, { primary: true, busy: 'Measuring…' })),
      h('p.faint', `Try ${SUGGESTED_G.join(', ')} g; on this plate they are about 3.7, 5.3 and 8.3 m/s.`),
      out);
  },
};

const sweepStep = {
  id: 'sweep',
  title: 'Sweep the fan from 2 to 10 m/s',
  why: `Hold each speed for a few seconds so every 1 m/s band has at least ${BAND_FULL_S} s of data. The curve fit uses the last ${CURVE_WINDOW_S / 60} minutes, so sweep just before fitting.`,
  key: (s) => `${s.isSim}|${tunnel(s)}`,
  state(s) {
    if (!tunnel(s)) return NEEDS_TUNNEL;
    const c = bandsCovered(s);
    return c >= BANDS.length - 1 ? { tone: 'done', text: `${c} of ${BANDS.length} speeds` } : { tone: 'todo', text: c ? `${c} of ${BANDS.length} speeds` : '' };
  },
  body(ctx) {
    if (!tunnel(ctx.store)) return tunnelNote();
    return h('div.stack',
      h('div.bands', { dataset: { bands: '' }, 'aria-label': 'Seconds of data per speed band' }, BANDS.map((v) => h('span', { title: `${v}–${v + 1} m/s` }, v))),
      ctx.store.isSim
        ? h('div.row', button('Run the simulated fan through steps', () => ctx.actions.sim({ fan: 'on', profile: 'steps' })))
        : h('p.faint', 'Green: enough data. Amber: some. Step the fan through the range and hold each speed.'));
  },
  update(body, s) {
    const cells = body.querySelector('[data-bands]')?.children;
    if (!cells) return;
    coverage(s).forEach((sec, i) => {
      cells[i].className = sec >= BAND_FULL_S ? 'full' : sec > 0.5 ? 'part' : '';
      cells[i].title = `${BANDS[i]}–${BANDS[i] + 1} m/s: ${sec.toFixed(1)} s`;
    });
  },
};

const curveStep = {
  id: 'curve',
  title: 'Fit the calibration curve',
  why: 'Finds the drag area Cd·A and F₀ that make the formula match the pitot across the sweep. A robust fit ignores the odd spike from the pitot.',
  key: (s) => `${S.curve?.at || ''}|${tunnel(s)}`,
  state(s) {
    if (S.curve) return S.curve.ok ? { tone: 'done', text: `Cd·A ${fixed(cdaCm2(S.curve.params.sqrt_k), 1)} cm²` } : { tone: 'blocked', text: 'Fit failed' };
    if (!tunnel(s)) return NEEDS_TUNNEL;
    if (bandsCovered(s) < 6) return { tone: 'blocked', text: 'Sweep first' };
    return { tone: 'todo', text: '' };
  },
  body(ctx) {
    if (!tunnel(ctx.store) && !S.curve) return tunnelNote();
    const area = numberInput({ value: S.plateCm2, min: 1, label: 'Plate area in cm²', width: '100px' });
    area.addEventListener('change', () => { S.plateCm2 = Number(area.value) || S.plateCm2; savePlate(S.plateCm2); ctx.refresh(); });
    const r = S.curve;
    let out = null;
    if (r?.ok) {
      const cda = cdaCm2(r.params.sqrt_k), cd = cda / S.plateCm2;
      const cdOk = cd >= CD_FLAT_PLATE[0] && cd <= CD_FLAT_PLATE[1];
      out = fitSummary(r, [
        ['Drag area Cd·A', `${fixed(cda, 1)} cm²  (was ${fixed(cdaCm2(r.previous.sqrt_k), 1)})`],
        ['Zero-flow force F₀', `${signed(r.params.sqrt_x0, 4)} N`],
        ['Implied Cd', `${fixed(cd, 2)} ${cdOk ? '(a flat plate is 1.1–1.3)' : '(outside 1.1–1.3: check the plate area)'}`],
      ]);
      out.append(h('div.row', button('Undo fit', () => {
        ctx.actions.setParams({ sqrt_k: r.previous.sqrt_k, sqrt_x0: r.previous.sqrt_x0 });
        S.curve = null;
        ctx.refresh();
      }, { cls: '.quiet' })));
    } else if (r) {
      out = h('div.result.warn', h('strong', 'Fit failed'), h('p', r.message || 'Not enough steady data.'));
    }
    return h('div.stack',
      h('div.row.end',
        h('div.field', h('label', 'Plate area (cm²)'), area),
        asyncButton('Fit curve', async () => {
          const res = await ctx.actions.fit('robust_lsq', ['sqrt_k', 'sqrt_x0'], CURVE_WINDOW_S);
          S.curve = { ...res, at: Date.now() };
          ctx.toast(res.ok ? 'Curve fitted. The orange preview now uses it.' : `Fit failed: ${res.message}`, res.ok ? 'ok' : 'bad');
          ctx.refresh();
        }, { primary: true, busy: 'Fitting…' })),
      out);
  },
};

const responseStep = {
  id: 'response',
  title: 'Tune smoothing and lag',
  why: 'The score is MSE against the pitot, so noise at steady speed and lag on speed changes both cost points. Choose a filter, then fit its setting together with the lag compensation while the fan steps.',
  key: (s) => `${S.response?.at || ''}|${isEma(s.params)}|${tunnel(s)}`,
  state(s) {
    if (!S.response) return tunnel(s) ? { tone: 'todo', text: '' } : NEEDS_TUNNEL;
    if (!S.response.ok) return { tone: 'blocked', text: 'Fit failed' };
    const p = S.response.params;
    return { tone: 'done', text: 'ema_alpha' in p ? `α ${fixed(p.ema_alpha, 3)}, lead ${fixed(p.lead_tau * 1000, 0)} ms` : `${fixed(p.lpf_hz, 1)} Hz, lead ${fixed(p.lead_tau * 1000, 0)} ms` };
  },
  body(ctx) {
    const s = ctx.store, ema = isEma(s.params);
    if (!tunnel(s) && !S.response) return tunnelNote();
    const seg = h('div.segmented', { role: 'group', 'aria-label': 'Smoothing filter' },
      [[1, 'EMA'], [0, 'Butterworth']].map(([v, l]) => h('button', { type: 'button', 'aria-pressed': String((ema ? 1 : 0) === v), onclick: () => { ctx.actions.setParam('filter_kind', v); setTimeout(ctx.refresh, 150); } }, l)));
    const r = S.response;
    let out = null;
    if (r?.ok) {
      const key = 'ema_alpha' in r.params ? 'ema_alpha' : 'lpf_hz';
      out = fitSummary(r, [
        [key === 'ema_alpha' ? 'Smoothing α' : 'Cutoff', key === 'ema_alpha' ? fixed(r.params.ema_alpha, 4) : `${fixed(r.params.lpf_hz, 2)} Hz`],
        ['Lag compensation', `${fixed(r.params.lead_tau * 1000, 1)} ms`],
        isNum(r.after?.lag_ms) && ['Lag after', `${fixed(r.after.lag_ms, 0)} ms`],
      ]);
      out.append(h('div.row', button('Undo fit', () => {
        ctx.actions.setParams({ [key]: r.previous[key], lead_tau: r.previous.lead_tau });
        S.response = null;
        ctx.refresh();
      }, { cls: '.quiet' })));
    } else if (r) {
      out = h('div.result.warn', h('strong', 'Fit failed'), h('p', r.message || 'Not enough speed changes in the window.'));
    }
    return h('div.stack',
      h('div.row', h('span.label', 'Filter'), seg),
      h('p.faint', ema ? 'EMA: one weight α. Simple and fast.' : 'Butterworth: 2nd order, damps the plate\'s ringing better for the same lag.'),
      h('div.row',
        s.isSim && button('Run the simulated fan through steps', () => ctx.actions.sim({ fan: 'on', profile: 'steps' })),
        asyncButton('Fit response', async () => {
          const res = await ctx.actions.fit('dynamics', [smoother(s.params), 'lead_tau'], RESPONSE_WINDOW_S);
          S.response = { ...res, at: Date.now() };
          ctx.toast(res.ok ? 'Response fitted.' : `Fit failed: ${res.message}`, res.ok ? 'ok' : 'bad');
          ctx.refresh();
        }, { primary: true, busy: 'Fitting…' })),
      out);
  },
};

const pushStep = {
  id: 'push',
  title: 'Send to the board and verify',
  why: 'Sends every changed setting with SET, reads them back with GET, then compares the board\'s own output with the PC preview. Give it one scoring window (30 s) after a push.',
  key: (s) => `${s.dirtyParams().join(',')}|${tunnel(s)}`,
  state(s) {
    const d = s.dirtyParams().length;
    if (d) return { tone: 'todo', text: `${d} change${d > 1 ? 's' : ''} not on the board` };
    if (!tunnel(s)) return { tone: 'done', text: 'Board matches the PC' };
    const b = boardScore(s), p = previewScore(s);
    if (!isNum(b)) return { tone: 'todo', text: 'Waiting for a score' };
    if (isNum(p) && Math.abs(b - p) > 0.15 * p + 0.002) return { tone: 'blocked', text: `Board ${fixed(b, 4)} vs preview ${fixed(p, 4)}` };
    return { tone: 'done', text: `Board MSE ${fixed(b, 4)}` };
  },
  body(ctx) {
    const s = ctx.store, dirty = s.dirtyParams();
    return h('div.stack',
      dirty.length
        ? table(['Setting', 'PC', 'Board'], dirty.map((n) => [h('span.mono', n), smart(s.params[n], 5), smart(s.board[n], 5)]))
        : h('p.faint', 'The board runs the same settings as the PC.'),
      !tunnel(s) && h('p.faint', 'No pitot: settings are confirmed by reading them back. The score comparison runs in the tunnel.'),
      h('div.row',
        asyncButton('Push to board', async () => {
          ctx.actions.push();
          await new Promise((r) => setTimeout(r, 700));
          ctx.toast('Settings sent. Wait one scoring window before comparing.');
          ctx.refresh();
        }, { primary: true, busy: 'Sending…' }),
        button('Export calibration…', () => { location.hash = '#runs'; })));
  },
};

// ----------------------------------------------------------------------------------------- module
const LEGEND_LIVE = [
  { key: 'gt', label: 'Pitot', color: '--s-truth' },
  { key: 'dev', label: 'Sensor (board)', color: '--s-sensor' },
  { key: 'est', label: 'Preview (PC settings)', color: '--s-preview', dash: true },
];

export default {
  id: 'airspeed',
  title: 'Airspeed',
  part: 'Drag plate on the load cell · known forces on the bench, the pitot in the tunnel · 200 Hz',
  intro: 'Turns plate force into airspeed. On the bench: measure F₀ and check a known force. In the tunnel: also sweep, fit the curve and the response, then push. The orange preview shows unpushed settings live.',
  allClear: 'No problems found. In the tunnel this includes the scoring window against the pitot.',

  status(s) {
    if (s.conn !== 'open') return { tone: 'idle', text: 'Offline' };
    const p = streamProblem(s);
    if (p) return { tone: 'bad', text: 'No readings' };
    if (!tunnel(s)) return S.force ? { tone: S.force.ok ? 'ok' : 'warn', text: S.force.ok ? 'Bench check passed' : 'Bench check off' } : { tone: 'idle', text: 'Bench, no pitot' };
    const mse = boardScore(s);
    if (!isNum(mse)) return { tone: 'idle', text: 'Scoring…' };
    return { tone: tone(mse) === 'ok' ? 'ok' : tone(mse) === 'warn' ? 'warn' : 'bad', text: `MSE ${fixed(mse, 3)}` };
  },

  readouts: [
    { label: 'Sensor', unit: 'm/s', digits: 2, value: (s) => s.buf.stats('dev', 0.5).mean, note: () => 'board output' },
    {
      label: 'Plate force', unit: 'mN', digits: 1, value: (s) => (s.buf.stats('feature', 0.5).mean - (s.params.sqrt_x0 || 0)) * 1000,
      note: () => 'after F₀, last 0.5 s',
    },
    { label: 'Pitot', unit: 'm/s', digits: 2, value: (s) => (tunnel(s) ? pitot(s) : NaN), note: (s) => (tunnel(s) ? 'ground truth' : 'not connected') },
    {
      label: 'Error', unit: 'm/s', value: (s) => (tunnel(s) ? s.buf.stats('dev', 0.5).mean - pitot(s) : NaN), format: (v) => (isNum(v) ? signed(v, 2) : '–'),
      note: (s) => (tunnel(s) ? 'sensor − pitot, last 0.5 s' : 'needs the pitot'),
    },
    {
      label: 'Score (MSE, 30 s)', unit: 'm²/s²', digits: 4, value: (s) => (tunnel(s) ? boardScore(s) : NaN), tone: (s) => (tunnel(s) ? tone(boardScore(s)) : null),
      note: (s) => {
        const m = s.metrics?.dev;
        if (!m) return tunnel(s) ? '' : 'needs the pitot';
        const base = `bias ${signed(m.bias, 3)} · σ ${fixed(m.sigma, 3)} · lag ${fixed(m.lag_ms, 0)} ms`;
        return s.dirtyParams().length && isNum(previewScore(s)) ? `${base} · preview ${fixed(previewScore(s), 4)}` : base;
      },
    },
  ],

  views: [
    { id: 'live', label: 'Live', title: 'Airspeed', sub: 'Last 20 s', legend: LEGEND_LIVE, draw: drawLive },
    {
      id: 'curve', label: 'Curve', title: 'Calibration curve', sub: 'Force against pitot speed, last 2 min. The line should run through the dots.', tall: true, kind: 'xy',
      legend: [{ key: 'pts', label: 'Samples', color: '--s-truth' }, { key: 'pc', label: 'PC curve', color: '--s-preview' }, { key: 'board', label: 'Board curve', color: '--s-sensor', dash: true }],
      draw: drawCurve,
    },
    {
      id: 'accuracy', label: 'Accuracy', title: 'Sensor against pitot', sub: 'Last 60 s. On the diagonal is perfect; loops are lag on speed changes.', tall: true, kind: 'xy',
      legend: [{ key: 'dev', label: 'Board', color: '--s-sensor' }, { key: 'est', label: 'Preview', color: '--s-preview' }],
      draw: drawAccuracy,
    },
    {
      id: 'bands', label: 'By speed', title: 'Error by speed', sub: 'Mean error per 1 m/s band, last 60 s. Whiskers: ±1σ.', kind: 'xy',
      legend: [{ key: 'dev', label: 'Board', color: '--s-sensor' }, { key: 'est', label: 'Preview', color: '--s-preview' }],
      draw: drawBands,
    },
  ],

  steps: [zeroStep, forceStep, sweepStep, curveStep, responseStep, pushStep],

  settings: {
    title: 'Settings',
    sub: 'Edits change the orange preview at once and reach the board when you push.',
    fields: [
      { name: 'sqrt_k', label: 'Drag area Cd·A', help: 'Larger reads lower. Fitted in the tunnel (step 4).', view: { unit: 'cm²', min: 20, max: 400, step: 0.1, digits: 4, to: cdaCm2, from: (a) => (a > 0 ? 2e4 / a : NaN) } },
      { name: 'sqrt_x0', label: 'Zero-flow force F₀', help: 'Subtracted before the square root. Measured in step 1.', view: { unit: 'N', min: -0.05, max: 0.05, digits: 4 } },
      { name: 'filter_kind', label: 'Smoothing filter', kind: 'choice', options: [[1, 'EMA'], [0, 'Butterworth']], help: 'Butterworth damps plate ringing better for the same lag.' },
      { name: 'ema_alpha', label: 'Smoothing α', help: 'Weight of the newest sample. Lower is smoother but lags.', view: { min: 0.005, max: 1, digits: 3 }, showIf: (p) => isEma(p) },
      { name: 'lpf_hz', label: 'Cutoff', help: 'Below the plate\'s ringing frequency. Lower is smoother but lags.', view: { unit: 'Hz', min: 0.5, max: 40, digits: 3 }, showIf: (p) => !isEma(p) },
      { name: 'lead_tau', label: 'Lag compensation', help: 'Pushes the output ahead in time. Too much overshoots steps.', view: { unit: 's', min: 0, max: 0.2, digits: 3 } },
    ],
    footer(ctx) {
      const s = ctx.store;
      const auto = h('input', { type: 'checkbox', onchange: (e) => ctx.actions.autoPush(e.target.checked) });
      const push = button('Push to board', () => { ctx.actions.push(); ctx.toast('Settings sent to the board.'); }, { primary: true });
      const row = h('div.row', push,
        button('Back to board values', () => {
          const vals = Object.fromEntries(s.firmwareParams.filter((n) => n !== 'lc_offset' && isNum(s.board[n])).map((n) => [n, s.board[n]]));
          ctx.actions.setParams(vals);
        }),
        h('span.spacer'),
        h('label.check', auto, 'Push every change'));
      // keep the footer in step with the server (5 Hz from the page loop through this observer)
      const sync = () => {
        auto.checked = !!s.tele.auto_push;
        const d = s.dirtyParams().length;
        push.textContent = d ? `Push ${d} change${d > 1 ? 's' : ''} to board` : 'Push to board';
      };
      sync();
      const iv = setInterval(() => (row.isConnected ? sync() : clearInterval(iv)), 300);
      return row;
    },
  },

  checks(s, ctx) {
    const p = streamProblem(s);
    if (p) return [{ id: 'stream', level: 'bad', ...p, action: p.modeD ? { label: 'Send MODE D', run: () => ctx.actions.command('MODE D') } : null }];
    const out = [];
    if (s.tele.sensor_hz < 190) {
      out.push({ id: 'rate', level: 'warn', title: `Sensor stream at ${fixed(s.tele.sensor_hz, 0)} Hz`, detail: 'The brief requires 200 Hz. Close other programs using the port, and check the USB cable.' });
    }
    const dirty = s.dirtyParams();
    if (dirty.length) {
      out.push({ id: 'dirty', level: 'info', title: `${dirty.length} setting${dirty.length > 1 ? 's are' : ' is'} not on the board yet`, detail: `${dirty.join(', ')}. The sensor line still uses the old values.`, action: { label: 'Push to board', run: () => ctx.actions.push() } });
    }
    if (!tunnel(s)) {
      out.push({ id: 'bench', level: 'info', title: 'Bench mode: no pitot', detail: 'F₀ and the known-force check work without airflow. The sweep, fits and score appear once the pitot is connected.' });
    } else if (!s.metrics?.est) {
      out.push({ id: 'collecting', level: 'info', title: 'Collecting a score', detail: 'Recommendations appear after a few seconds of data with the pitot.' });
    }
    const LEVEL = { high: 'bad', medium: 'warn', info: 'info' };
    for (const d of s.metrics?.diagnosis || []) {
      const apply = d.apply && Object.keys(d.apply).length ? d.apply : null;
      out.push({
        id: d.id, level: LEVEL[d.severity] || 'info', title: d.title, detail: [d.why, d.action].filter(Boolean).join(' '),
        action: apply && {
          label: `Apply ${Object.entries(apply).map(([k, v]) => applyLabel(k, v)).join(', ')}`,
          run: () => { ctx.actions.setParams(apply); ctx.toast('Applied on the PC. Push to send it to the board.'); },
        },
      });
    }
    return out;
  },

  info: {
    summary: 'The board estimates speed from the plate force on its own and streams it: v = √(2·(F − F₀) / (ρ·Cd·A)), then median-3 → lag compensation → EMA or Butterworth, clamped at 0. MODE V sends one speed per line (what the course script reads); MODE D adds force, ρ, temperature and pressure for this dashboard.',
    brief: 'Estimate instantaneous airspeed from 2 to 10 m/s, including steps and ramps, entirely on the Nucleo. Stream a float at 200 Hz, 115200 baud, over the virtual COM port. Assessed 70 % on accuracy rank (MSE) and streaming without drops; the report wants MSE, bias and σ.',
    source: 'Project brief · calibration/docs/SPEC.md · firmware/src/apps/airspeed.cpp',
    parts: [
      { name: 'Nucleo-F411RE', role: 'Runs the whole estimate', connect: 'USB: power, ST-LINK, virtual COM', spec: '200 Hz tick on TIM5' },
      { name: 'Drag plate on the load cell', role: 'Transducer', connect: 'See Load cell', spec: 'F₀, Cd·A fitted here' },
      { name: 'Environment sensors', role: 'Air density ρ', connect: 'See Environment', spec: 'Updated between ticks' },
    ],
    test: [
      'Bench: measure F₀, then pull the plate with 10, 20 and 50 g; the board reads each force and its speed.',
      'Tunnel: sweep 2 → 10 m/s holding each speed, fit the curve, then fit the response on steps.',
      'Push and export: the board runs the fitted values, and the next build boots with them.',
      'Check STATS: no overruns at 200 Hz.',
    ],
    commands: [
      { label: 'Competition build', cmd: 'cd firmware && pio run -e airspeed_drag_plate -t upload' },
      { label: 'Run the dashboard on the board', cmd: 'cd calibration && python -m calib serve --port /dev/cu.usbmodem103' },
    ],
  },
};
