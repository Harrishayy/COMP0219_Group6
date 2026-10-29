// Wind speed: the whole drag-plate chain. The board computes
// v = √(2·(F − F₀) / (ρ·Cd·A)) and then median-3 → lead → EMA | Butterworth (calibration/docs/SPEC.md §3).
// The server runs the same chain on the PC as a preview with unpushed settings, fits it and diagnoses it.
// Rig-agnostic: on the open bench (no fan, no tunnel) the chain is checked with the plate size and a known force;
// in the tunnel the ground truth (`--gt-vdas`, or any --gt-* source) adds the sweep, the fits and the score.
// Tunnel steps skip themselves without it. Runs saved by the course's Wind Tunnel Logger (data/tunnel/) are
// overlaid and scored in the "Tunnel runs" panel (calib/tunnel.py, /api/tunnel/*).
import { h, fill } from '../core/dom.js';
import { fixed, signed, smart, isNum } from '../core/format.js';
import { TimeChart } from '../core/chart.js';
import { GRAVITY } from '../core/store.js';
import { asyncButton, button, numberInput, table, kv } from '../core/ui.js';

const BANDS = [2, 3, 4, 5, 6, 7, 8, 9, 10];   // 1 m/s bands from 2 to 11 m/s
const BAND_FULL_S = 3;
const CURVE_WINDOW_S = 120;                   // the server keeps 120 s, enough for one sweep
const RESPONSE_WINDOW_S = 60;
const MSE_GOOD = 0.05, MSE_OK = 0.2;

const FORCE_TOL = 0.01, SPEED_TOL = 0.05;   // known-force check: 1 % on force, 0.05 m/s (or 1 %) on speed
const SUGGESTED_G = [3, 20, 50];              // ≈ 2.4, 6.3 and 10 m/s on the Ø86 mm cup
const CDA_SANE_CM2 = [10, 1000];              // a Cd·A outside this is not a plate that fits the 30 cm envelope
const DRIFT_S = 2;                            // moving mean of the error in the 2 min view

// The body on the load cell. Cd is for the frontal area A (cm² from dimensions in cm), sharp-edged and square to the
// flow (Hoerner; docs/formulas.md §3): a hemisphere's is its outer diameter, wall included. `range` is where a
// tunnel-fitted Cd should land for that shape. Our body is the Ø86 mm cup (catapult.stl), the default.
const OUR_BODY = { shape: 'cup', dims: [8.6] };
const circle = ([d]) => (Math.PI * d * d) / 4;
const SHAPES = {
  rect: { label: 'Flat plate (square or rectangle)', cd: 1.17, range: [1.05, 1.35], dims: ['Width', 'Height'], area: ([w, ht]) => w * ht },
  disc: { label: 'Flat disc', cd: 1.17, range: [1.05, 1.3], dims: ['Diameter'], area: circle },
  cup: { label: 'Hollow hemisphere, open side to the wind', cd: 1.42, range: [1.25, 1.55], dims: ['Outer rim diameter'], area: circle },
  dome: { label: 'Hollow hemisphere, round side to the wind', cd: 0.38, range: [0.3, 0.5], dims: ['Outer rim diameter'], area: circle },
  sphere: { label: 'Sphere', cd: 0.47, range: [0.4, 0.55], dims: ['Diameter'], area: circle },
};

const S = { f0: null, force: null, curve: null, response: null, body: loadBody(), runs: null, run: null };

function loadBody() {
  try {
    const b = JSON.parse(localStorage.getItem('calib.body') || 'null');
    if (b && SHAPES[b.shape] && b.dims?.every((x) => x > 0)) return b;
    const old = Number(localStorage.getItem('calib.plateAreaCm2'));  // before shapes existed
    return old > 0 ? { shape: 'rect', dims: [Math.sqrt(old), Math.sqrt(old)] } : OUR_BODY;
  } catch { return OUR_BODY; }
}
function saveBody() {
  try { localStorage.setItem('calib.body', JSON.stringify(S.body)); } catch { /* storage blocked: keep in memory */ }
}
const shape = () => SHAPES[S.body.shape];
const bodyArea = () => shape().area(S.body.dims);   // cm²
const bodyCda = () => shape().cd * bodyArea();      // cm², before the tunnel fit
const bodyText = () => `${shape().label.toLowerCase()}, ${S.body.dims.map((x) => fixed(x, 1)).join(' × ')} cm`;

