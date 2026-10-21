// LM35: analog thermometer, 10 mV/°C from 0 V at 0 °C, on PC3 (ADC1_IN13) and powered from 5 V. The board reads
// it inside EnvSensors with the TMP36 and fuses it (σ 1 °C); `ENV` reports both its temperature and its output
// voltage, so a wiring fault shows as a voltage with a reason rather than a missing number.
import { fixed, signed, isNum } from '../core/format.js';
import wiring from '../wiring/parts/lm35.js';
import { envNow, envVal, histStats, historyView, wireStep, pinchStep, settleStep, compareStep } from './thermometer-kit.js';

const KEY = 'lm35';
const S = { pinch: null, pinchOk: null };
const LIMITS = { tol: 1.5, noise: 0.3, zeroV: 0.02, highV: 1.0 };
const FLASH = 'cd firmware && pio run -e airspeed_drag_plate -t upload';

/** What the output voltage says about the wiring, or null when it is a plausible room temperature. */
function diagnose(s) {
  const v = envVal(s, 'lm35_v');
  if (!isNum(v)) return null;
  if (v < LIMITS.zeroV) {
    return { level: 'bad', title: `PC3 reads ${fixed(v * 1000, 0)} mV`, detail: 'Nothing is driving PC3: the Vout wire is not in column 11, the LM35 has no supply (it needs the 5V pin; 3V3 is too low), or GND is missing. The 100 kΩ pull-down holds an unconnected PC3 at 0 V, so this is a wiring fault, not a cold room.' };
  }
  if (v > LIMITS.highV) {
    return { level: 'bad', title: `PC3 reads ${fixed(v, 2)} V, over 100 °C`, detail: 'Unplug the USB. PC3 may be on a supply, or the LM35 is in backwards. Check the yellow wire is in column 11 and the flat face points at the centre channel.' };
  }
  return null;
}

