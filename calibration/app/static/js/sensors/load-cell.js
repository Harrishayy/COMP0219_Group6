// Load cell: a bar cell behind the M5Stack Weight I2C unit (HX711) or the SparkFun Qwiic Scale (NAU7802), whichever
// the firmware finds on I²C; its GET reply names it (lc_frontend). The board streams force F in newtons,
// F = (counts − lc_offset) × lc_n_per_count; TARE sets lc_offset, the known-mass step sets lc_n_per_count.
import { h } from '../core/dom.js';
import { fixed, signed, smart, isNum } from '../core/format.js';
import { GRAVITY, RHO_REF } from '../core/store.js';
import { asyncButton, button, numberInput, table, kv } from '../core/ui.js';

// The cell and ADC as wired (firmware/lib/drivers/src/load_cell.cpp). Change these for another cell.
const CELL = {
  capacityG: 1000,
  ratedMvPerV: [0.8, 2.2],   // typical rated output range of small bar cells
  gain: 128,
  adcFullScale: 2 ** 23,
  noiseWarnG: 0.5,
  noiseBadG: 2,
  suggestedMassesG: [0, 200, 500, 800],
};
// N/count for a cell of rated output r (mV/V), ratiometric excitation: F_full / (r·1e-3 · gain · 2^23)
const expectedScale = (r) => (CELL.capacityG / 1000 * GRAVITY) / (r * 1e-3 * CELL.gain * CELL.adcFullScale);
const SCALE_RANGE = [expectedScale(CELL.ratedMvPerV[1]), expectedScale(CELL.ratedMvPerV[0])];

// The two amplifiers the firmware can find (firmware/lib/drivers/src/load_cell.cpp). Both are gain 128 and
// ratiometric, so SCALE_RANGE fits either. `zero` is the raw reading for no bridge signal: the Weight unit reports
// HX711 counts offset by 2^23 (it rests at about 8 390 000 with nothing on the bridge).
const FRONTENDS = {
  weight_i2c: { name: 'M5Stack Weight I2C', chip: 'HX711', addr: '0x26', sps: 10, zero: 2 ** 23 },
  nau7802: { name: 'Qwiic Scale', chip: 'NAU7802', addr: '0x2A', sps: 320, zero: 0 },
};
const frontendId = (s) => s.tele.board?.lc_frontend;
const frontend = (s) => FRONTENDS[frontendId(s)] || null;

// Progress kept across a remount (the page object is rebuilt, this module is not).
const S = { taredAt: null, rows: [], result: null, scaledAt: null, lastScale: null, verify: null };

const toG = (n) => (n / GRAVITY) * 1000;
const loadG = (s) => toG(s.buf.stats('feature', 0.5).mean);
// Noise from the median sample-to-sample change (×1.4826/√2 = σ for white noise): a mass being put on or
// taken off moves only a handful of samples, so it does not read as noise.
function diffs(s, sec) {
  const b = s.buf, f = b.col('feature'), out = [];
  for (let i = Math.max(1, b.since(sec)); i < b.n; i++) {
    const d = f[i] - f[i - 1];
    // A slow amplifier (Weight I2C, 10 SPS) repeats its last value between conversions: skip the repeats.
    if (Number.isFinite(d) && d !== 0) out.push(Math.abs(d));
  }
  return out.sort((a, c) => a - c);
}
const noiseG = (s) => { const d = diffs(s, 3); return d.length > 12 ? toG((1.4826 / Math.SQRT2) * d[d.length >> 1]) : NaN; };
// The load changed in the last 30 s if any single step is far above the noise.
const loadChanged = (s) => { const d = diffs(s, 30); return d.length > 20 && d[d.length - 1] > 25 * Math.max(d[d.length >> 1], 1e-9); };
const driftG = (s) => (loadChanged(s) ? NaN : toG(s.buf.stats('feature', 3).mean - s.buf.stats('feature', 3, 27).mean));
const scale = (s) => (isNum(s.board.lc_n_per_count) ? s.board.lc_n_per_count : s.params.lc_n_per_count);
const offset = (s) => (isNum(s.board.lc_offset) ? s.board.lc_offset : s.params.lc_offset);
const rawCounts = (s) => s.buf.last('feature') / scale(s) + offset(s);
const hasForce = (s) => isNum(s.buf.last('feature'));
const rhoNow = (s) => (s.buf.last('rho') > 0.5 ? s.buf.last('rho') : RHO_REF);
const fanOn = (s) => s.hasPitot && Math.abs(s.buf.last('gt')) > 0.5;
const time = (d) => d.toTimeString().slice(0, 5);