const cdaCm2 = (k) => 2e4 / k;     // firmware stores k = 2/(Cd·A) in 1/m²
const isEma = (p) => p.filter_kind >= 0.5;
const smoother = (p) => (isEma(p) ? 'ema_alpha' : 'lpf_hz');
const pitot = (s) => s.buf.stats('gt', 0.5).mean;
const tunnel = (s) => s.hasPitot;
const fanOn = (s) => tunnel(s) && Math.abs(s.buf.last('gt')) > 0.3;
const boardParams = (s) => ({ ...s.params, ...Object.fromEntries(Object.entries(s.board).filter(([, v]) => isNum(v))) });
const rhoNow = (s) => (s.buf.last('rho') > 0.5 ? s.buf.last('rho') : 1.2);
const NEEDS_TUNNEL = { tone: 'skip', text: 'Needs airflow and the ground truth' };
const tunnelNote = () => h('p.faint', 'Runs in the tunnel. Start the dashboard with the tunnel\'s ground truth (pnpm dev --gt-vdas on the white tunnel, pnpm dev --gt-giga on the wood one, or Connection, top right) to enable it; the bench steps do not need it.');
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
  // a replayed Wind Tunnel Logger run has speeds but no force: expected, not a fault
  if (!hasForce(s)) return s.boardState === 'replay' ? null : { title: 'The stream has no force column', detail: 'Send MODE D; if that does not help, flash the airspeed_drag_plate build.', modeD: true };
  return null;
}

// ------------------------------------------------------------------------------------------- views
function drawLive(chart, s, hidden) {
  const b = s.buf, windowS = 20;
  const lines = [
    { key: 'gt', label: 'Tunnel', color: '--s-truth', data: b.col('gt'), width: 2 },
    { key: 'dev', label: 'Sensor', color: '--s-sensor', data: b.col('dev'), width: 2 },
    { key: 'est', label: 'Preview', color: '--s-preview', data: b.col('est'), dash: true, width: 1.75 },
  ].filter((l) => !hidden.has(l.key));
  chart.draw({ t: b.col('t'), i0: b.since(windowS), window: windowS, unit: 'm/s', yMin: 0, yMax: 4, lines });
}

// Last 2 min: tunnel and board, and the board's error as a DRIFT_S moving mean, to see it creep
let errBuf = new Float64Array(0);
function drawDrift(chart, s, hidden) {
  const b = s.buf, windowS = 120, n = b.n, i0 = b.since(windowS), k = Math.round(DRIFT_S * 200);
  const gt = b.col('gt'), dev = b.col('dev');
  if (errBuf.length < b.cap) errBuf = new Float64Array(b.cap);
  let sum = 0, cnt = 0;
  for (let i = Math.max(0, i0 - k); i < n; i++) {
    const e = dev[i] - gt[i];
    if (Number.isFinite(e)) { sum += e; cnt++; }
    const j = i - k, ej = j >= 0 ? dev[j] - gt[j] : NaN;
    if (j >= 0 && Number.isFinite(ej)) { sum -= ej; cnt--; }
    errBuf[i] = cnt > k / 2 ? sum / cnt : NaN;
  }
  const lines = [
    { key: 'gt', label: 'Tunnel', color: '--s-truth', data: gt, width: 2 },
    { key: 'dev', label: 'Board', color: '--s-sensor', data: dev, width: 1.5 },
    { key: 'err', label: `Error, ${DRIFT_S} s mean`, color: '--s-preview', data: errBuf.subarray(0, n), dash: true, width: 2 },
  ].filter((l) => !hidden.has(l.key));
  chart.draw({ t: b.col('t'), i0, window: windowS, unit: 'm/s', zero: true, lines, emptyText: 'Waiting for data…' });
}

