// Pitot: the ground truth (Dwyer 616KD differential-pressure transmitter on a pitot tube), read by the lab's
// script on the PC at 200 Hz. The server lines it up with the board's stream; every airspeed score uses it.
// On the open bench there is no pitot: the tab says so and its checks skip.
import { h } from '../core/dom.js';
import { fixed, signed, isNum } from '../core/format.js';
import { asyncButton, button, kv } from '../core/ui.js';

const RATE_MIN_HZ = 190;
const ZERO_LIMIT = 0.3;       // m/s with no airflow; the √ of pressure noise reads a little above 0
const STEADY_SD_LIMIT = 0.15; // m/s at a held fan speed

const S = { zero: null, steady: null };

const speed = (s) => s.buf.stats('gt', 0.5).mean;
const rhoNow = (s) => (s.buf.last('rho') > 0.5 ? s.buf.last('rho') : 1.2);
const NEEDS_PITOT = { tone: 'skip', text: 'Needs the pitot' };
const openConnection = () => document.getElementById('connection')?.showPopover();

const connectStep = {
  id: 'connect',
  title: 'Connect the pitot',
  why: 'The lab\'s script reads the pressure transmitter and prints one reading per line. Point the server at it in Connection: a command, a Python function or a serial port. If it prints pressure in Pa, tick "prints pressure".',
  key: (s) => `${s.hasPitot}|${s.isSim}`,
  state: (s) => (s.hasPitot ? { tone: 'done', text: `${fixed(s.tele.gt_hz, 0)} Hz` } : { tone: 'todo', text: 'Not connected' }),
  body(ctx) {
    const s = ctx.store;
    if (s.hasPitot) return h('p.faint', `Streaming from ${s.tele.port_gt || s.tele.gt_status || 'the pitot source'}.`);
    return h('div.row',
      button('Open connection', openConnection, { primary: true }),
      s.isSim && button('Simulate the tunnel', () => ctx.actions.sim({ pitot: 'on', fan: 'on' })));
  },
};

const zeroStep = {
  id: 'zero',
  title: 'Check it reads zero with no airflow',
  why: 'With the fan stopped the pitot should read near 0 m/s. A large reading means the transmitter needs zeroing or a tube is pinched.',
  key: (s) => `${S.zero?.at || ''}|${s.isSim}`,
  state(s) {
    if (!s.hasPitot) return NEEDS_PITOT;
    if (S.zero) return S.zero.ok ? { tone: 'done', text: `${fixed(S.zero.mean, 2)} m/s` } : { tone: 'blocked', text: `${fixed(S.zero.mean, 2)} m/s` };
    return { tone: 'todo', text: '' };
  },
  body(ctx) {
    const s = ctx.store;
    return h('div.stack',
      h('div.row',
        s.isSim && button('Stop the simulated fan', () => ctx.actions.sim({ fan: 'off' })),
        asyncButton('Measure (3 s)', async () => {
          const m = await ctx.actions.measure('gt', 3);
          S.zero = { mean: m.mean, sd: m.sd, ok: Math.abs(m.mean) < ZERO_LIMIT, at: Date.now() };
          ctx.toast(S.zero.ok ? 'Pitot reads zero.' : 'Pitot is off zero.', S.zero.ok ? 'ok' : 'warn');
          ctx.refresh();
        }, { primary: true, busy: 'Measuring…' })),
      S.zero && h(`div.result${S.zero.ok ? '.ok' : '.warn'}`,
        h('strong', S.zero.ok ? 'Reads zero' : `Reads ${fixed(S.zero.mean, 2)} m/s with no airflow`),
        kv([['Mean', `${signed(S.zero.mean, 3)} m/s`], ['σ', `${fixed(S.zero.sd, 3)} m/s`]]),
        !S.zero.ok && h('p', 'Zero the transmitter with the fan off, and check both tubes are clear and not kinked.')));
  },
};

const steadyStep = {
  id: 'steady',
  title: 'Check the noise at a steady speed',
  why: `Hold the fan at one speed (about 6 m/s) and measure. Turbulence and transmitter noise should stay under ${STEADY_SD_LIMIT} m/s σ, or every score inherits it.`,
  key: (s) => `${S.steady?.at || ''}|${s.isSim}`,
  state(s) {
    if (!s.hasPitot) return NEEDS_PITOT;
    if (S.steady) return S.steady.ok ? { tone: 'done', text: `σ ${fixed(S.steady.sd, 3)} m/s` } : { tone: 'blocked', text: `σ ${fixed(S.steady.sd, 3)} m/s` };
    return { tone: 'todo', text: '' };
  },
  body(ctx) {
    const s = ctx.store;
    return h('div.stack',
      h('div.row',
        s.isSim && button('Hold the simulated fan', () => ctx.actions.sim({ fan: 'on', profile: 'steps' })),
        asyncButton('Measure (3 s)', async () => {
          const m = await ctx.actions.measure('gt', 3);
          if (m.mean < 1.5) throw new Error(`The pitot reads ${fixed(m.mean, 2)} m/s: start the fan and hold one speed first.`);
          S.steady = { mean: m.mean, sd: m.sd, ok: m.sd < STEADY_SD_LIMIT, at: Date.now() };
          ctx.refresh();
        }, { primary: true, busy: 'Measuring…' })),
      S.steady && h(`div.result${S.steady.ok ? '.ok' : '.warn'}`,
        h('strong', S.steady.ok ? 'Quiet enough to score against' : 'Noisy'),
        kv([['Speed', `${fixed(S.steady.mean, 2)} m/s`], ['σ', `${fixed(S.steady.sd, 3)} m/s`]]),
        !S.steady.ok && h('p', 'Check the speed was held for the whole 3 s, then the pitot tip alignment and the tube length.')));
  },
};

