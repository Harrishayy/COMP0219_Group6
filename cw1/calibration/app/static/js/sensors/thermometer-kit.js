// Shared pieces for a single-thermometer section (the MCP9808 in temperature.js): readings from the board's 1 Hz `ENV` reply,
// a history chart, and the bench steps every thermometer goes through (wire it, pinch it, let it settle,
// compare it, calibrate it). Not a section itself: sensors/index.js does not list it.
//
// The thermometers are never combined. `ENV` reports each one raw; the board adds `<key>_offset` to the
// one `temp_source` picks and calls that the air temperature (`t_air`), which sets the density.
import { h } from '../core/dom.js';
import { fixed, signed, isNum } from '../core/format.js';
import { asyncButton, kv, numberInput } from '../core/ui.js';

/** Board order of the thermometers: the value of `temp_source` that picks each one (calib/models.py). */
export const THERMOMETER_KEYS = ['mcp9808', 'sht40', 'stts22h', 'lps22df', 'tmp36', 'lm35'];

/** The last `ENV` reply if it is fresh (the server asks once a second), else null. */
export const envNow = (s) => (s.env && s.env.age_s < 5 ? s.env : null);
export const envVal = (s, k) => envNow(s)?.[k] ?? NaN;

/** The offset the board adds to this thermometer (°C), NaN before the board has answered GET. */
export const boardOffset = (s, k) => s.board?.[`${k}_offset`] ?? NaN;
/** Raw reading plus the board's offset: what this thermometer reads once calibrated. */
export const calibrated = (s, k) => envVal(s, k) + (isNum(boardOffset(s, k)) ? boardOffset(s, k) : 0);
/** True when the board takes the air temperature (and so ρ) from this thermometer. */
export const feedsRho = (s, k) => THERMOMETER_KEYS[s.board?.temp_source ?? 0] === k;

/** Mean, σ, min and max of one ENV field over the last `sec` seconds of history. */
export function histStats(s, k, sec) {
  const hist = s.envHistory;
  const t1 = hist.length ? hist[hist.length - 1].t : 0;
  let n = 0, sum = 0, sum2 = 0, min = Infinity, max = -Infinity, t0 = Infinity;
  for (let i = hist.length - 1; i >= 0 && hist[i].t >= t1 - sec; i--) {
    const v = hist[i][k];
    if (!isNum(v)) continue;
    n++; sum += v; sum2 += v * v; min = Math.min(min, v); max = Math.max(max, v); t0 = hist[i].t;
  }
  if (!n) return { n: 0, mean: NaN, sd: NaN, min: NaN, max: NaN, span: 0 };
  const mean = sum / n;
  return { n, mean, sd: Math.sqrt(Math.max(0, sum2 / n - mean * mean)), min, max, span: t1 - t0 };
}

/** A chart view over the env history (up to 10 minutes). lines: [{ key, label, color, width?, digits? }]. */
export function historyView({ id, label, title, sub, lines, unit = '°C', minSpan = 0.5 }) {
  return {
    id, label, title, sub, legend: lines,
    draw(chart, s, hidden) {
      const hist = s.envHistory, n = hist.length;
      const t = Float64Array.from(hist, (r) => r.t);
      const windowS = Math.min(10 * 60, Math.max(60, n > 1 ? t[n - 1] - t[0] : 0));
      let i0 = 0;
      while (i0 < n && t[i0] < (t[n - 1] ?? 0) - windowS) i0++;
      chart.draw({
        t, i0, window: windowS, unit, minSpan,
        emptyText: envNow(s) ? 'Collecting readings (one per second)…' : 'No readings from the board',
        lines: lines.filter((l) => !hidden.has(l.key)).map((l) => ({
          label: l.label, color: l.color, width: l.width ?? 2, digits: l.digits ?? 2,
          data: Float64Array.from(hist, (r) => (isNum(r[l.key]) ? r[l.key] : NaN)),
        })),
      });
    },
  };
}

/** Step: wire the part until the board reads it. `hint(s)` says what to fix while it does not. */
export function wireStep({ key, name, hint }) {
  return {
    id: 'wire',
    title: `Wire the ${name} and see it on the board`,
    why: `Follow the Wiring tab step by step, then check the board reads a temperature. Nothing else in this section means anything until it does.`,
    key: (s) => String(isNum(envVal(s, key))),
    state(s) {
      if (!s.streaming || !envNow(s)) return { tone: 'blocked', text: 'No live data' };
      return isNum(envVal(s, key)) ? { tone: 'done', text: `${fixed(envVal(s, key), 2)} °C` } : { tone: 'todo', text: 'Not reading' };
    },
    body(ctx) {
      const s = ctx.store;
      return h('div.stack',
        h('div.row', h('button.btn.primary', { type: 'button', onclick: () => ctx.show('wiring') }, 'Open the wiring steps')),
        !isNum(envVal(s, key)) && h('p', hint(s)));
    },
  };
}