function drawCurve(chart, s, hidden) {
  const b = s.buf, i0 = b.since(CURVE_WINDOW_S);
  const f = b.col('feature').subarray(i0), gt = b.col('gt').subarray(i0);
  let lo = Infinity, hi = -Infinity;
  for (const v of f) if (Number.isFinite(v)) { if (v < lo) lo = v; if (v > hi) hi = v; }
  if (lo === Infinity) return chart.empty('No force readings yet');
  if (!tunnel(s)) return chart.empty('Needs the ground truth: the curve is fitted in the tunnel');
  const span = hi - lo || 0.01, rho = b.last('rho') > 0.5 ? b.last('rho') : 1.2;
  const curves = [];
  if (!hidden.has('board') && isNum(s.board.sqrt_k)) curves.push({ fn: (x) => speedOf({ ...s.params, ...s.board }, x, rho), color: '--s-sensor', dash: true });
  if (!hidden.has('pc')) curves.push({ fn: (x) => speedOf(s.params, x, rho), color: '--s-preview' });
  chart.draw({
    xRange: [lo - span * 0.05, hi + span * 0.05], yRange: [0, 12], xUnit: 'force N', yUnit: 'tunnel m/s',
    points: hidden.has('pts') ? [] : [{ xs: f, ys: gt, color: '--s-truth', alpha: 0.22 }], curves,
  });
}

function drawAccuracy(chart, s, hidden) {
  if (!tunnel(s)) return chart.empty('Needs the ground truth');
  const b = s.buf, i0 = b.since(60), gt = b.col('gt').subarray(i0);
  const points = [];
  if (!hidden.has('dev')) points.push({ xs: gt, ys: b.col('dev').subarray(i0), color: '--s-sensor', alpha: 0.3 });
  if (!hidden.has('est')) points.push({ xs: gt, ys: b.col('est').subarray(i0), color: '--s-preview', alpha: 0.22 });
  chart.draw({ xRange: [0, 12], yRange: [0, 12], xUnit: 'tunnel m/s', yUnit: 'sensor m/s', diagonal: true, points });
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
  if (!bars.length) return chart.empty(tunnel(s) ? 'Needs at least 0.2 s at each speed in the last minute' : 'Needs the ground truth');
  m = Math.min(4, m * 1.15);
  chart.draw({ xRange: [1, 12], yRange: [-m, m], xUnit: 'tunnel m/s', yUnit: 'error m/s (reads high ↑)', zero: true, bars });
}

// ------------------------------------------------------------------------------------------ steps
function fitSummary(r, rows) {
  const better = r.after?.mse < r.before?.mse;
  return h(`div.result${better ? '.ok' : '.warn'}`,
    h('strong', better ? `MSE ${fixed(r.before.mse, 4)} → ${fixed(r.after.mse, 4)} m²/s²` : `No improvement (MSE ${fixed(r.before?.mse, 4)} → ${fixed(r.after?.mse, 4)})`),
    kv(rows));
}

// Cd·A the board runs, and the plate it implies
const boardCda = (s) => (isNum(s.board.sqrt_k) && s.board.sqrt_k > 0 ? cdaCm2(s.board.sqrt_k) : NaN);
const plateK = () => 2e4 / bodyCda();
const cdaSane = (cda) => cda >= CDA_SANE_CM2[0] && cda <= CDA_SANE_CM2[1];
const speedForGrams = (s, g) => speedOf({ ...boardParams(s), sqrt_x0: 0 }, (g / 1000) * GRAVITY, rhoNow(s));

