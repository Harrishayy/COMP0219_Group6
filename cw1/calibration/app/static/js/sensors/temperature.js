// Temperature and air density: the MCP9808 (± 0.25 °C typical, I²C1 0x18) plus its offset is the air
// temperature; with the LPS22DF pressure (or the constant pressure_ref_pa) and the SHT40 humidity it gives the
// density ρ that every airspeed reading is divided by (firmware/lib/drivers/src/env_sensors.cpp). The server
// asks the board for `ENV` once a second. Steps: wire it, prove the reading is it, settle, calibrate it against
// a reference (SET mcp9808_offset), then set the pressure and check ρ.
import { h } from '../core/dom.js';
import { fixed, signed, isNum } from '../core/format.js';
import { asyncButton, numberInput, kv } from '../core/ui.js';
import wiring from '../wiring/parts/mcp9808.js';
import {
  THERMOMETER_KEYS, envNow, envVal, histStats, historyView, wireStep, pinchStep, settleStep, calibrateStep,
  boardOffset, calibrated, feedsRho,
} from './thermometer-kit.js';

const KEY = 'mcp9808';
const S = { pinch: null, pinchOk: null, cal: null, ref: null };
const LIMITS = { noise: 0.05, hot: 35 };
const RHO_RANGE = [1.1, 1.3];
const PRESSURE_RANGE_HPA = [950, 1060];
const STD_HPA = 1013.25;

const notFound = 'The board does not find it at 0x18. Check SDA (PB9) and SCL (PB8) are not swapped, VIN (VDD) is on 3V3, and A0–A2 are left open. The board looks for it again every 2 s, so it appears as soon as the wiring is right.';

// The barometer's reading when it has one, else the constant the board puts into ρ (pressure_ref_pa).
const measuredHpa = (s) => envVal(s, 'p') / 100;
const constHpa = (s) => (isNum(s.board?.pressure_ref_pa) ? s.board.pressure_ref_pa / 100 : NaN);
const hpa = (s) => (isNum(measuredHpa(s)) ? measuredHpa(s) : constHpa(s));
const rho = (s) => envVal(s, 'rho');

// ------------------------------------------------------------------------------------------ steps
const pressureStep = {
  id: 'pressure',
  title: 'Enter today\'s air pressure',
  why: 'No barometer is reading, so the board puts a constant pressure into ρ. Use the lab barometer or a nearby weather station\'s station pressure (not the sea-level figure): 10 hPa off is 1 % on ρ and 0.5 % on the airspeed.',
  key: (s) => `${constHpa(s)}|${isNum(measuredHpa(s))}`,
  state(s) {
    if (isNum(measuredHpa(s))) return { tone: 'skip', text: 'LPS22DF reads live' };
    if (!isNum(constHpa(s))) return { tone: 'blocked', text: 'Board too old' };
    return constHpa(s) === STD_HPA ? { tone: 'todo', text: `Standard ${STD_HPA} hPa` } : { tone: 'done', text: `${fixed(constHpa(s), 1)} hPa` };
  },
  body(ctx) {
    const s = ctx.store;
    if (isNum(measuredHpa(s))) return h('p.faint', 'The LPS22DF is reading, so the board uses its live pressure and ignores this constant.');
    if (!isNum(constHpa(s))) return h('p.faint', 'This board does not take a pressure setting yet: flash the current airspeed_drag_plate build.');
    const press = numberInput({ value: fixed(constHpa(s), 1), min: 500, max: 1200, label: 'Air pressure in hPa' });
    return h('div.stack',
      h('div.row.end',
        h('div.field', h('label', 'Pressure (hPa)'), press),
        asyncButton('Set on board', async () => {
          const v = Number(press.value);
          if (!press.value.trim() || !isNum(v) || v < 800 || v > 1100) throw new Error('Pressure is in hPa, about 1000.');
          ctx.actions.setOnBoard('pressure_ref_pa', Math.round(v * 100));
          ctx.toast(`Set ${fixed(v, 1)} hPa. Export the header to keep it after a reset.`);
          ctx.refresh();
        }, { primary: true })),
      kv([
        ['On the board now', `${fixed(constHpa(s), 1)} hPa`],
        ['Air density', `${fixed(rho(s), 4)} kg/m³ at ${fixed(envVal(s, 't_air'), 2)} °C`],
      ]));
  },
};

