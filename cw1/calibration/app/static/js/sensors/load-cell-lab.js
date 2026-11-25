// The HAL lab firmware reports raw counts until the user calibrates a known mass.
// Only confirmed board replies complete a step; raw counts are never shown as grams.
import { h } from '../core/dom.js';
import { fixed, isNum } from '../core/format.js';
import { asyncButton, numberInput } from '../core/ui.js';

const lab = (s) => s.tele.loadcell_lab || {};
const raw = (s) => s.buf.last('lab_raw');
const weight = (s) => lab(s).calibrated && isNum(lab(s).weight_g) ? lab(s).weight_g : NaN;
let pending = false;

export async function confirmedCommand(ctx, line, field) {
  const s = ctx.store;
  if (pending) throw new Error('Wait for the board to finish the current step.');
  if (!s.streaming || !s.tele.loadcell_lab) throw new Error('Connect the board and wait for live readings.');
  const before = lab(s)[field] || 0, errors = lab(s).error_seq || 0;
  pending = true;
  try {
    await new Promise((resolve, reject) => {
      let off, timer;
      const finish = (error) => { off?.(); clearTimeout(timer); error ? reject(error) : resolve(); };
      off = s.on('change', () => {
        const state = lab(s);
        if (!s.streaming || !s.tele.loadcell_lab) return finish(new Error('Board disconnected. Reconnect before calibrating.'));
        if ((state.error_seq || 0) > errors) return finish(new Error(state.last_error || 'The board rejected the command.'));
        if ((state[field] || 0) > before) finish();
      });
      timer = setTimeout(() => finish(new Error('No confirmation from the board. Check the connection and try again.')), 6000);
      if (!ctx.actions.command(line)) finish(new Error('Calibration server disconnected.'));
    });
  } finally { pending = false; }
}

function draw(key, unit, span) {
  return (chart, s) => {
    const b = s.buf;
    // Before calibration and after reset, the mass chart has no numerical values.
    const data = key === 'lab_weight' && !lab(s).calibrated ? new Float64Array(b.n).fill(NaN) : b.col(key);
    chart.draw({ t: b.col('t'), i0: b.since(30), window: 30, unit, minSpan: span, digits: key === 'lab_raw' ? 0 : 2,
      lines: [{ label: unit === 'g' ? 'Weight' : 'Raw ADC', color: '--s-sensor', data }],
      emptyText: key === 'lab_weight' ? 'Tare and calibrate with a known mass to see grams' : 'Waiting for the board' });
  };
}

export default {
  id: 'load-cell', title: 'Load cell', part: 'NAU7802 · lab firmware · 80 samples/s, averaged readings',
  intro: 'Read live counts, zero the unloaded cell, then use a known mass to display grams.',
  allClear: 'The board confirms its calibration. Use another known mass to check the result.',
  status(s) {
    if (!s.streaming) return { tone: 'bad', text: 'No live readings' };
    return lab(s).calibrated ? { tone: 'ok', text: 'Calibrated' } : { tone: 'warn', text: lab(s).tared ? 'Ready for known mass' : 'Uncalibrated' };
  },
  readouts: [
    { label: 'Weight', unit: 'g', digits: 2, value: weight,
      note: (s) => lab(s).calibrated ? 'Measured by the board' : 'Complete tare and known-mass calibration' },
    { label: 'Raw ADC', unit: 'counts', digits: 0, value: raw, note: () => 'Live sensor reading' },
    { label: 'Zero offset', unit: 'counts', digits: 0, value: (s) => lab(s).tared ? lab(s).zero_offset : NaN,
      note: (s) => lab(s).tared ? 'Tare confirmed by the board' : 'Remove all load, then tare' },
    { label: 'Scale factor', unit: 'counts/g', digits: 4, value: (s) => lab(s).calibrated ? lab(s).scale_factor : NaN,
      note: () => 'Calibration is lost when the board resets' },
  ],
  views: [
    { id: 'raw', label: 'Raw counts', title: 'Live load-cell readings', sub: 'Raw counts are available before calibration',
      legend: [{ key: 'raw', label: 'Raw ADC', color: '--s-sensor' }], draw: draw('lab_raw', 'counts', 100) },
    { id: 'grams', label: 'Grams', title: 'Weight', sub: 'Calibrated readings from the board',
      legend: [{ key: 'weight', label: 'Weight', color: '--s-sensor' }], draw: draw('lab_weight', 'g', 2) },
  ],
  steps: [
    {
      id: 'tare', title: 'Zero the unloaded cell',
      why: 'Remove everything from the load cell and stop any airflow. This tells the board what zero load looks like.',
      state: (s) => !s.streaming ? { tone: 'blocked', text: 'Connect the board' }
        : lab(s).tared ? { tone: 'done', text: 'Board confirms zero' } : { tone: 'todo', text: 'Remove all load' },
      body(ctx) {
        return h('div.stack', h('p', 'Keep the cell still, then click once.'), asyncButton('Tare unloaded cell', async () => {
          await confirmedCommand(ctx, 't', 'tare_seq');
          ctx.toast('The board confirmed its zero offset. Place your known mass next.'); ctx.refresh();
        }, { primary: true, busy: 'Waiting for board…' }));
      },
    },
    {
      id: 'scale', title: 'Calibrate with a known mass',
      why: 'Place a mass whose weight you know on the cell. Enter its actual weight in grams, then calibrate. Stay within your load cell’s rated capacity.',
      state: (s) => !s.streaming || !lab(s).tared ? { tone: 'blocked', text: 'Tare first' }
        : lab(s).calibrated ? { tone: 'done', text: 'Board confirms calibration' } : { tone: 'todo', text: 'Place known mass' },
      body(ctx) {
        const mass = numberInput({ value: '', min: 0.001, label: 'Known mass in grams' });
        const btn = asyncButton('Calibrate with this mass', async () => {
          if (!lab(ctx.store).tared) throw new Error('Remove all load and complete the tare step first.');
          const grams = Number(mass.value);
          if (!mass.value.trim() || !Number.isFinite(grams) || grams <= 0) throw new Error('Enter the actual known mass in grams, greater than zero.');
          await confirmedCommand(ctx, `k ${grams}`, 'calibration_seq');
          ctx.toast('The board confirmed calibration. Live weight is now shown in grams.'); ctx.refresh();
        }, { primary: true, busy: 'Waiting for board…' });
        btn.disabled = !ctx.store.streaming || !lab(ctx.store).tared;
        return h('div.stack', h('div.row.end', h('div.field', h('label', 'Known mass (g)'), mass), btn),
          h('p.faint', 'Use your measured mass; no default mass is assumed.'));
      },
      key: (s) => `${s.streaming}|${lab(s).tared}`,
    },
  ],
  checks(s) {
    if (!s.streaming) return [{ id: 'connection', level: 'bad', title: 'Waiting for live board readings',
      detail: 'Connect the Nucleo through Connection. Close the Terminal serial monitor so the website can use its port.' }];
    const state = lab(s), out = [];
    if (!state.calibrated) out.push({ id: 'uncalibrated', level: 'info', title: 'Raw counts are working; grams need calibration',
      detail: state.tared ? 'Place a known mass and complete step 2.' : 'Remove all load and complete step 1, then calibrate with a known mass.' });
    if (state.last_error) out.push({ id: 'board-error', level: 'warn', title: 'Board message', detail: state.last_error });
    out.push({ id: 'ram', level: 'info', title: 'Recalibrate after unplugging or resetting',
      detail: 'The board stores zero and scale factor in RAM. This website shows the calibration the board actually confirms.' });
    return out;
  },
};
