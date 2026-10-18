// Environment: five thermometers fused by inverse variance, LPS22DF pressure and SHT40 humidity give the air
// density ρ that the airspeed uses (firmware/lib/drivers/src/env_sensors.cpp). The server polls the board's
// `ENV` command once a second; this page reads it from telemetry.env.
import { h } from '../core/dom.js';
import { fixed, signed, isNum } from '../core/format.js';
import { asyncButton, numberInput, table, kv } from '../core/ui.js';

// σ (°C) are the fusion weights in env_sensors.cpp; `tolerance` is when a sensor is called out of line.
const THERMOMETERS = [
  { key: 'mcp9808', name: 'MCP9808', where: 'Qwiic breakout', sigma: 0.25, color: '--s-1' },
  { key: 'sht40', name: 'SHT40', where: 'IKS4A1 shield', sigma: 0.2, color: '--s-2' },
  { key: 'stts22h', name: 'STTS22H', where: 'IKS4A1 shield', sigma: 0.5, color: '--s-3' },
  { key: 'lps22df', name: 'LPS22DF', where: 'IKS4A1 barometer die', sigma: 1.5, color: '--s-4' },
  { key: 'tmp36', name: 'TMP36', where: 'analog, PC2', sigma: 2.0, color: '--s-5' },
].map((t) => ({ ...t, tolerance: Math.max(0.5, 2 * t.sigma) }));
const RHO_RANGE = [1.1, 1.3];
const PRESSURE_RANGE_HPA = [950, 1060];
const SETTLE_WINDOW_S = 120;
const SETTLE_LIMIT_C = 0.1;

const S = { ref: null };

const env = (s) => {
  const e = s.env;
  return e && e.age_s < 5 ? e : null;
};
const pick = (s, k) => env(s)?.[k] ?? NaN;
const hpa = (s) => pick(s, 'p') / 100;

function settleDrift(s) {
  const hist = s.envHistory;
  if (hist.length < 2) return { span: 0, drift: NaN };
  const t1 = hist[hist.length - 1].t, first = hist.find((r) => r.t >= t1 - SETTLE_WINDOW_S);
  return { span: t1 - hist[0].t, drift: hist[hist.length - 1].t_fused - first.t_fused, minutes: (t1 - first.t) / 60 };
}

function historyChart(lines, unit, opts = {}) {
  return (chart, s, hidden) => {
    const hist = s.envHistory, n = hist.length;
    const t = Float64Array.from(hist, (r) => r.t);
    const windowS = Math.min(10 * 60, Math.max(60, n > 1 ? t[n - 1] - t[0] : 0));
    let i0 = 0;
    while (i0 < n && t[i0] < (t[n - 1] ?? 0) - windowS) i0++;
    chart.draw({
      t, i0, window: windowS, unit, minSpan: opts.minSpan,
      emptyText: env(s) ? 'Collecting readings (one per second)…' : 'No environment readings',
      lines: lines.filter((l) => !hidden.has(l.key)).map((l) => ({
        label: l.label, color: l.color, width: l.width, dash: l.dash, digits: l.digits ?? 2,
        data: Float64Array.from(hist, (r) => (isNum(r[l.key]) ? r[l.key] : NaN)),
      })),
    });
  };
}

const TEMP_LINES = [
  { key: 't_fused', label: 'Fused', color: '--s-truth', width: 2.5 },
  ...THERMOMETERS.map((t) => ({ key: t.key, label: t.name, color: t.color, width: 1.5 })),
];
const RHO_LINES = [{ key: 'rho', label: 'Air density', color: '--s-sensor', digits: 4 }];

function thermometerTable(s) {
  const e = env(s);
  if (!e) return h('p.empty', 'No readings yet. They arrive once a second while the board is connected.');
  const fused = e.t_fused;
  const rows = THERMOMETERS.map((t) => {
    const v = e[t.key];
    const dev = isNum(v) && isNum(fused) ? v - fused : NaN;
    const refDev = S.ref && isNum(v) ? v - S.ref.tC : NaN;
    const status = !isNum(v) ? { v: 'No reading', cls: 'bad' }
      : Math.abs(dev) > t.tolerance ? { v: 'Out of line', cls: 'warn' } : { v: 'OK', cls: 'ok' };
    return [
      h('span', h('strong', t.name), h('span.faint', ` · ${t.where}`)),
      `± ${t.sigma} °C`,
      isNum(v) ? `${fixed(v, 2)} °C` : '–',
      { v: isNum(dev) ? signed(dev, 2) : '–', cls: Math.abs(dev) > t.tolerance ? 'warn' : '' },
      ...(S.ref ? [{ v: isNum(refDev) ? signed(refDev, 2) : '–', cls: Math.abs(refDev) > t.tolerance ? 'warn' : '' }] : []),
      status,
    ];
  });
  return table(['Sensor', 'Spec', 'Reading', 'vs fused', ...(S.ref ? ['vs reference'] : []), 'Status'], rows);
}