function refResult(s) {
  const tErr = envVal(s, 't_air') - S.ref.tC, pErr = isNum(S.ref.pHpa) ? hpa(s) - S.ref.pHpa : NaN;
  // ρ ∝ p / T: relative error ≈ Δp/p − ΔT/T
  const rhoErr = (isNum(pErr) ? pErr / S.ref.pHpa : 0) - tErr / (S.ref.tC + 273.15);
  return { tErr, pErr, rhoErr, ok: Math.abs(rhoErr) < 0.003 };
}

const densityStep = {
  id: 'density',
  title: 'Check the air density',
  why: 'With the offset set, compare the air temperature and pressure with a trusted reference once more. This tells you how far ρ, and so every airspeed reading, could be off.',
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
        h('strong', r.ok ? 'Density is within 0.3 %: good enough for the airspeed.' : 'Density error over 0.3 %: recalibrate the offset or check the pressure.'),
        kv([
          ['Air temperature', `${signed(r.tErr, 2)} °C vs reference`],
          isNum(r.pErr) && ['Pressure', `${signed(r.pErr, 1)} hPa vs reference`],
          ['Air density', `${signed(r.rhoErr * 100, 2)} %`],
          ['Airspeed effect', `${signed(-r.rhoErr * 50, 2)} % (v ∝ 1/√ρ)`],
        ])));
  },
};

