// TEMPLATE: copy to <name>.js, rename, fill in, then add it to sensors/index.js. Not registered itself.
// The page layout, colours and components come from core/sensor-page.js and css/; a module is data plus
// the few functions below. Delete what you do not need: only id, title, status and steps are required.
import { h } from '../core/dom.js';
import { fixed, isNum } from '../core/format.js';
import { asyncButton, kv } from '../core/ui.js';

// Limits and constants for this part, in one place so they are easy to tune.
const LIMITS = { noiseWarn: 0.5 };

// Progress kept while the tab is closed.
const S = { zero: null };

// Read the live value. Stream columns (200 Hz): s.buf.stats(col, seconds) with col one of t, gt, est, dev,
// feature, rho, temp_c, pressure_pa. Slow readings: s.env (1 Hz, see environment.js). Server telemetry: s.tele.
const value = (s) => s.buf.stats('feature', 0.5).mean;

const zeroStep = {
  id: 'zero',
  title: 'Zero it',
  why: 'One or two sentences: what to do with your hands, and why it matters.',
  key: () => String(S.zero?.at || ''),              // the body re-renders when this changes
  state(s) {
    if (!s.streaming) return { tone: 'blocked', text: 'No readings' };
    if (S.zero) return { tone: 'done', text: `${fixed(S.zero.mean, 3)}` };
    return { tone: 'todo', text: '' };              // 'skip' when the rig cannot do it (e.g. no airflow)
  },
  body(ctx) {
    return h('div.stack',
      h('div.row', asyncButton('Measure (3 s)', async () => {
        const m = await ctx.actions.measure('feature', 3);
        S.zero = { mean: m.mean, sd: m.sd, at: Date.now() };
        ctx.refresh();
      }, { primary: true, busy: 'Measuring…' })),
      S.zero && h('div.result.ok', h('strong', 'Zeroed'), kv([['Mean', fixed(S.zero.mean, 4)], ['σ', fixed(S.zero.sd, 4)]])));
  },
};

export default {
  id: 'template',                                   // also the URL hash
  title: 'Template',                                // tab and heading
  part: 'Part number · bus and address · rate',    // one line under the heading
  intro: 'One sentence: what this sensor measures and what calibrating it involves.',
  allClear: 'Shown in Recommendations when no check fires.',

  status(s) {                                       // pill and tab badge; always a word, never colour alone
    if (!s.streaming) return { tone: 'idle', text: 'No readings' };
    return { tone: S.zero ? 'ok' : 'idle', text: S.zero ? 'Zeroed' : 'Not zeroed' };
  },

  // Optional: say when the running firmware does not read this part. The tab then shows a notice with the
  // commands, plus the reference, instead of live data.
  // available: (s) => ({ live: false, title: '…', detail: '…', commands: ['cd firmware && pio run -e … -t upload'] }),

  readouts: [                                       // the stat strip: the numbers a decision needs, 3 to 5
    { label: 'Reading', unit: 'N', digits: 4, value, note: () => 'last 0.5 s' },
    { label: 'Noise σ', unit: 'N', digits: 4, value: (s) => s.buf.stats('feature', 2).sd, tone: (s) => (s.buf.stats('feature', 2).sd > LIMITS.noiseWarn ? 'warn' : null) },
  ],

  views: [{                                         // chart views; colours are token names
    id: 'live', label: 'Live', title: 'Reading', sub: 'Last 20 s', legend: [],
    draw(chart, s) {
      const b = s.buf, windowS = 20;
      chart.draw({ t: b.col('t'), i0: b.since(windowS), window: windowS, unit: 'N', lines: [{ label: 'Reading', color: '--s-sensor', data: b.col('feature') }] });
    },
  }],

  steps: [zeroStep],                                // the calibration procedure, in order

  checks(s) {                                       // recommendations: level, title, one line of why, one fix
    const sd = s.buf.stats('feature', 2).sd;
    return isNum(sd) && sd > LIMITS.noiseWarn
      ? [{ id: 'noise', level: 'warn', title: `Noisy: σ ${fixed(sd, 3)}`, detail: 'What to check first, in the team\'s words.' }]
      : [];
  },

  info: {                                           // the Reference panel at the foot of the tab
    summary: 'How the part works, in two or three sentences.',
    brief: 'What the project brief says about it.',
    source: 'Where these facts come from',
    parts: [{ name: 'Part', role: 'What it does', connect: 'Pin or I²C address', spec: 'Key spec' }],
    test: ['First bench check.', 'Second bench check.'],
    commands: [{ label: 'Flash its test firmware', cmd: 'cd firmware && pio run -e <env> -t upload' }],
  },
};