// ------------------------------------------------------------------------------------------ steps
const settleStep = {
  id: 'settle',
  title: 'Let the temperatures settle',
  why: 'The boards warm up after power-on, and a hand or the fan near the shield moves the readings. Wait until the fused temperature changes by less than 0.1 °C over two minutes.',
  state(s) {
    if (!env(s)) return { tone: 'blocked', text: 'No readings' };
    const d = settleDrift(s);
    if (d.span < SETTLE_WINDOW_S) return { tone: 'todo', text: `Watching, ${Math.floor(d.span)} of ${SETTLE_WINDOW_S} s` };
    if (Math.abs(d.drift) < SETTLE_LIMIT_C) return { tone: 'done', text: `Steady (${signed(d.drift, 2)} °C in 2 min)` };
    return { tone: 'todo', text: `Changing ${signed(d.drift, 2)} °C in 2 min` };
  },
  body: () => h('p.faint', 'Nothing to do here: this step completes by itself once the readings are steady.'),
};

const referenceStep = {
  id: 'reference',
  title: 'Compare with a reference',
  why: 'Read a trusted thermometer next to the board, and the lab barometer or a nearby weather station\'s station pressure (not the sea-level figure). The firmware has no per-sensor offsets: this step tells you which sensors to trust and how far ρ, and so the airspeed, could be off.',
  key: () => String(!!S.ref),
  state(s) {
    if (!S.ref) return { tone: 'todo', text: '' };
    const r = refResult(s);
    return r.ok ? { tone: 'done', text: `ρ within ${fixed(Math.abs(r.rhoErr) * 100, 2)} %` } : { tone: 'blocked', text: `ρ off ${signed(r.rhoErr * 100, 2)} %` };
  },
  body(ctx) {
    const temp = numberInput({ value: S.ref?.tC ?? '', label: 'Reference temperature in °C' });
    const press = numberInput({ value: S.ref?.pHpa ?? '', label: 'Reference pressure in hPa' });
    const r = S.ref && refResult(ctx.store);
    return h('div.stack',
      h('div.row.end',
        h('div.field', h('label', 'Temperature (°C)'), temp),
        h('div.field', h('label', 'Pressure (hPa, optional)'), press),
        asyncButton('Compare', async () => {
          const tC = Number(temp.value), pHpa = press.value === '' ? NaN : Number(press.value);
          if (!isNum(tC) || temp.value === '') throw new Error('Enter the reference temperature.');
          if (isNum(pHpa) && (pHpa < 800 || pHpa > 1100)) throw new Error('Pressure is in hPa, about 1000.');
          S.ref = { tC, pHpa, at: new Date() };
          ctx.refresh();
        }, { primary: true })),
      r && h(`div.result${r.ok ? '.ok' : '.warn'}`,
        h('strong', r.ok ? 'Density is within 0.3 %: good enough for the airspeed.' : 'Density error over 0.3 %: check the sensors listed below.'),
        kv([
          ['Fused temperature', `${signed(r.tErr, 2)} °C vs reference`],
          isNum(r.pErr) && ['Pressure', `${signed(r.pErr, 1)} hPa vs reference`],
          ['Air density', `${signed(r.rhoErr * 100, 2)} %`],
          ['Airspeed effect', `${signed(-r.rhoErr * 50, 2)} % (v ∝ 1/√ρ)`],
        ])));
  },
};

function refResult(s) {
  const tC = pick(s, 't_fused'), p = hpa(s);
  const tErr = tC - S.ref.tC, pErr = isNum(S.ref.pHpa) ? p - S.ref.pHpa : NaN;
  // ρ ∝ p / T: relative error ≈ Δp/p − ΔT/T
  const rhoErr = (isNum(pErr) ? pErr / S.ref.pHpa : 0) - tErr / (S.ref.tC + 273.15);
  return { tErr, pErr, rhoErr, ok: Math.abs(rhoErr) < 0.003 };
}