function solve(rows, oldScale) {
  const pts = rows.filter((r) => r.massG > 0);
  if (!pts.length) return null;
  const W = pts.map((r) => (r.massG / 1000) * GRAVITY), F = pts.map((r) => r.forceN);
  const gain = W.reduce((a, w, i) => a + w * F[i], 0) / W.reduce((a, w) => a + w * w, 0); // board reads gain × true
  const fullScale = Math.max(...F.map(Math.abs)) || 1;
  const linearity = Math.max(...F.map((f, i) => Math.abs(f - gain * W[i]))) / fullScale;
  let zeroG = NaN;
  if (rows.length >= 2) {
    const x = rows.map((r) => (r.massG / 1000) * GRAVITY), y = rows.map((r) => r.forceN);
    const mx = x.reduce((a, b) => a + b) / x.length, my = y.reduce((a, b) => a + b) / y.length;
    const slope = x.reduce((a, xi, i) => a + (xi - mx) * (y[i] - my), 0) / (x.reduce((a, xi) => a + (xi - mx) ** 2, 0) || 1);
    zeroG = toG(my - slope * mx);
  }
  return { gain, oldScale, newScale: oldScale / gain, linearity, zeroG, points: pts.length };
}

function streamProblem(s) {
  if (s.conn !== 'open') return { title: 'Calibration server not reachable', detail: 'Start it with `python -m calib serve` in calibration/, then reload this page.' };
  if (!s.streaming) return { title: 'No readings from the board', detail: 'Open Connection (top right), pick the Nucleo port and connect, or start the simulator.', modeD: false };
  if (!hasForce(s)) return { title: 'The stream has no force column', detail: 'The board is not sending debug rows. Send MODE D; if that does not help, flash the airspeed_drag_plate build.', modeD: true };
  if (frontendId(s) === 'none') return { title: 'No load-cell amplifier on I²C', detail: 'The board found neither the Weight I2C unit (0x26) nor the Qwiic Scale (0x2A), so force reads 0. Check the Grove/Qwiic cable to D14/D15; the board looks for the Weight unit again every 2 s.' };
  return null;
}

// ------------------------------------------------------------------------------------------- views
let gramsBuf = new Float64Array(0);
function drawLoad(windowS) {
  return (chart, s) => {
    const b = s.buf, i0 = b.since(windowS), n = b.n, f = b.col('feature');
    if (gramsBuf.length < n) gramsBuf = new Float64Array(b.cap);
    for (let i = i0; i < n; i++) gramsBuf[i] = toG(f[i]);
    chart.draw({
      t: b.col('t'), i0, window: windowS, unit: 'g', minSpan: 2, digits: 1,
      lines: [{ label: 'Load', color: '--s-sensor', data: gramsBuf.subarray(0, n), digits: 1 }],
      emptyText: hasForce(s) ? 'Waiting for data…' : 'No force readings: connect the board',
    });
  };
}

// ------------------------------------------------------------------------------------------ steps
const tareStep = {
  id: 'tare',
  title: 'Zero the cell',
  why: 'Take everything off the cell and make sure no air is blowing on the plate. Tare stores the current reading as zero; every later reading is measured from it.',
  state(s) {
    if (!s.streaming || !hasForce(s)) return { tone: 'blocked', text: 'No readings' };
    if (S.taredAt) return { tone: 'done', text: `Tared at ${time(S.taredAt)}` };
    if (fanOn(s)) return { tone: 'blocked', text: 'Fan is on' };
    return { tone: 'todo', text: '' };
  },
  body(ctx) {
    const live = h('p.num', { dataset: { live: '' } });
    return h('div.stack', live, h('div.row', asyncButton('Tare now', async () => {
      if (fanOn(ctx.store)) ctx.toast('The fan is on: the tare will include drag. Stop it first for a true zero.', 'warn', 6000);
      ctx.actions.command('TARE');
      S.taredAt = new Date();
      S.rows = [];
      S.result = null;
      await new Promise((r) => setTimeout(r, 900));  // board averages ~0.5 s, then the server reads it back
      ctx.toast('Tared. The reading should now sit near 0 g.');
      ctx.refresh();
    }, { primary: true, busy: 'Taring…' })));
  },
  update(body, s) {
    const el = body.querySelector('[data-live]');
    if (el) el.textContent = `Reading now: ${fixed(loadG(s), 1)} g  (noise ${fixed(noiseG(s), 2)} g)`;
  },
};