/**
 * Step: hold the part between finger and thumb. A rise proves the reading is this part and not another
 * thermometer, which a mix-up of pins or addresses would otherwise hide.
 */
export function pinchStep({ key, name, rise = 0.5, limitS = 45, S }) {
  const result = (s) => {
    if (!S.pinch) return null;
    const hist = s.envHistory.filter((r) => r.t >= S.pinch.t0 && isNum(r[key]));
    const peak = hist.reduce((m, r) => Math.max(m, r[key]), -Infinity);
    const elapsed = Date.now() / 1000 - S.pinch.t0;
    return { gain: isNum(peak) ? peak - S.pinch.base : NaN, elapsed };
  };
  return {
    id: 'pinch',
    title: `Pinch test: warm it with your fingers`,
    why: `Hold the ${name} between finger and thumb for up to ${limitS} s. It should climb at least ${rise} °C. This proves the number on screen comes from this part.`,
    key: () => String(S.pinch?.t0 || ''),
    state(s) {
      if (S.pinchOk) return { tone: 'done', text: `+${fixed(S.pinchOk, 2)} °C` };
      if (!isNum(envVal(s, key))) return { tone: 'blocked', text: 'Not reading' };
      const r = result(s);
      if (!r) return { tone: 'todo', text: '' };
      if (r.gain >= rise) { S.pinchOk = r.gain; return { tone: 'done', text: `+${fixed(r.gain, 2)} °C` }; }
      if (r.elapsed > limitS) return { tone: 'blocked', text: `Only ${signed(r.gain, 2)} °C` };
      return { tone: 'todo', text: `${signed(r.gain, 2)} °C, ${Math.floor(r.elapsed)} s` };
    },
    body(ctx) {
      const r = result(ctx.store);
      const failed = r && !S.pinchOk && r.elapsed > limitS;
      return h('div.stack',
        h('div.row', asyncButton(S.pinch ? 'Start again' : 'Start, then hold it', async () => {
          const base = histStats(ctx.store, key, 5).mean;
          if (!isNum(base)) throw new Error(`No ${name} reading yet.`);
          S.pinch = { t0: Date.now() / 1000, base };
          S.pinchOk = null;
          ctx.refresh();
        }, { primary: true })),
        S.pinchOk && h('div.result.ok', h('strong', `Rose ${fixed(S.pinchOk, 2)} °C: the reading is the ${name}.`), h('span', 'Let go; it takes a minute or two to cool back.')),
        failed && h('div.result.warn', h('strong', `No rise in ${limitS} s.`), h('span', `The number may be coming from somewhere else, or the part is not in contact with your fingers. Check the wiring, and that you are holding the ${name}.`)));
    },
  };
}

/** Step: wait for a steady reading (drift under 0.1 °C over two minutes). */
export function settleStep({ key, name, windowS = 120, limitC = 0.1 }) {
  return {
    id: 'settle',
    title: 'Let it settle',
    why: `After wiring or a pinch the ${name} takes a few minutes to reach room temperature. Wait until it moves less than ${limitC} °C in two minutes before comparing.`,
    state(s) {
      if (!isNum(envVal(s, key))) return { tone: 'blocked', text: 'Not reading' };
      const st = histStats(s, key, windowS);
      if (st.span < windowS - 5) return { tone: 'todo', text: `Watching, ${Math.floor(st.span)} of ${windowS} s` };
      const drift = st.max - st.min;
      return drift < limitC ? { tone: 'done', text: `Steady (${fixed(drift, 2)} °C in 2 min)` } : { tone: 'todo', text: `Moving ${fixed(drift, 2)} °C in 2 min` };
    },
    body: () => h('p.faint', 'Nothing to do: keep hands and breath away from it. This step completes by itself.'),
  };
}

/**
 * Step: compare with the other thermometers over the last 30 s. refs: [{ key, name, tol }], first one present
 * is used. Skipped when none of them reads (on the bench without the shield).
 */