const plateStep = {
  id: 'plate',
  title: 'Enter the plate shape and size',
  why: 'The board turns force into speed with the drag area Cd·A, sent as sqrt_k = 2/(Cd·A). Before any tunnel data, Cd·A is the frontal area times the shape\'s Cd (1.17 for a flat plate or disc, 1.42 for a hollow hemisphere open to the wind, 0.38 turned round, 0.47 for a sphere); the tunnel fit refines it later. A wrong Cd·A scales every reading: with Cd·A = 2 m² (sqrt_k 1) a few hundred grams reads as 2 m/s. docs/formulas.md has the formulas.',
  key: (s) => `${S.body.shape}|${S.body.dims.join()}|${fixed(boardCda(s), 1)}`,
  state(s) {
    const cda = boardCda(s);
    if (!isNum(cda)) return { tone: 'blocked', text: 'Board has not answered GET' };
    if (!cdaSane(cda)) return { tone: 'blocked', text: `Board Cd·A ${fixed(cda, 0)} cm²` };
    const cd = cda / bodyArea(), [lo, hi] = shape().range;
    return cd > lo * 0.75 && cd < hi * 1.25 ? { tone: 'done', text: `Cd·A ${fixed(cda, 1)} cm²` } : { tone: 'todo', text: `Cd ${fixed(cd, 2)} for ${fixed(bodyArea(), 0)} cm²` };
  },
  body(ctx) {
    const s = ctx.store, sh = shape(), cda = boardCda(s);
    const kind = h('select.select', { 'aria-label': 'Body shape' },
      Object.entries(SHAPES).map(([id, x]) => h('option', { value: id, selected: id === S.body.shape }, `${x.label} (Cd ${x.cd})`)));
    kind.addEventListener('change', () => {
      const d = S.body.dims[0];
      S.body = { shape: kind.value, dims: SHAPES[kind.value].dims.map(() => d) };
      saveBody();
      ctx.refresh();
    });
    const inputs = sh.dims.map((label, i) => ({ label, el: numberInput({ value: fixed(S.body.dims[i], 1), min: 1, max: 30, label: `${label} in cm`, width: '90px' }) }));
    // drag at speed v for a drag area in cm²: ½ρv²·Cd·A, in grams
    const grams = (cdaCm, v) => (1000 * 0.5 * rhoNow(s) * v * v * cdaCm * 1e-4) / GRAVITY;
    return h('div.stack',
      h('div.field', h('label', 'Shape'), kind),
      h('div.row.end',
        inputs.map((x) => h('div.field', h('label', `${x.label} (cm)`), x.el)),
        asyncButton('Send Cd·A to board', async () => {
          const dims = inputs.map((x) => Number(x.el.value));
          if (!dims.every((x) => x > 0 && x <= 30)) throw new Error('Enter the size in cm (the body must fit the 30 cm envelope).');
          S.body = { shape: S.body.shape, dims };
          saveBody();
          ctx.actions.setOnBoard('sqrt_k', Number(plateK().toPrecision(5)));
          ctx.toast(`Cd·A ${fixed(bodyCda(), 1)} cm² (sqrt_k ${fixed(plateK(), 1)}) sent. Export the header so the board boots with it.`);
          await new Promise((r) => setTimeout(r, 700));
          ctx.refresh();
        }, { primary: true, busy: 'Sending…' })),
      kv([
        ['This body', `A ${fixed(bodyArea(), 1)} cm² × Cd ${sh.cd} = Cd·A ${fixed(bodyCda(), 1)} cm², sqrt_k ${fixed(plateK(), 1)}`],
        ['Expected drag', `${fixed(grams(bodyCda(), 2), 1)} g at 2 m/s, ${fixed(grams(bodyCda(), 10), 0)} g at 10 m/s`],
        ['Board now', isNum(cda) ? `Cd·A ${fixed(cda, 1)} cm² (sqrt_k ${smart(s.board.sqrt_k, 4)})${cdaSane(cda) ? '' : `: a ${fixed(Math.sqrt(cda), 0)} cm square plate`}` : 'no GET reply yet'],
        isNum(cda) && ['On this board', SUGGESTED_G.map((g) => `${g} g → ${fixed(speedForGrams(s, g), 1)} m/s`).join(' · ')],
      ]));
  },
};

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
          if (tunnel(s) && Math.abs(m.gt) > 0.3) ctx.toast(`The tunnel reads ${fixed(m.gt, 2)} m/s: F₀ includes some drag. Stop the fan and measure again.`, 'warn', 6000);
          ctx.actions.setParam('sqrt_x0', Number(m.mean.toPrecision(5)));
          S.f0 = { value: m.mean, sd: m.sd };
          ctx.toast(`F₀ = ${signed(m.mean, 4)} N set on the PC. Push it in the last step.`);
          ctx.refresh();
        }, { primary: true, busy: 'Measuring…' })),
      h('p.faint', tunnel(s) ? 'Stop the fan and wait until the tunnel reads about 0 m/s.' : 'No ground truth connected: make sure nothing is blowing on the plate.'));
  },
  update(body, s) {
    const el = body.querySelector('[data-live]');
    if (el) el.textContent = `${tunnel(s) ? `Tunnel ${fixed(pitot(s), 2)} m/s · ` : ''}force ${signed(s.buf.stats('feature', 0.5).mean, 4)} N`;
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
          ? 'The force is off: scale the load cell (Load cell section), then measure F₀ again.'
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
      h('p.faint', `Try ${SUGGESTED_G.join(', ')} g; on this board they read about ${SUGGESTED_G.map((g) => fixed(speedForGrams(s, g), 1)).join(', ')} m/s.`),
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
  why: 'Finds the drag area Cd·A and F₀ that make the formula match the tunnel across the sweep. A robust fit ignores the odd spike in the ground truth.',
  key: (s) => `${S.curve?.at || ''}|${tunnel(s)}`,
  state(s) {
    if (S.curve) return S.curve.ok ? { tone: 'done', text: `Cd·A ${fixed(cdaCm2(S.curve.params.sqrt_k), 1)} cm²` } : { tone: 'blocked', text: 'Fit failed' };
    if (!tunnel(s)) return NEEDS_TUNNEL;
    if (bandsCovered(s) < 6) return { tone: 'blocked', text: 'Sweep first' };
    return { tone: 'todo', text: '' };
  },
  body(ctx) {
    if (!tunnel(ctx.store) && !S.curve) return tunnelNote();
    const r = S.curve;
    let out = null;
    if (r?.ok) {
      const cda = cdaCm2(r.params.sqrt_k), cd = cda / bodyArea(), [lo, hi] = shape().range;
      const cdOk = cd >= lo && cd <= hi;
      out = fitSummary(r, [
        ['Drag area Cd·A', `${fixed(cda, 1)} cm²  (was ${fixed(cdaCm2(r.previous.sqrt_k), 1)})`],
        ['Zero-flow force F₀', `${signed(r.params.sqrt_x0, 4)} N`],
        ['Implied Cd', `${fixed(cd, 2)} for a ${bodyText()} ${cdOk ? `(expected ${lo}–${hi})` : `(outside ${lo}–${hi}: check the shape and size in step 1)`}`],
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
        h('span.faint', `Body: ${bodyText()} (step 1)`),
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
  why: 'The score is MSE against the tunnel, so noise at steady speed and lag on speed changes both cost points. Choose a filter, then fit its setting together with the lag compensation while the fan steps.',
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
      !tunnel(s) && h('p.faint', 'No ground truth: settings are confirmed by reading them back. The score comparison runs in the tunnel.'),
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

// ------------------------------------------------------------------------------------ tunnel runs
// Runs saved by the Wind Tunnel Logger (tools/wind-tunnel-logger, `pnpm logger`): the board against the tunnel,
// scored and with the drift of its error. Read over HTTP from calib/tunnel.py.
const runView = { chart: null, sig: '' };
const runFile = (r) => `${r.dir}/${r.name}_aligned.csv`;

/** Replays a logger run through this page's live views, score and diagnosis (the server's replay source). */
function replayRun(ctx, r) {
  ctx.actions.connect({ source: 'replay', replay_file: runFile(r), speed: 1 });
  ctx.toast(`Replaying ${r.name}: the board line is the logger's board speed, the tunnel line its ground truth. No force was logged, so the curve and response fits need a live session.`, 'ok', 8000);
  location.hash = '#wind-speed';
}
const nan = (a) => Float64Array.from(a, (v) => (v == null ? NaN : v));

function drawRun() {
  const r = S.run, c = runView.chart;
  if (!c || !r?.series) return;
  const sr = r.series, t = nan(sr.t);
  c.draw({
    t, i0: 0, window: t[t.length - 1] - t[0] || 1, unit: 'm/s', zero: true, xFormat: (v) => `${Math.round(v)} s`,
    lines: [
      { label: 'Tunnel', color: '--s-truth', data: nan(sr.gt), width: 2 },
      { label: 'Board', color: '--s-sensor', data: nan(sr.board), width: 1.5 },
      { label: `Error, ${sr.window_s} s mean`, color: '--s-preview', data: nan(sr.error_mean), dash: true, width: 2 },
    ],
  });
}

function runStats(r, ctx) {
  if (!('mse' in r)) return h('p.faint', `Only ${r.pairs} paired samples: nothing to score.`);
  const drift = r.drift_per_min, k = r.k_scale;
  return h('div.stack',
    kv([
      ['Run', `${fixed(r.duration_s, 0)} s · ${r.pairs} pairs${r.missing ? ` (${r.missing} tunnel samples with no board sample)` : ''}${isNum(r.board_rate_hz) ? ` · board ${fixed(r.board_rate_hz, 0)} Hz` : ''}${isNum(r.gt_rate_hz) ? ` · tunnel ${fixed(r.gt_rate_hz, 1)} Hz` : ''}`],
      ['Score', `MSE ${fixed(r.mse, 4)} m²/s² · RMSE ${fixed(r.rmse, 3)} m/s`],
      ['Error', `bias ${signed(r.bias, 3)} m/s · σ ${fixed(r.sigma, 3)} m/s`],
      isNum(drift) && ['Drift', `${signed(drift, 3)} m/s per minute · bias ${signed(r.bias_first, 3)} in the first third, ${signed(r.bias_last, 3)} in the last`],
      r.bands?.length && ['By speed', r.bands.map((b) => `${b.lo}–${b.lo + 1}: ${signed(b.bias, 2)}`).join(' · ')],
    ]),
    isNum(k) && Math.abs(k - 1) > 0.02 && h('div.result.warn',
      h('strong', `Above 2 m/s the board reads ${fixed(Math.abs(1 / Math.sqrt(k) - 1) * 100, 1)} % ${k < 1 ? 'high' : 'low'} at every speed`),
      h('p', `That is the drag area. If the board ran its current settings during this run, multiply sqrt_k by ${fixed(k, 3)} (Cd·A ${fixed(cdaCm2(ctx.store.params.sqrt_k), 1)} → ${fixed(cdaCm2(ctx.store.params.sqrt_k * k), 1)} cm²), then push.`),
      h('div.row', button(`Apply sqrt_k × ${fixed(k, 3)} on the PC`, () => {
        ctx.actions.setParam('sqrt_k', Number((ctx.store.params.sqrt_k * k).toPrecision(5)));
        ctx.toast('Applied on the PC. Push to send it to the board.');
      }))));
}

const runsPanel = {
  title: 'Tunnel runs',
  sub: 'Saved by the Wind Tunnel Logger (pnpm logger) or by Record with tunnel ground truth, in data/tunnel/: the board against the tunnel and the drift of its error.',
  render(ctx) {
    const pick = h('select.select', { 'aria-label': 'Tunnel run', style: { flex: '1' } });
    const stats = h('div'), note = h('p.faint'), host = h('div.chart', { hidden: true });
    runView.chart = new TimeChart(host);
    runView.chart.onHover = drawRun;

    async function open(i) {
      const r = S.runs?.[i];
      if (!r) return;
      fill(stats, h('p.faint', 'Loading…'));
      const res = await fetch(`/api/tunnel/run?name=${encodeURIComponent(r.name)}&dir=${encodeURIComponent(r.dir)}`);
      const data = await res.json();
      if (!res.ok) { fill(stats, h('p.faint', data.error || 'Could not read the run.')); return; }
      S.run = data;
      runView.block.hidden = false;
      fill(stats, runStats(data, ctx));
      drawRun();
    }

    async function list(pickName) {
      try {
        const res = await fetch('/api/tunnel/runs');
        const data = await res.json();
        S.runs = data.runs;
        const when = (r) => new Date(r.mtime * 1000).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
        fill(pick, S.runs.map((r, i) => h('option', { value: String(i) }, `${r.name} · ${when(r)}`)));
        note.textContent = S.runs.length ? ''
          : 'No runs yet. Start the logger with pnpm logger (stop pnpm dev first: one program owns the board\'s port), log a run and save it; it lands in data/tunnel/.';
        pick.hidden = !S.runs.length;
        const i = Math.max(0, S.runs.findIndex((r) => r.name === pickName));
        pick.value = String(i);
        if (S.runs.length) await open(i);
      } catch {
        note.textContent = 'The calibration server is not reachable.';
      }
    }

    pick.addEventListener('change', () => open(Number(pick.value)));
    list();
    // a recording with tunnel ground truth was just saved as a logger run: show it
    ctx.store.on('session', (m) => { if (m.recording === false && m.tunnel_run) list(m.tunnel_run.name); });
    const key = (label, color, dash) => h('span.row', { style: { gap: 'var(--sp-1)' } },
      h(`span.sw${dash ? '.dash' : ''}`, { style: { background: `var(${color})`, color: `var(${color})` } }), label);
    const legend = h('div.legend', key('Tunnel', '--s-truth'), key('Board', '--s-sensor'), key('Board − tunnel, 10 s mean', '--s-preview', true));
    const chartBlock = h('div.stack', { hidden: true }, legend, host);
    host.hidden = false;
    runView.block = chartBlock;
    const replay = button('Replay in the calibration views', () => S.runs?.[Number(pick.value)] && replayRun(ctx, S.runs[Number(pick.value)]));
    return h('div.stack', h('div.row', pick, replay, button('Refresh', () => list(), { cls: '.quiet' })), note, stats, chartBlock);
  },
  update() { drawRun(); },
};

// ----------------------------------------------------------------------------------------- module
const LEGEND_LIVE = [
  { key: 'gt', label: 'Tunnel', color: '--s-truth' },
  { key: 'dev', label: 'Sensor (board)', color: '--s-sensor' },
  { key: 'est', label: 'Preview (PC settings)', color: '--s-preview', dash: true },
];

export default {
  id: 'wind-speed',
  title: 'Wind speed',
  part: 'Drag plate on the load cell · v = √(2·(F − F₀) / (ρ·Cd·A)) on the board · 200 Hz',
  intro: 'Turns plate force into wind speed. On the bench: enter the plate size, measure F₀ and check a known force. In the tunnel: also sweep, fit the curve and the response, then push. The orange preview shows unpushed settings live.',
  allClear: 'No problems found. In the tunnel this includes the scoring window against the ground truth.',

  status(s) {
    if (s.conn !== 'open') return { tone: 'idle', text: 'Offline' };
    const p = streamProblem(s);
    if (p) return { tone: 'bad', text: 'No readings' };
    if (!tunnel(s)) return S.force ? { tone: S.force.ok ? 'ok' : 'warn', text: S.force.ok ? 'Bench check passed' : 'Bench check off' } : { tone: 'idle', text: 'Bench, no tunnel' };
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
    { label: 'Tunnel', unit: 'm/s', digits: 2, value: (s) => (tunnel(s) ? pitot(s) : NaN), note: (s) => (tunnel(s) ? 'ground truth' : 'not connected') },
    {
      label: 'Error', unit: 'm/s', value: (s) => (tunnel(s) ? s.buf.stats('dev', 0.5).mean - pitot(s) : NaN), format: (v) => (isNum(v) ? signed(v, 2) : '–'),
      note: (s) => (tunnel(s) ? 'board − tunnel, last 0.5 s' : 'needs the ground truth'),
    },
    {
      label: 'Score (MSE, 30 s)', unit: 'm²/s²', digits: 4, value: (s) => (tunnel(s) ? boardScore(s) : NaN), tone: (s) => (tunnel(s) ? tone(boardScore(s)) : null),
      note: (s) => {
        const m = s.metrics?.dev;
        if (!m) return tunnel(s) ? '' : 'needs the ground truth';
        const base = `bias ${signed(m.bias, 3)} · σ ${fixed(m.sigma, 3)} · lag ${fixed(m.lag_ms, 0)} ms`;
        return s.dirtyParams().length && isNum(previewScore(s)) ? `${base} · preview ${fixed(previewScore(s), 4)}` : base;
      },
    },
  ],

  views: [
    { id: 'live', label: 'Live', title: 'Wind speed', sub: 'Last 20 s', legend: LEGEND_LIVE, draw: drawLive },
    {
      id: 'drift', label: '2 min', title: 'Board against the tunnel', sub: `Last 2 min. The dashed line is the board's error (${DRIFT_S} s mean): a slope is drift.`,
      legend: [{ key: 'gt', label: 'Tunnel', color: '--s-truth' }, { key: 'dev', label: 'Board', color: '--s-sensor' }, { key: 'err', label: 'Error', color: '--s-preview', dash: true }],
      draw: drawDrift,
    },
    {
      id: 'curve', label: 'Curve', title: 'Calibration curve', sub: 'Force against tunnel speed, last 2 min. The line should run through the dots.', tall: true, kind: 'xy',
      legend: [{ key: 'pts', label: 'Samples', color: '--s-truth' }, { key: 'pc', label: 'PC curve', color: '--s-preview' }, { key: 'board', label: 'Board curve', color: '--s-sensor', dash: true }],
      draw: drawCurve,
    },
    {
      id: 'accuracy', label: 'Accuracy', title: 'Board against the tunnel', sub: 'Last 60 s. On the diagonal is perfect; loops are lag on speed changes.', tall: true, kind: 'xy',
      legend: [{ key: 'dev', label: 'Board', color: '--s-sensor' }, { key: 'est', label: 'Preview', color: '--s-preview' }],
      draw: drawAccuracy,
    },
    {
      id: 'bands', label: 'By speed', title: 'Error by speed', sub: 'Mean error per 1 m/s band, last 60 s. Whiskers: ±1σ.', kind: 'xy',
      legend: [{ key: 'dev', label: 'Board', color: '--s-sensor' }, { key: 'est', label: 'Preview', color: '--s-preview' }],
      draw: drawBands,
    },
  ],

  panels: [runsPanel],

  tile: {
    readouts: ['Sensor', 'Tunnel', 'Error', 'Score (MSE, 30 s)'], view: 'live', wide: true,
    // the newest Wind Tunnel Logger run, with a way into the calibration views
    footer(ctx) {
      const line = h('div.tile-run');
      async function refresh() {
        if (!line.isConnected && runView.tileSeen) return;
        runView.tileSeen = true;
        try {
          const runs = (await (await fetch('/api/tunnel/runs')).json()).runs;
          if (!runs.length) { fill(line, h('span.label', 'Tunnel'), h('span.faint', 'No logger runs yet: pnpm logger saves them in data/tunnel/.')); return; }
          const r = runs[0], d = await (await fetch(`/api/tunnel/run?name=${encodeURIComponent(r.name)}&dir=${encodeURIComponent(r.dir)}&series=0`)).json();
          fill(line, h('span.label', 'Last tunnel run'), h('span.tile-next-title', r.name),
            h('span.faint', isNum(d.mse) ? `MSE ${fixed(d.mse, 3)} · bias ${signed(d.bias, 2)} · drift ${signed(d.drift_per_min, 2)}/min` : 'too short to score'),
            h('span.spacer'), button('Replay', () => replayRun(ctx, r), { cls: '.sm.quiet' }));
        } catch { fill(line, h('span.faint', 'Tunnel runs unavailable: is the server running?')); }
        setTimeout(refresh, 30000);
      }
      refresh();
      return line;
    },
  },

  steps: [plateStep, zeroStep, forceStep, sweepStep, curveStep, responseStep, pushStep],

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
    const cda = boardCda(s);
    if (isNum(cda) && !cdaSane(cda)) {
      out.push({
        id: 'cda', level: 'bad', title: `The board's drag area is ${fixed(cda, 0)} cm²`,
        detail: `That is a ${fixed(Math.sqrt(cda), 0)} cm square plate, so the board reads ${SUGGESTED_G.map((g) => `${g} g as ${fixed(speedForGrams(s, g), 1)} m/s`).join(', ')}. Enter the shape and size in step 1, or send the Cd·A of the ${bodyText()} now.`,
        action: { label: `Send Cd·A ${fixed(bodyCda(), 0)} cm²`, run: () => ctx.actions.setOnBoard('sqrt_k', Number(plateK().toPrecision(5))) },
      });
    }
    if (s.tele.sensor_hz < 190) {
      out.push({ id: 'rate', level: 'warn', title: `Sensor stream at ${fixed(s.tele.sensor_hz, 0)} Hz`, detail: 'The brief requires 200 Hz. Close other programs using the port, and check the USB cable.' });
    }
    const dirty = s.dirtyParams();
    if (dirty.length) {
      out.push({ id: 'dirty', level: 'info', title: `${dirty.length} setting${dirty.length > 1 ? 's are' : ' is'} not on the board yet`, detail: `${dirty.join(', ')}. The sensor line still uses the old values.`, action: { label: 'Push to board', run: () => ctx.actions.push() } });
    }
    if (!tunnel(s)) {
      out.push({ id: 'bench', level: 'info', title: 'Bench mode: no ground truth', detail: 'The plate size, F₀ and the known-force check work without airflow. The sweep, fits and score appear once the tunnel\'s ground truth is connected (pnpm dev --gt-vdas).' });
    } else if (!s.metrics?.est) {
      out.push({ id: 'collecting', level: 'info', title: 'Collecting a score', detail: 'Recommendations appear after a few seconds of data with the ground truth.' });
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
      { name: 'Environment sensors', role: 'Air density ρ', connect: 'See Temperature', spec: 'Updated between ticks' },
    ],
    test: [
      'Bench: enter the plate size, measure F₀, then pull the plate with 3, 20 and 50 g; the board reads each force and its speed.',
      'Tunnel: sweep 2 → 10 m/s holding each speed, fit the curve, then fit the response on steps. Log the assessed runs with the Wind Tunnel Logger (pnpm logger) and compare them under Tunnel runs.',
      'Push and export: the board runs the fitted values, and the next build boots with them.',
      'Check STATS: no overruns at 200 Hz.',
    ],
    commands: [
      { label: 'Competition build', cmd: 'cd firmware && pio run -e airspeed_drag_plate -t upload' },
      { label: 'Run the dashboard on the board', cmd: 'cd calibration && python -m calib serve --port /dev/cu.usbmodem103' },
    ],
  },
};