function drawLive(chart, s, hidden) {
  const b = s.buf, windowS = 20;
  chart.draw({
    t: b.col('t'), i0: b.since(windowS), window: windowS, unit: 'm/s', yMin: 0, yMax: 4,
    emptyText: s.hasPitot ? 'Waiting for data…' : 'No pitot connected',
    lines: [
      { key: 'gt', label: 'Pitot', color: '--s-truth', data: b.col('gt'), width: 2 },
      { key: 'dev', label: 'Sensor (board)', color: '--s-sensor', data: b.col('dev'), width: 1.25 },
    ].filter((l) => !hidden.has(l.key)),
  });
}

export default {
  id: 'pitot',
  title: 'Pitot',
  part: 'Dwyer 616KD + pitot · ground truth · read by the lab script on the PC',
  intro: 'The reference every airspeed score is measured against. Connect it, check it reads zero with no airflow and is quiet at a held speed.',
  allClear: 'Streaming at full rate; nothing to fix.',

  status(s) {
    if (s.conn !== 'open') return { tone: 'idle', text: 'Offline' };
    if (!s.hasPitot) return { tone: 'idle', text: 'Not connected' };
    if (s.tele.gt_hz < RATE_MIN_HZ) return { tone: 'warn', text: `${fixed(s.tele.gt_hz, 0)} Hz` };
    if (S.zero && !S.zero.ok) return { tone: 'warn', text: 'Off zero' };
    return { tone: 'ok', text: `${fixed(s.tele.gt_hz, 0)} Hz` };
  },

  readouts: [
    { label: 'Speed', unit: 'm/s', digits: 2, value: (s) => (s.hasPitot ? speed(s) : NaN), note: (s) => (s.hasPitot ? 'last 0.5 s' : 'not connected') },
    { label: 'Dynamic pressure', unit: 'Pa', digits: 1, value: (s) => (s.hasPitot ? 0.5 * rhoNow(s) * speed(s) ** 2 : NaN), note: () => 'q = ½ρv²' },
    { label: 'Rate', unit: 'Hz', digits: 0, value: (s) => (s.hasPitot ? s.tele.gt_hz : NaN), note: () => `needs ${RATE_MIN_HZ}+`, tone: (s) => (s.hasPitot && s.tele.gt_hz < RATE_MIN_HZ ? 'warn' : null) },
    { label: 'Noise σ', unit: 'm/s', digits: 3, value: (s) => (s.hasPitot ? s.buf.stats('gt', 1).sd : NaN), note: () => 'last 1 s, includes speed changes' },
  ],

  views: [{ id: 'live', label: 'Live', title: 'Pitot', sub: 'Last 20 s', legend: [{ key: 'gt', label: 'Pitot', color: '--s-truth' }, { key: 'dev', label: 'Sensor (board)', color: '--s-sensor' }], draw: drawLive }],

  steps: [connectStep, zeroStep, steadyStep],

  checks(s) {
    if (s.conn !== 'open') return [{ id: 'server', level: 'bad', title: 'Calibration server not reachable', detail: 'Start it with `python -m calib serve` in calibration/, then reload this page.' }];
    if (!s.hasPitot) {
      return [{ id: 'bench', level: 'info', title: 'No pitot: bench mode', detail: 'Load cell, environment and the airspeed bench checks work without it. Connect it in the tunnel to score the sensor.', action: { label: 'Open connection', run: openConnection } }];
    }
    const out = [];
    if (s.tele.gt_hz < RATE_MIN_HZ) out.push({ id: 'rate', level: 'warn', title: `Pitot at ${fixed(s.tele.gt_hz, 0)} Hz`, detail: 'Scores line the pitot up with the 200 Hz sensor stream. Check the script\'s sample rate and that nothing else reads the transmitter.' });
    const v = speed(s);
    if (isNum(v) && v > 11) out.push({ id: 'range', level: 'warn', title: `Reads ${fixed(v, 1)} m/s`, detail: 'Above the brief\'s 2–10 m/s range. If the fan is not that fast, check the pressure units ("prints pressure" in Connection).' });
    return out;
  },

  info: {
    summary: 'A pitot tube in the tunnel feeds a Dwyer 616KD differential-pressure transmitter. The lab\'s Python script samples it at 200 Hz and prints either speed in m/s or dynamic pressure in Pa. With pressure, the server converts it with the board\'s own air density: v = √(2·Δp/ρ).',
    brief: 'Ground truth is a Dwyer 616KD-LR-B42-BD1 differential pressure sensor and pitot, sampled at 200 Hz. The course script compares your 200 Hz stream against it; accuracy rank is by MSE.',
    source: 'Project brief · calibration/docs/SPEC.md',
    parts: [
      { name: 'Dwyer 616KD-LR-B42-BD1', role: 'Differential pressure transmitter', connect: 'Lab DAQ / script on the PC', spec: 'Read at 200 Hz' },
      { name: 'Pitot tube', role: 'Total and static pressure taps', connect: 'Two tubes to the transmitter', spec: 'Upstream of the sensor' },
    ],
    test: [
      'Start the server with the pitot source, or set it in Connection.',
      'Fan off: the pitot should read under 0.3 m/s.',
      'Hold the fan at one speed: σ should stay under 0.15 m/s.',
      'Step the fan: the pitot should follow within a few tens of ms.',
    ],
    commands: [
      { label: 'Script that prints m/s', cmd: 'python -m calib serve --port /dev/cu.usbmodem103 --gt-cmd "python3 gt.py"' },
      { label: 'Script that prints pressure in Pa', cmd: 'python -m calib serve --port /dev/cu.usbmodem103 --gt-cmd "python3 gt.py" --gt-pressure' },
    ],
  },
};