// ----------------------------------------------------------------------------------------- module
export default {
  id: 'environment',
  title: 'Environment',
  part: 'MCP9808, SHT40, STTS22H, LPS22DF and TMP36 · pressure and humidity',
  intro: 'Air density ρ comes from these sensors and scales every airspeed reading. Check that each one agrees with the others, and with a reference if you have one.',
  allClear: 'All five thermometers agree and the air density is plausible.',

  status(s) {
    if (s.conn !== 'open') return { tone: 'idle', text: 'Offline' };
    if (!env(s)) return { tone: 'bad', text: 'No readings' };
    const rho = pick(s, 'rho');
    if (!(rho >= RHO_RANGE[0] && rho <= RHO_RANGE[1])) return { tone: 'bad', text: 'Density implausible' };
    const missing = THERMOMETERS.filter((t) => !isNum(pick(s, t.key))).length;
    const off = THERMOMETERS.filter((t) => Math.abs(pick(s, t.key) - pick(s, 't_fused')) > t.tolerance).length;
    if (missing || off) return { tone: 'warn', text: missing ? `${missing} sensor${missing > 1 ? 's' : ''} missing` : `${off} out of line` };
    return { tone: 'ok', text: 'Healthy' };
  },

  readouts: [
    {
      label: 'Air density', unit: 'kg/m³', digits: 4, value: (s) => pick(s, 'rho'),
      tone: (s) => { const r = pick(s, 'rho'); return isNum(r) && (r < RHO_RANGE[0] || r > RHO_RANGE[1]) ? 'bad' : null; },
      note: (s) => (isNum(pick(s, 'rh')) ? 'humid air' : env(s) ? 'dry air: no humidity' : ''),
    },
    { label: 'Temperature', unit: '°C', digits: 2, value: (s) => pick(s, 't_fused'), note: (s) => (isNum(pick(s, 'sigma')) ? `fused, ± ${fixed(pick(s, 'sigma'), 2)} °C` : '') },
    { label: 'Pressure', unit: 'hPa', digits: 1, value: hpa, note: () => 'LPS22DF' },
    { label: 'Humidity', unit: '%', digits: 1, value: (s) => pick(s, 'rh'), note: () => 'SHT40' },
  ],

  views: [
    { id: 'temps', label: 'Temperatures', title: 'Temperatures', sub: 'Up to the last 10 minutes, one reading per second', legend: TEMP_LINES, draw: historyChart(TEMP_LINES, '°C', { minSpan: 0.5 }) },
    { id: 'rho', label: 'Air density', title: 'Air density', sub: 'Up to the last 10 minutes', legend: RHO_LINES, draw: historyChart(RHO_LINES, 'kg/m³', { minSpan: 0.002 }) },
  ],

  panels: [{
    title: 'Thermometers',
    sub: 'Fused by inverse variance: a sensor with a larger spec σ counts for less.',
    render: (ctx) => thermometerTable(ctx.store),
    update(body, s) {
      const sig = JSON.stringify([env(s) && THERMOMETERS.map((t) => fixed(pick(s, t.key), 2)), S.ref?.tC]);
      if (body.dataset.sig === sig) return;
      body.dataset.sig = sig;
      body.replaceChildren(thermometerTable(s));
    },
  }],

  steps: [settleStep, referenceStep],

  checks(s) {
    const out = [];
    if (s.conn !== 'open') return [{ id: 'server', level: 'bad', title: 'Calibration server not reachable', detail: 'Start it with `python -m calib serve` in calibration/, then reload this page.' }];
    if (!s.streaming) return [{ id: 'stream', level: 'bad', title: 'No readings from the board', detail: 'Open Connection (top right) and connect the Nucleo, or start the simulator.' }];
    if (!env(s)) return [{ id: 'env', level: 'bad', title: 'The board is not answering ENV', detail: 'Flash the current airspeed_drag_plate build, and check the IKS4A1 shield is fully seated on the Nucleo headers.' }];
    const fused = pick(s, 't_fused'), rho = pick(s, 'rho'), p = hpa(s);
    if (!isNum(p)) {
      out.push({ id: 'pressure', level: 'bad', title: 'No pressure reading', detail: 'Without the LPS22DF the density falls back to standard air (1.204 kg/m³), which can be 3 % off. Re-seat the IKS4A1 shield.' });
    } else if (p < PRESSURE_RANGE_HPA[0] || p > PRESSURE_RANGE_HPA[1]) {
      out.push({ id: 'pressure-range', level: 'warn', title: `Pressure ${fixed(p, 0)} hPa looks wrong`, detail: 'Room pressure is about 980–1040 hPa. Check the LPS22DF reading in Pa is being converted correctly.' });
    }
    if (isNum(rho) && (rho < RHO_RANGE[0] || rho > RHO_RANGE[1])) {
      out.push({ id: 'rho', level: 'bad', title: `Air density ${fixed(rho, 3)} kg/m³ is implausible`, detail: 'A room is about 1.15–1.25 kg/m³. ρ feeds straight into the airspeed, so fix the temperature or pressure reading first.' });
    }
    for (const t of THERMOMETERS) {
      const v = pick(s, t.key);
      if (!isNum(v)) {
        const why = t.key === 'sht40' ? 'Humidity is lost too, so the density assumes dry air (up to 1 % off).'
          : t.key === 'lps22df' ? 'This is also the pressure sensor; see above.'
            : 'The fusion carries on with the others.';
        out.push({ id: `missing-${t.key}`, level: 'warn', title: `${t.name} is not reading`, detail: `${why} Check the ${t.where} connection.` });
      } else if (isNum(fused) && Math.abs(v - fused) > t.tolerance) {
        out.push({
          id: `off-${t.key}`, level: 'warn', title: `${t.name} reads ${signed(v - fused, 2)} °C from the fused value`,
          detail: `More than its ${t.tolerance} °C tolerance. Usually self-heating from nearby parts or poor contact with the air. The fusion gives it weight 1/σ² so the effect is small; if it persists, leave it out of the fusion in env_sensors.cpp.`,
        });
      }
    }
    if (pick(s, 'sigma') > 0.3) {
      out.push({ id: 'sigma', level: 'warn', title: `Fused temperature uncertain (± ${fixed(pick(s, 'sigma'), 2)} °C)`, detail: 'Only the less accurate sensors are reporting. Check the MCP9808 and SHT40 connections.' });
    }
    if (S.ref) {
      const r = refResult(s);
      if (!r.ok) {
        out.push({ id: 'ref', level: 'warn', title: `Density ${signed(r.rhoErr * 100, 2)} % off the reference`, detail: `That is ${signed(-r.rhoErr * 50, 2)} % on every airspeed reading. If one sensor dominates the error (see the table), it is the one to fix.` });
      }
    }
    return out;
  },

  info: {
    summary: 'Air density ρ scales the drag-plate formula, so a 1 °C error is about 0.17 % in speed. Five thermometers are fused by inverse variance; the LPS22DF gives pressure and the SHT40 humidity. The server asks the board for ENV once a second.',
    brief: 'TMP36 (analog temperature) and MCP9808 (I²C temperature) for density compensation; the X-NUCLEO-IKS4A1 adds the barometer.',
    source: 'Project brief · docs/hardware.md · firmware/lib/drivers/src/env_sensors.cpp',
    parts: [
      { name: 'MCP9808', role: 'Thermometer, most accurate', connect: 'I²C 0x18 (A2..A0 = 000)', spec: '± 0.25 °C; mount in the free stream' },
      { name: 'SHT40', role: 'Humidity and temperature', connect: 'I²C 0x44 · IKS4A1', spec: '± 0.2 °C, ± 1.8 %RH' },
      { name: 'STTS22H', role: 'Thermometer', connect: 'I²C 0x38 · IKS4A1', spec: '± 0.5 °C' },
      { name: 'LPS22DF', role: 'Barometer (+ die temperature)', connect: 'I²C 0x5D · IKS4A1', spec: '260–1260 hPa; die temp ± 1.5 °C' },
      { name: 'TMP36', role: 'Analog thermometer', connect: 'PC2 · ADC1_IN12', spec: '10 mV/°C, 750 mV at 25 °C, ± 2 °C' },
    ],
    test: [
      'Run the I²C scan: 0x18, 0x38, 0x44 and 0x5D answer.',
      'Power up and wait two minutes for the boards to warm.',
      'All five agree within their tolerance; the TMP36 is the loosest.',
      'Compare with a trusted thermometer: density within 0.3 %.',
    ],
    commands: [
      { label: 'Every sensor as CSV at 4 Hz', cmd: 'cd firmware && pio run -e env_monitor -t upload && pio device monitor -b 115200' },
      { label: 'Back to the competition build', cmd: 'cd firmware && pio run -e airspeed_drag_plate -t upload' },
    ],
  },
};
