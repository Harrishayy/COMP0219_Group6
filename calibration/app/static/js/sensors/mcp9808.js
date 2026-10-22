// MCP9808: the most accurate thermometer in the kit (± 0.25 °C typical), on I²C1 at 0x18, and by default the
// one the board takes the air temperature (and so ρ) from. The board reads it four times a second inside
// EnvSensors; the server's 1 Hz `ENV` request brings its raw reading here. Its own tab: wire it (Wiring), prove
// the reading is it, settle, compare, then calibrate it against a reference (SET mcp9808_offset).
import { fixed, signed, isNum } from '../core/format.js';
import wiring from '../wiring/parts/mcp9808.js';
import { envNow, envVal, histStats, historyView, wireStep, pinchStep, settleStep, compareStep, calibrateStep, boardOffset, calibrated, feedsRho } from './thermometer-kit.js';

const KEY = 'mcp9808';
const S = { pinch: null, pinchOk: null, cal: null };
const LIMITS = { offSht40: 0.5, noise: 0.05, hot: 35 };

const notFound = 'The board does not find it at 0x18. Check SDA (PB9) and SCL (PB8) are not swapped, VDD is on 3V3, and A0–A2 are left open. The board looks for it again every 2 s, so it appears as soon as the wiring is right.';

export default {
  id: 'mcp9808',
  title: 'MCP9808',
  part: 'Microchip MCP9808 · I²C1 0x18 · ± 0.25 °C typ, 0.0625 °C steps',
  intro: 'The thermometer the air density comes from: wire it to the I²C bus, prove the reading is it, then calibrate it against a reference thermometer.',
  allClear: 'The MCP9808 reads and is quiet.',

  status(s) {
    if (s.conn !== 'open') return { tone: 'idle', text: 'Offline' };
    if (!s.streaming) return { tone: 'idle', text: 'No live data' };
    if (!envNow(s)) return { tone: 'bad', text: 'No ENV reply' };
    if (!isNum(envVal(s, KEY))) return { tone: 'bad', text: 'Not found' };
    const sht = envVal(s, 'sht40');
    if (isNum(sht) && Math.abs(histStats(s, KEY, 30).mean - histStats(s, 'sht40', 30).mean) > LIMITS.offSht40) return { tone: 'warn', text: 'Out of line' };
    return { tone: 'ok', text: 'Reading' };
  },

  readouts: [
    { label: 'Raw', unit: '°C', digits: 2, value: (s) => envVal(s, KEY), note: () => 'as read, 4 Hz on the board' },
    {
      label: 'Calibrated', unit: '°C', digits: 2, value: (s) => calibrated(s, KEY),
      note: (s) => `${isNum(boardOffset(s, KEY)) ? `offset ${signed(boardOffset(s, KEY), 2)} °C` : 'no offset on the board'}${feedsRho(s, KEY) ? ' · feeds ρ' : ''}`,
    },
    {
      label: 'vs SHT40', unit: '°C', format: (v) => signed(v, 2), value: (s) => envVal(s, KEY) - envVal(s, 'sht40'),
      tone: (s) => (Math.abs(envVal(s, KEY) - envVal(s, 'sht40')) > LIMITS.offSht40 ? 'warn' : null),
      note: (s) => (isNum(envVal(s, 'sht40')) ? 'IKS4A1, ± 0.2 °C' : 'SHT40 not reading'),
    },
    {
      label: 'Noise σ', unit: '°C', digits: 3, value: (s) => histStats(s, KEY, 60).sd,
      tone: (s) => (histStats(s, KEY, 60).sd > LIMITS.noise ? 'warn' : null), note: () => 'last 60 s',
    },
  ],

  views: [
    historyView({
      id: 'temps', label: 'Temperature', title: 'MCP9808 against its neighbours', sub: 'Up to the last 10 minutes, one reading per second',
      lines: [
        { key: KEY, label: 'MCP9808', color: '--s-sensor', width: 2.5 },
        { key: 'sht40', label: 'SHT40', color: '--s-2', width: 1.5 },
        { key: 't_air', label: 'Air (ρ)', color: '--s-truth', width: 1.5 },
      ],
    }),
  ],

  steps: [
    wireStep({ key: KEY, name: 'MCP9808', hint: () => notFound }),
    pinchStep({ key: KEY, name: 'MCP9808', S }),
    settleStep({ key: KEY, name: 'MCP9808' }),
    compareStep({ key: KEY, name: 'MCP9808', refs: [{ key: 'sht40', name: 'SHT40', tol: LIMITS.offSht40 }, { key: 'stts22h', name: 'STTS22H', tol: 0.75 }] }),
    calibrateStep({ key: KEY, name: 'MCP9808', S }),
  ],

  checks(s) {
    if (s.conn !== 'open') return [{ id: 'server', level: 'bad', title: 'Calibration server not reachable', detail: 'Start it with pnpm dev, then reload this page.' }];
    if (!s.streaming) return [{ id: 'stream', level: 'bad', title: 'No live data from the board', detail: 'See the banner at the top: plug in the Nucleo, or flash the competition build.' }];
    if (!envNow(s)) return [{ id: 'env', level: 'bad', title: 'The board is not answering ENV', detail: 'Flash the current airspeed_drag_plate build.' }];
    const v = envVal(s, KEY), out = [];
    if (!isNum(v)) {
      return [{ id: 'missing', level: 'bad', title: 'MCP9808 not found at 0x18', detail: notFound, action: { label: 'Open the wiring', run: (ctx) => ctx.show('wiring') } }];
    }
    const d = histStats(s, KEY, 30).mean - histStats(s, 'sht40', 30).mean;
    if (isNum(d) && Math.abs(d) > LIMITS.offSht40) {
      out.push({ id: 'off', level: 'warn', title: `Reads ${signed(d, 2)} °C from the SHT40`, detail: 'Together they should agree within 0.5 °C. Usually a warm spot: keep the breakout away from the Nucleo regulator and out of your breath, and let both settle.' });
    }
    if (v > LIMITS.hot) out.push({ id: 'hot', level: 'warn', title: `Reads ${fixed(v, 1)} °C`, detail: 'Hotter than a lab. Is it sitting on a warm board or in sunlight?' });
    const sd = histStats(s, KEY, 60).sd;
    if (sd > LIMITS.noise) out.push({ id: 'noise', level: 'warn', title: `Jumpy: σ ${fixed(sd, 3)} °C over a minute`, detail: 'The MCP9808 should be steady to a few hundredths. Moving air or a hand nearby does this; so does a loose ground wire.' });
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
    summary: 'A digital thermometer with ± 0.25 °C typical (± 0.5 °C max) accuracy from −20 to 100 °C. The firmware sets 0.0625 °C resolution (250 ms per conversion) and reads it at 4 Hz. With temp_source 0 (the default) its reading plus mcp9808_offset is the air temperature the density uses; no other thermometer is mixed in.',
    brief: 'MCP9808 (I²C temperature) for density compensation.',
    source: 'MCP9808 datasheet · docs/hardware.md · firmware/lib/drivers/src/mcp9808.cpp',
    parts: [
      { name: 'MCP9808 breakout', role: 'Thermometer', connect: 'I²C1 0x18 · SDA PB9, SCL PB8', spec: '2.7–5.5 V; ± 0.25 °C typ' },
      { name: 'VDD', role: 'Supply', connect: '3V3 (CN7 pin 16)', spec: 'Keeps the bus at 3.3 V' },
      { name: 'A0–A2', role: 'Address', connect: 'Open (pulled low)', spec: '0x18; 0x19 clashes with the IKS4A1' },
    ],
    test: [
      'Wire it as on the Wiring tab, USB unplugged.',
      'Flash i2c_scan: 0x18 answers alongside the IKS4A1 and NAU7802.',
      'Flash airspeed_drag_plate: this tab shows a temperature.',
      'Pinch it: the reading rises within seconds.',
      'Settled, it agrees with the SHT40 within 0.5 °C.',
      'Next to a reference thermometer, set the offset; Export so it is in the header.',
    ],
    commands: [
      { label: 'Find every I²C device', cmd: 'cd firmware && pio run -e i2c_scan -t upload && pio device monitor -b 115200' },
      { label: 'Every thermometer as CSV at 4 Hz', cmd: 'cd firmware && pio run -e env_monitor -t upload && pio device monitor -b 115200' },
      { label: 'Back to the competition build', cmd: 'cd firmware && pio run -e airspeed_drag_plate -t upload' },
    ],
  },
};