const scaleStep = {
  id: 'scale',
  title: 'Scale with known masses',
  why: `Measure 0 g and two or more known masses spread over the range (for example ${CELL.suggestedMassesG.join(', ')} g; never above ${CELL.capacityG} g). The scale is the least-squares slope, so extra points also reveal non-linearity.`,
  key: (s) => `${S.rows.length}|${!!S.result}|${s.isSim}`,
  state() {
    if (S.scaledAt) return { tone: 'done', text: `Applied (${signed((1 / S.lastScale.gain - 1) * 100, 2)} %)` };
    if (S.result) return { tone: 'todo', text: 'Ready to apply' };
    return { tone: 'todo', text: S.rows.length ? `${S.rows.length} measured` : '' };
  },
  body(ctx) {
    const s = ctx.store;
    const next = CELL.suggestedMassesG.find((m) => !S.rows.some((r) => r.massG === m)) ?? 300;
    const mass = numberInput({ value: next, min: 0, max: CELL.capacityG, label: 'Mass in grams' });
    const measure = asyncButton(s.isSim ? 'Hang and measure 3 s' : 'Measure 3 s', async () => {
      const g = Number(mass.value);
      if (!(g >= 0)) throw new Error('Enter the mass in grams.');
      if (g > CELL.capacityG) throw new Error(`${g} g is over the cell's ${CELL.capacityG} g capacity.`);
      if (s.isSim) { ctx.actions.sim({ load_g: g }); await new Promise((r) => setTimeout(r, 1500)); }
      try {
        const m = await ctx.actions.measure('feature', 3);
        S.rows.push({ massG: g, forceN: m.mean, sdN: m.sd });
        S.result = null;
      } finally {
        if (s.isSim) ctx.actions.sim({ load_g: 0 });
      }
      ctx.refresh();
    }, { busy: 'Measuring…' });

    const rows = S.rows.map((r, i) => {
      const err = r.massG > 0 ? (toG(r.forceN) - r.massG) / r.massG : NaN;
      return [
        `${r.massG} g`,
        `${fixed(toG(r.forceN), 2)} g`,
        `± ${fixed(toG(r.sdN), 2)} g`,
        { v: isNum(err) ? `${signed(err * 100, 2)} %` : '–', cls: Math.abs(err) > 0.01 ? 'warn' : '' },
        h('button.btn.quiet.sm', { type: 'button', 'aria-label': `Remove ${r.massG} g`, onclick: () => { S.rows.splice(i, 1); S.result = null; ctx.refresh(); } }, 'Remove'),
      ];
    });

    const res = S.result;
    return h('div.stack',
      S.rows.length ? table(['Mass', 'Reads', 'Noise', 'Error', ''], rows) : null,
      h('div.row.end',
        h('div.field', h('label', 'Known mass (g)'), mass),
        measure,
        S.rows.some((r) => r.massG > 0) && button('Solve scale', () => {
          S.result = solve(S.rows, scale(s));
          ctx.refresh();
        })),
      !s.isSim && h('p.faint', 'Place the mass gently and wait for the chart to settle before measuring.'),
      res && h(`div.result${Math.abs(res.gain - 1) < 0.002 ? '.ok' : ''}`,
        h('strong', `The board reads ${fixed(Math.abs(res.gain - 1) * 100, 2)} % ${res.gain > 1 ? 'high' : 'low'}`),
        kv([
          ['New scale', `${smart(res.newScale, 5)} N/count`],
          ['Board now', `${smart(res.oldScale, 5)} N/count`],
          ['Linearity', `${fixed(res.linearity * 100, 2)} % of the largest load`],
          isNum(res.zeroG) && ['Zero offset', `${signed(res.zeroG, 2)} g${Math.abs(res.zeroG) > 1 ? '  (tare again with nothing on the cell)' : ''}`],
        ]),
        h('div.row',
          asyncButton('Apply to board', async () => {
            ctx.actions.setOnBoard('lc_n_per_count', res.newScale);
            S.lastScale = res;
            S.scaledAt = new Date();
            S.rows = [];
            S.result = null;
            ctx.toast(`Scale ${smart(res.newScale, 5)} N/count sent to the board.`);
            ctx.refresh();
          }, { primary: true, busy: 'Sending…' }),
          button('Discard', () => { S.result = null; ctx.refresh(); }, { cls: '.quiet' }))));
  },
};