export function compareStep({ key, name, refs }) {
  const pick = (s) => refs.find((r) => isNum(envVal(s, r.key)));
  const diff = (s, r) => histStats(s, key, 30).mean - histStats(s, r.key, 30).mean;
  return {
    id: 'compare',
    title: 'Compare with the other thermometers',
    why: `Side by side and settled, the ${name} should agree with ${refs.map((r) => r.name).join(' or ')} within their combined tolerance. A larger gap means self-heating, a warm spot, or a wrong reading.`,
    state(s) {
      if (!isNum(envVal(s, key))) return { tone: 'blocked', text: 'Not reading' };
      const r = pick(s);
      if (!r) return { tone: 'skip', text: 'No other thermometer' };
      const d = diff(s, r);
      return Math.abs(d) <= r.tol ? { tone: 'done', text: `${signed(d, 2)} °C vs ${r.name}` } : { tone: 'todo', text: `${signed(d, 2)} °C vs ${r.name}` };
    },
    body(ctx) {
      const s = ctx.store;
      return h('div.stack', kv(refs.map((r) => {
        const d = isNum(envVal(s, r.key)) ? diff(s, r) : NaN;
        return [r.name, isNum(d) ? `${signed(d, 2)} °C (tolerance ± ${r.tol} °C)` : 'not reading'];
      })), h('p.faint', 'Averages over the last 30 s. This updates by itself.'));
    },
    update(body, s) {
      const sig = refs.map((r) => fixed(isNum(envVal(s, r.key)) ? diff(s, r) : NaN, 2)).join();
      if (body.dataset.sig !== sig) {
        body.dataset.sig = sig;
        const dl = body.querySelector('dl');
        if (dl) dl.replaceWith(kv(refs.map((r) => {
          const d = isNum(envVal(s, r.key)) ? diff(s, r) : NaN;
          return [r.name, isNum(d) ? `${signed(d, 2)} °C (tolerance ± ${r.tol} °C)` : 'not reading'];
        })));
      }
    },
  };
}

/**
 * Step: calibrate against a reference thermometer held next to the part. The offset is reference minus the
 * raw 30 s mean; it goes to the board with SET <key>_offset and into the header with Export.
 */
export function calibrateStep({ key, name, S, windowS = 30 }) {
  const param = `${key}_offset`;
  return {
    id: 'calibrate',
    title: 'Calibrate against a reference thermometer',
    why: `Hold a trusted thermometer next to the ${name}, both settled, and type in what it reads. The board then adds the difference to every ${name} reading. Export afterwards (Runs) so the offset survives a reset.`,
    key: (s) => `${S.cal?.at || ''}|${fixed(boardOffset(s, key), 3)}`,
    state(s) {
      if (!isNum(envVal(s, key))) return { tone: 'blocked', text: 'Not reading' };
      if (!isNum(boardOffset(s, key))) return { tone: 'blocked', text: `Board has no ${param}: flash the current build` };
      if (!S.cal) return { tone: 'todo', text: boardOffset(s, key) ? `Offset ${signed(boardOffset(s, key), 2)} °C on the board` : '' };
      return Math.abs(boardOffset(s, key) - S.cal.offset) < 0.005
        ? { tone: 'done', text: `Offset ${signed(S.cal.offset, 2)} °C on the board` }
        : { tone: 'todo', text: 'Waiting for the board to confirm' };
    },
    body(ctx) {
      const s = ctx.store;
      const ref = numberInput({ value: S.cal?.ref ?? '', label: 'Reference temperature in °C' });
      return h('div.stack',
        h('div.row.end',
          h('div.field', h('label', 'Reference (°C)'), ref),
          asyncButton('Set the offset', async () => {
            const tRef = Number(ref.value);
            if (ref.value === '' || !isNum(tRef)) throw new Error('Enter what the reference thermometer reads.');
            const st = histStats(ctx.store, key, windowS);
            if (st.n < 10) throw new Error(`Need ${windowS} s of ${name} readings first.`);
            if (st.max - st.min > 0.2) throw new Error(`The ${name} moved ${fixed(st.max - st.min, 2)} °C in ${windowS} s: let it settle first.`);
            const offset = tRef - st.mean;
            if (Math.abs(offset) > 5) throw new Error(`That is ${signed(offset, 2)} °C away. Check the reference is next to the ${name} and reads in °C.`);
            S.cal = { ref: tRef, raw: st.mean, offset, at: Date.now() };
            ctx.actions.setOnBoard(param, Number(offset.toFixed(3)));
            ctx.refresh();
          }, { primary: true }),
          isNum(boardOffset(s, key)) && boardOffset(s, key) !== 0 && asyncButton('Clear', async () => {
            S.cal = null;
            ctx.actions.setOnBoard(param, 0);
            ctx.refresh();
          })),
        kv([
          ['On the board now', isNum(boardOffset(s, key)) ? `${signed(boardOffset(s, key), 3)} °C` : 'not reported'],
          S.cal && ['Last calibration', `reference ${fixed(S.cal.ref, 2)} °C, raw ${fixed(S.cal.raw, 2)} °C (${windowS} s mean)`],
        ]),
        h('p.faint', feedsRho(s, key)
          ? `The board uses the ${name} for the air density, so this offset moves ρ too.`
          : `The board takes the air density from another thermometer; this offset only changes the ${name}'s own calibrated reading.`));
    },
  };
}