export default {
  id: 'lm35',
  title: 'LM35',
  part: 'TI LM35 · TO-92 · PC3 (ADC1_IN13) · 5 V supply · 10 mV/°C',
  intro: 'A second analog thermometer for the air density. Wire it from 5V, check the output voltage makes sense, prove the reading is it, then check it against the MCP9808.',
  allClear: 'The LM35 reads a sensible voltage and agrees with the MCP9808.',

  available(s) {
    const e = envNow(s);
    if (e && !(KEY in e)) {
      return {
        live: false, title: 'The running firmware does not read the LM35',
        detail: 'This board\'s ENV reply has no lm35 field: it runs a build from before the LM35 was added. Flash the current competition build; the Wiring and Datasheet tabs work meanwhile.',
        commands: [FLASH],
      };
    }
    return { live: true };
  },

  status(s) {
    if (s.conn !== 'open') return { tone: 'idle', text: 'Offline' };
    if (!s.streaming) return { tone: 'idle', text: 'No live data' };
    if (!envNow(s)) return { tone: 'bad', text: 'No ENV reply' };
    if (diagnose(s)) return { tone: 'bad', text: 'Wiring fault' };
    if (!isNum(envVal(s, KEY))) return { tone: 'bad', text: 'Not reading' };
    const ref = envVal(s, 'mcp9808');
    if (isNum(ref) && Math.abs(histStats(s, KEY, 30).mean - histStats(s, 'mcp9808', 30).mean) > LIMITS.tol) return { tone: 'warn', text: 'Out of line' };
    return { tone: 'ok', text: 'Reading' };
  },

  readouts: [
    { label: 'Temperature', unit: '°C', digits: 2, value: (s) => envVal(s, KEY), note: () => 'LM35, 4 Hz on the board' },
    {
      label: 'Output', unit: 'mV', digits: 1, value: (s) => envVal(s, 'lm35_v') * 1000,
      tone: (s) => (diagnose(s) ? 'bad' : null), note: (s) => (diagnose(s) ? 'wiring fault' : '10 mV per °C'),
    },
    {
      label: 'vs MCP9808', unit: '°C', format: (v) => signed(v, 2), value: (s) => envVal(s, KEY) - envVal(s, 'mcp9808'),
      tone: (s) => (Math.abs(envVal(s, KEY) - envVal(s, 'mcp9808')) > LIMITS.tol ? 'warn' : null),
      note: (s) => (isNum(envVal(s, 'mcp9808')) ? `tolerance ± ${LIMITS.tol} °C` : 'MCP9808 not reading'),
    },
    {
      label: 'Noise σ', unit: '°C', digits: 2, value: (s) => histStats(s, KEY, 60).sd,
      tone: (s) => (histStats(s, KEY, 60).sd > LIMITS.noise ? 'warn' : null), note: () => 'last 60 s',
    },
  ],

  views: [
    historyView({
      id: 'temps', label: 'Temperature', title: 'LM35 against the MCP9808', sub: 'Up to the last 10 minutes, one reading per second',
      lines: [
        { key: KEY, label: 'LM35', color: '--s-preview', width: 2.5 },
        { key: 'mcp9808', label: 'MCP9808', color: '--s-sensor', width: 1.5 },
        { key: 'tmp36', label: 'TMP36', color: '--s-5', width: 1.5 },
      ],
    }),
  ],

  steps: [
    wireStep({ key: KEY, name: 'LM35', hint: (s) => diagnose(s)?.detail || 'Waiting for a reading between 2 and 150 °C on PC3.' }),
    pinchStep({ key: KEY, name: 'LM35', S }),
    settleStep({ key: KEY, name: 'LM35' }),
    compareStep({ key: KEY, name: 'LM35', refs: [{ key: 'mcp9808', name: 'MCP9808', tol: LIMITS.tol }, { key: 'sht40', name: 'SHT40', tol: LIMITS.tol }] }),
  ],

  checks(s) {
    if (s.conn !== 'open') return [{ id: 'server', level: 'bad', title: 'Calibration server not reachable', detail: 'Start it with pnpm dev, then reload this page.' }];
    if (!s.streaming) return [{ id: 'stream', level: 'bad', title: 'No live data from the board', detail: 'See the banner at the top: plug in the Nucleo, or flash the competition build.' }];
    if (!envNow(s)) return [{ id: 'env', level: 'bad', title: 'The board is not answering ENV', detail: 'Flash the current airspeed_drag_plate build.' }];
    const out = [];
    const dx = diagnose(s);
    if (dx) out.push({ id: 'volts', ...dx, action: { label: 'Open the wiring', run: (ctx) => ctx.show('wiring') } });
    const d = histStats(s, KEY, 30).mean - histStats(s, 'mcp9808', 30).mean;
    if (isNum(d) && Math.abs(d) > LIMITS.tol) {
      out.push({
        id: 'off', level: 'warn', title: `Reads ${signed(d, 2)} °C from the MCP9808`,
        detail: d < 0 ? 'Low readings usually mean the LM35 is on 3V3 instead of 5V: it needs at least 4 V. Otherwise let both settle side by side.'
          : 'High readings usually mean self-heating from nearby parts or a warm hand. Let both settle side by side, away from the Nucleo.',
      });
    }
    const sd = histStats(s, KEY, 60).sd;
    if (sd > LIMITS.noise) out.push({ id: 'noise', level: 'warn', title: `Jumpy: σ ${fixed(sd, 2)} °C over a minute`, detail: 'Each ADC step is 0.08 °C. More than that is pickup on the Vout wire: keep it short, twist it with GND, or fit the 0.1 µF from +Vs to GND.' });
    return out;
  },

  wiring,
  wiringLive(s) {
    if (!s.streaming || !envNow(s)) return { tone: 'idle', text: 'No live data from the board yet.' };
    if (!(KEY in envNow(s))) return { tone: 'warn', label: 'Old firmware', text: 'This firmware does not read the LM35: flash airspeed_drag_plate.' };
    const dx = diagnose(s);
    if (dx) return { tone: 'bad', label: 'Fault', text: `${dx.title}. ${dx.detail}` };
    const v = envVal(s, KEY);
    return isNum(v) ? { tone: 'ok', label: 'Reading', text: `PC3 reads ${fixed(envVal(s, 'lm35_v') * 1000, 0)} mV: ${fixed(v, 2)} °C.` } : { tone: 'bad', label: 'Not reading', text: 'No reading on PC3 yet.' };
  },

  datasheet: {
    file: 'lm35.pdf', title: 'LM35 datasheet', source: 'Texas Instruments SNIS159H',
    pages: [
      { page: 1, label: 'Features and basic circuit' },
      { page: 3, label: 'Pinout (TO-92)' },
      { page: 4, label: 'Absolute maximum ratings' },
      { page: 8, label: 'Accuracy: LM35, LM35C, LM35D' },
      { page: 14, label: 'Driving capacitive loads' },
      { page: 15, label: 'Basic centigrade sensor' },
      { page: 19, label: 'Supply and layout' },
    ],
  },

  info: {
    summary: 'The output is 10 mV per °C from 0 V at 0 °C, so room temperature is about 0.2–0.3 V. The board reads it with 16× oversampling on the 12-bit ADC (about 0.08 °C per step), scaled by the measured VDDA, and fuses it into the air density with σ 1 °C.',
    brief: 'An extra analog thermometer alongside the TMP36 and MCP9808 for density compensation.',
    source: 'LM35 datasheet · docs/hardware.md · firmware/lib/drivers/src/lm35.cpp',
    parts: [
      { name: 'LM35 (TO-92)', role: 'Analog thermometer', connect: 'Vout → PC3 (CN7 pin 37)', spec: '4–30 V; ± 0.5 °C (A) to ± 1.5 °C (D) at 25 °C' },
      { name: '+Vs', role: 'Supply', connect: '5V (CN7 pin 18)', spec: '3V3 is below its 4 V minimum' },
      { name: '100 kΩ', role: 'Pull-down on Vout', connect: 'PC3 to GND', spec: 'Unplugged reads 0 V, not noise' },
      { name: '0.1 µF', role: 'Optional bypass', connect: '+Vs to GND', spec: 'For long leads' },
    ],
    test: [
      'Wire it as on the Wiring tab, USB unplugged.',
      'Measure 5 V between +Vs and GND, then 0.2–0.3 V on Vout.',
      'Flash airspeed_drag_plate: the Output readout shows the same voltage.',
      'Pinch it: the reading rises within seconds.',
      'Settled, it agrees with the MCP9808 within 1.5 °C.',
    ],
    commands: [
      { label: 'Every thermometer as CSV at 4 Hz', cmd: 'cd firmware && pio run -e env_monitor -t upload && pio device monitor -b 115200' },
      { label: 'Back to the competition build', cmd: FLASH },
    ],
  },
};