const verifyStep = {
  id: 'verify',
  title: 'Check with another mass',
  why: 'Put on a mass you did not use for scaling. It should read within 0.5 % of its true value.',
  key: () => `${!!S.verify}`,
  state() {
    if (!S.verify) return { tone: 'todo', text: S.scaledAt ? '' : 'After scaling' };
    const ok = Math.abs(S.verify.err) < 0.005;
    return { tone: ok ? 'done' : 'blocked', text: `${signed(S.verify.err * 100, 2)} % at ${S.verify.massG} g` };
  },
  body(ctx) {
    const s = ctx.store;
    const mass = numberInput({ value: S.verify?.massG ?? 300, min: 1, max: CELL.capacityG, label: 'Mass in grams' });
    return h('div.stack',
      h('div.row.end',
        h('div.field', h('label', 'True mass (g)'), mass),
        asyncButton(s.isSim ? 'Hang and check 3 s' : 'Check 3 s', async () => {
          const g = Number(mass.value);
          if (!(g > 0 && g <= CELL.capacityG)) throw new Error(`Enter a mass between 1 and ${CELL.capacityG} g.`);
          if (s.isSim) { ctx.actions.sim({ load_g: g }); await new Promise((r) => setTimeout(r, 1500)); }
          try {
            const m = await ctx.actions.measure('feature', 3);
            S.verify = { massG: g, readG: toG(m.mean), err: (toG(m.mean) - g) / g };
          } finally {
            if (s.isSim) ctx.actions.sim({ load_g: 0 });
          }
          ctx.refresh();
        }, { busy: 'Measuring…' })),
      S.verify && h(`div.result${Math.abs(S.verify.err) < 0.005 ? '.ok' : '.warn'}`,
        h('strong', `Reads ${fixed(S.verify.readG, 2)} g for ${S.verify.massG} g (${signed(S.verify.err * 100, 2)} %)`),
        Math.abs(S.verify.err) >= 0.005 && h('p', 'Over 0.5 % off: repeat step 2 with more masses spread across the range, or check nothing rubs on the cell.')));
  },
};