// ----------------------------------------------------------------------------------------- module
export default {
  id: 'temperature',
  title: 'Temperature',
  part: 'MCP9808 · I²C1 0x18 · ± 0.25 °C typ  ·  LPS22DF pressure · SHT40 humidity → air density ρ',
  intro: 'The air temperature and density every airspeed reading is divided by. Wire the MCP9808, calibrate its offset against a reference thermometer, then set the pressure and check ρ.',
  allClear: 'The MCP9808 reads, is quiet, and the air density is plausible.',

  status(s) {
    if (s.conn !== 'open') return { tone: 'idle', text: 'Offline' };
    if (!s.streaming) return { tone: 'idle', text: 'No live data' };
    if (!envNow(s)) return { tone: 'bad', text: 'No ENV reply' };
    if (!isNum(envVal(s, KEY))) return { tone: 'bad', text: 'MCP9808 not found' };
    const r = rho(s);
    if (!(r >= RHO_RANGE[0] && r <= RHO_RANGE[1])) return { tone: 'bad', text: 'Density implausible' };
    if (!feedsRho(s, KEY)) return { tone: 'warn', text: 'ρ not from the MCP9808' };
    return { tone: 'ok', text: 'Reading' };
  },

  readouts: [
    {
      label: 'Air temperature', unit: '°C', digits: 2, value: (s) => calibrated(s, KEY),
      note: (s) => `${isNum(boardOffset(s, KEY)) ? `offset ${signed(boardOffset(s, KEY), 2)} °C` : 'no offset on the board'}${feedsRho(s, KEY) ? ' · feeds ρ' : ''}`,
    },
    { label: 'MCP9808 raw', unit: '°C', digits: 2, value: (s) => envVal(s, KEY), note: () => 'as read, 4 Hz on the board' },
    {
      label: 'Noise σ', unit: '°C', digits: 3, value: (s) => histStats(s, KEY, 60).sd,
      tone: (s) => (histStats(s, KEY, 60).sd > LIMITS.noise ? 'warn' : null), note: () => 'last 60 s',
    },
    {
      label: 'Air density', unit: 'kg/m³', digits: 4, value: rho,
      tone: (s) => { const r = rho(s); return isNum(r) && (r < RHO_RANGE[0] || r > RHO_RANGE[1]) ? 'bad' : null; },
      note: (s) => (isNum(envVal(s, 'rh')) ? 'humid air' : envNow(s) ? 'dry air: no humidity' : ''),
    },
    { label: 'Pressure', unit: 'hPa', digits: 1, value: hpa, note: (s) => (isNum(measuredHpa(s)) ? 'LPS22DF' : isNum(constHpa(s)) ? 'constant: no barometer' : '') },
    { label: 'Humidity', unit: '%', digits: 1, value: (s) => envVal(s, 'rh'), note: () => 'SHT40' },
  ],

  tile: { readouts: ['Air temperature', 'Air density', 'Pressure', 'Humidity'], view: 'temps' },

  views: [
    historyView({
      id: 'temps', label: 'Temperature', title: 'MCP9808', sub: 'Up to the last 10 minutes, one reading per second',
      lines: [{ key: KEY, label: 'MCP9808', color: '--s-sensor', width: 2.5 }],
    }),
    historyView({
      id: 'rho', label: 'Air density', title: 'Air density', sub: 'Up to the last 10 minutes', unit: 'kg/m³', minSpan: 0.002,
      lines: [{ key: 'rho', label: 'Air density', color: '--s-sensor', digits: 4 }],
    }),
  ],

  steps: [
    wireStep({ key: KEY, name: 'MCP9808', hint: () => notFound }),
    pinchStep({ key: KEY, name: 'MCP9808', S }),
    settleStep({ key: KEY, name: 'MCP9808' }),
    calibrateStep({ key: KEY, name: 'MCP9808', S }),
    pressureStep,
    densityStep,
  ],

  checks(s) {
    if (s.conn !== 'open') return [{ id: 'server', level: 'bad', title: 'Calibration server not reachable', detail: 'Start it with pnpm dev, then reload this page.' }];
    if (!s.streaming) return [{ id: 'stream', level: 'bad', title: 'No live data from the board', detail: 'See the banner at the top: plug in the Nucleo, or flash the competition build.' }];
    if (!envNow(s)) return [{ id: 'env', level: 'bad', title: 'The board is not answering ENV', detail: 'Flash the current airspeed_drag_plate build.' }];
    const v = envVal(s, KEY), out = [];
    if (!isNum(v)) {
      out.push({
        id: 'missing', level: 'bad', title: 'MCP9808 not found at 0x18',
        detail: `${notFound} Without it the board falls back to 15 °C, about 3 % on ρ and 2 % on the airspeed in a 25 °C room.`,
        action: { label: 'Open the wiring', run: (ctx) => ctx.show('wiring') },
      });
    } else {
      if (!feedsRho(s, KEY)) {
        out.push({ id: 'source', level: 'warn', title: 'The board takes ρ from another thermometer', detail: 'Point it back at the MCP9808, the most accurate one.',
          action: { label: 'Use the MCP9808', run: (ctx) => ctx.actions.setOnBoard('temp_source', THERMOMETER_KEYS.indexOf(KEY)) } });
      }
      if (v > LIMITS.hot) out.push({ id: 'hot', level: 'warn', title: `Reads ${fixed(v, 1)} °C`, detail: 'Hotter than a lab. Is it sitting on a warm board or in sunlight?' });
      const sd = histStats(s, KEY, 60).sd;
      if (sd > LIMITS.noise) out.push({ id: 'noise', level: 'warn', title: `Jumpy: σ ${fixed(sd, 3)} °C over a minute`, detail: 'The MCP9808 should be steady to a few hundredths. Moving air or a hand nearby does this; so does a loose ground wire.' });
    }
    const p = hpa(s), r = rho(s);
    if (!isNum(p)) {
      out.push({ id: 'pressure', level: 'bad', title: 'No pressure for the density', detail: 'The LPS22DF is not reading and this board has no pressure setting. Flash the current airspeed_drag_plate build.' });
    } else if (!isNum(measuredHpa(s)) && p === STD_HPA) {
      out.push({ id: 'pressure-std', level: 'warn', title: `ρ assumes the standard ${STD_HPA} hPa`, detail: 'No barometer is reading. Enter today\'s station pressure in step 5: the weather moves it by up to 3 %.' });
    } else if (p < PRESSURE_RANGE_HPA[0] || p > PRESSURE_RANGE_HPA[1]) {
      out.push({ id: 'pressure-range', level: 'warn', title: `Pressure ${fixed(p, 0)} hPa looks wrong`, detail: 'Room pressure is about 980–1040 hPa. Check the LPS22DF reading, or the value set in step 5.' });
    }
    if (isNum(r) && (r < RHO_RANGE[0] || r > RHO_RANGE[1])) {
      out.push({ id: 'rho', level: 'bad', title: `Air density ${fixed(r, 3)} kg/m³ is implausible`, detail: 'A room is about 1.15–1.25 kg/m³. ρ feeds straight into the airspeed, so fix the temperature or pressure first.' });
    }
    if (S.ref && !refResult(s).ok) {
      const e = refResult(s).rhoErr;
      out.push({ id: 'ref', level: 'warn', title: `Density ${signed(e * 100, 2)} % off the reference`, detail: `That is ${signed(-e * 50, 2)} % on every airspeed reading. Recalibrate the offset (step 4) and check the pressure.` });
    }
    return out;
  },

  wiring,
  wiringLive(s) {
    if (!s.streaming || !envNow(s)) return { tone: 'idle', text: 'No live data from the board yet.' };
    const v = envVal(s, KEY);
    return isNum(v) ? { tone: 'ok', label: 'Found', text: `The board reads it at 0x18: ${fixed(v, 2)} °C.` } : { tone: 'bad', label: 'Not found', text: notFound };
  },

  datasheet: {
    file: 'mcp9808.pdf', title: 'MCP9808 datasheet', source: 'Microchip DS20005095B',
    pages: [
      { page: 1, label: 'Features and pinout' },
      { page: 3, label: 'Electrical characteristics' },
      { page: 11, label: 'Pins and address table' },
      { page: 13, label: 'I²C timing' },
      { page: 17, label: 'Register map' },
      { page: 24, label: 'Ambient temperature register' },
      { page: 29, label: 'Resolution register' },
      { page: 35, label: 'Layout and self-heating' },
    ],
  },

  info: {
    summary: 'Air density ρ scales the drag-plate formula, so a 1 °C error is about 0.17 % in speed. The air temperature is the MCP9808 plus mcp9808_offset (temp_source 0, the default; no other thermometer is mixed in). The firmware reads it at 0.0625 °C resolution, 4 Hz. The LPS22DF gives pressure (without it, the constant pressure_ref_pa) and the SHT40 humidity.',
    brief: 'TMP36 (analog temperature) and MCP9808 (I²C temperature) for density compensation; the X-NUCLEO-IKS4A1 adds the barometer.',
    source: 'MCP9808 datasheet · docs/hardware.md · firmware/lib/drivers/src/env_sensors.cpp',
    parts: [
      { name: 'MCP9808 breakout', role: 'Air temperature', connect: 'I²C1 0x18 · SDA PB9, SCL PB8 · VIN 3V3', spec: '± 0.25 °C typ; A0–A2 open (0x19 clashes with the IKS4A1)' },
      { name: 'LPS22DF', role: 'Barometer', connect: 'I²C 0x5D · IKS4A1', spec: '260–1260 hPa' },
      { name: 'SHT40', role: 'Humidity', connect: 'I²C 0x44 · IKS4A1', spec: '± 1.8 %RH' },
    ],
    test: [
      'Flash i2c_scan: 0x18 answers alongside the load-cell amplifier.',
      'Pinch the MCP9808: the reading rises within seconds.',
      'Wait two minutes for the boards to warm, then set the offset against a reference thermometer.',
      'Compare with the reference again: density within 0.3 %. Export so the offset is in the header.',
    ],
    commands: [
      { label: 'Find every I²C device', cmd: 'cd firmware && pio run -e i2c_scan -t upload && pio device monitor -b 115200' },
      { label: 'Every environment sensor as CSV at 4 Hz', cmd: 'cd firmware && pio run -e env_monitor -t upload && pio device monitor -b 115200' },
      { label: 'Back to the competition build', cmd: 'cd firmware && pio run -e airspeed_drag_plate -t upload' },
    ],
  },
};