// ----------------------------------------------------------------------------------------- module
export default {
  id: 'load-cell',
  title: 'Load cell',
  part: `${CELL.capacityG / 1000} kg bar cell · M5Stack Weight I2C (HX711, 10 samples/s) or Qwiic Scale (NAU7802, 320 samples/s), whichever is plugged in`,
  intro: 'Measures the drag force on the plate. Zero it, scale it with known masses, then check it with one more.',
  allClear: 'Readings are steady and the scale is plausible for this cell.',

  status(s) {
    if (s.conn !== 'open') return { tone: 'idle', text: 'Offline' };
    if (!s.streaming || !hasForce(s)) return { tone: 'bad', text: 'No readings' };
    if (Math.abs(loadG(s)) > CELL.capacityG) return { tone: 'bad', text: 'Overloaded' };
    if (!fanOn(s) && noiseG(s) > CELL.noiseBadG) return { tone: 'bad', text: 'Very noisy' };
    if (S.scaledAt && (!S.verify || Math.abs(S.verify.err) < 0.005)) return { tone: 'ok', text: 'Calibrated' };
    return { tone: 'warn', text: 'Not calibrated' };
  },

  readouts: [
    { label: 'Load', unit: 'g', digits: 1, value: loadG, note: (s) => (hasForce(s) ? `${fixed(s.buf.stats('feature', 0.5).mean, 4)} N` : '') },
    { label: 'Wind speed', unit: 'm/s', digits: 2, value: (s) => s.buf.stats('dev', 0.5).mean, note: () => 'board output, last 0.5 s' },
    {
      label: 'Dynamic pressure', unit: 'Pa', digits: 2, value: (s) => s.buf.dynPressure('dev', 0.5),
      note: (s) => (s.hasPitot ? `pitot ${fixed(s.buf.dynPressure('gt', 0.5), 2)} Pa` : `½ρv², ρ ${fixed(rhoNow(s), 3)} kg/m³`),
    },
    {
      label: 'Noise (σ, 3 s)', unit: 'g', digits: 2, value: noiseG,
      tone: (s) => { const n = noiseG(s); return !isNum(n) ? null : n > CELL.noiseBadG ? 'bad' : n > CELL.noiseWarnG ? 'warn' : null; },
      note: (s) => (fanOn(s) ? 'fan on: includes airflow' : ''),
    },
    { label: 'Drift (30 s)', unit: 'g', value: driftG, format: (v) => signed(v, 2), note: (s) => (loadChanged(s) ? 'paused: the load changed' : '') },
    {
      label: 'Raw', unit: 'counts', value: rawCounts, format: (v) => (isNum(v) ? Math.round(v).toLocaleString('en-GB') : '–'),
      note: (s) => { const f = frontend(s); return `${f ? `${f.chip} · ${f.sps}/s · ` : ''}scale ${smart(scale(s), 4)} N/count`; },
    },
  ],

  tile: { readouts: ['Load', 'Noise (σ, 3 s)', 'Drift (30 s)'], view: 'short' },

  views: [
    { id: 'short', label: '30 s', title: 'Load', sub: 'Mass equivalent of the force on the cell', legend: [{ key: 'g', label: 'Load', color: '--s-sensor' }], draw: drawLoad(30) },
    { id: 'long', label: '2 min', title: 'Load', sub: 'Longer window for spotting drift', legend: [{ key: 'g', label: 'Load', color: '--s-sensor' }], draw: drawLoad(120) },
  ],

  steps: [tareStep, scaleStep, verifyStep],

  checks(s, ctx) {
    const out = [];
    const p = streamProblem(s);
    if (p) {
      out.push({ id: 'stream', level: 'bad', ...p, action: p.modeD ? { label: 'Send MODE D', run: () => ctx.actions.command('MODE D') } : null });
      return out;
    }
    const load = loadG(s), noise = noiseG(s), drift = driftG(s), raw = rawCounts(s), sc = scale(s);
    if (Math.abs(load) > CELL.capacityG) {
      out.push({ id: 'overload', level: 'bad', title: `Over capacity: ${fixed(load, 0)} g`, detail: `The cell is rated for ${CELL.capacityG} g. Remove the load before it deforms permanently.` });
    }
    const fe = frontend(s), signal = raw - (fe ? fe.zero : 0);
    if (Math.abs(signal) > 0.95 * CELL.adcFullScale) {
      out.push({ id: 'saturated', level: 'bad', title: 'ADC is at full scale', detail: 'Raw counts are at the 24-bit limit, so the reading is clipped. Check the cell wiring (E+/E−/A+/A−) and that nothing is pressing on it.' });
    } else if (fe && isNum(signal) && Math.abs(signal) < 3000 && !loadChanged(s)) {
      // A connected bridge almost always sits thousands of counts off zero; an unconnected one reads the ADC's own zero.
      out.push({
        id: 'no-signal', level: 'warn', title: `Raw sits at the ${fe.chip}'s zero (${signed(signal, 0)} counts)`,
        detail: 'This is what the amplifier reads with no bridge connected. If pressing the cell does not move Raw by thousands of counts, the cell is not reaching the amplifier: check the four wires are bare copper in the terminal (red E+, black E−, white A−, green A+) and not broken where they enter the bar.',
      });
    }
    if (isNum(noise) && noise > CELL.noiseWarnG) {
      out.push({
        id: 'noise', level: noise > CELL.noiseBadG ? 'bad' : 'warn', title: `Noisy: σ ${fixed(noise, 2)} g`,
        detail: fanOn(s)
          ? 'The fan is on, so turbulence on the plate is included. Check again with the fan off.'
          : `Expect under ${CELL.noiseWarnG} g. Check the four cell wires are tight in the terminal, keep the Qwiic cable short and away from the motor, and make sure nothing touches the plate.`,
      });
    }
    if (S.taredAt && !fanOn(s) && isNum(drift) && Math.abs(drift) > 1) {
      out.push({
        id: 'drift', level: 'warn', title: `Zero drifting ${signed(drift, 1)} g in 30 s`,
        detail: 'Strain gauges drift while they warm up. Leave the board powered for about 10 minutes, then tare again just before calibrating.',
        action: { label: 'Tare again', run: () => { ctx.actions.command('TARE'); S.taredAt = new Date(); } },
      });
    }
    if (S.taredAt && load < -5 && !fanOn(s)) {
      out.push({ id: 'negative', level: 'warn', title: 'Reads negative', detail: 'If pressing down on the cell makes the reading fall, swap the green (A+) and white (A−) wires at the terminal, then tare again.' });
    }
    if (isNum(sc) && (sc < SCALE_RANGE[0] * 0.8 || sc > SCALE_RANGE[1] * 1.25)) {
      out.push({
        id: 'scale-range', level: 'warn', title: `Scale does not look like a ${CELL.capacityG / 1000} kg cell`,
        detail: `The board uses ${smart(sc, 3)} N/count. A ${CELL.capacityG / 1000} kg cell rated ${CELL.ratedMvPerV.join('–')} mV/V at gain ${CELL.gain} gives ${smart(SCALE_RANGE[0], 2)} to ${smart(SCALE_RANGE[1], 2)}. Run "Scale with known masses".`,
      });
    } else if (!S.scaledAt) {
      out.push({ id: 'unscaled', level: 'info', title: 'Scale not checked in this session', detail: 'The board is using the scale it booted with. Run step 2 whenever the cell or its mount has been touched.' });
    }
    return out;
  },

  info: {
    summary: 'The bar cell is a strain-gauge bridge. The amplifier excites it and reads it at gain 128: the M5Stack Weight I2C unit (HX711) 10 times a second, or the Qwiic Scale (NAU7802) 320 times; the firmware uses whichever answers on I²C and holds the last reading between conversions. Either way F = (counts − offset) × scale: tare sets the offset, known masses set the scale. Swapping amplifiers changes both, so tare and scale again after a swap.',
    brief: 'SparkFun Qwiic Scale (NAU7802) and a strain-gauge load cell (the kit lists 5 kg; this rig uses a 1 kg cell, so drag at 2 m/s is a few millinewtons: lever ratio and tare discipline matter more than electronics).',
    source: 'Project brief · docs/hardware.md · firmware/lib/drivers/src/load_cell.cpp',
    parts: [
      { name: 'M5Stack Weight I2C (default)', role: 'HX711 bridge ADC behind its own MCU', connect: 'I²C 0x26 · D14/D15', spec: 'Gain 128, 10 SPS; raw counts read, its filters bypassed' },
      { name: 'NAU7802 (Qwiic Scale, alternative)', role: '24-bit bridge ADC', connect: 'I²C 0x2A · D14/D15', spec: 'Gain 128, 320 SPS, LDO 3.3 V' },
      { name: 'Bar load cell', role: 'Strain-gauge bridge', connect: 'E+ red, E− black, A+ green, A− white', spec: '1 kg; about 9.1e-6 N/count here' },
      { name: 'Drag plate', role: 'Turns airflow into force', connect: 'On the cell\'s free end', spec: 'F = ½ρ·Cd·A·v²' },
    ],
    test: [
      'Run the I²C scan: the Weight I2C unit answers at 0x26 (or the NAU7802 at 0x2A).',
      'Empty cell: the reading is steady to well under 0.5 g after a tare.',
      'Hang 0, 200, 500 and 800 g: each reads within 0.5 %.',
      'Press gently and release: it returns to zero.',
    ],
    commands: [
      { label: 'Check the bus', cmd: 'cd firmware && pio run -e i2c_scan -t upload && pio device monitor -b 115200' },
      { label: 'Stand-alone load cell test (t = tare, k <g> = scale)', cmd: 'cd firmware && pio run -e loadcell_test -t upload && pio device monitor -b 115200' },
      { label: 'Back to the competition build', cmd: 'cd firmware && pio run -e airspeed_drag_plate -t upload' },
    ],
  },
};
