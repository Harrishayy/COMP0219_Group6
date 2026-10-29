// Results: re-read a saved tunnel run (the Wind Tunnel Logger's own, or the copy Record saves) on one screen, built
// like the dashboard (core/dashboard.js): on a laptop it fills the viewport and the page never scrolls (body.fit),
// below that the tiles stack. The run bar on top: the run, its score and a verdict. Left: the timeline of the whole
// run and the moment picked on it at full resolution. Right: what to do about it, the error per speed, the files.
// Data from calib/tunnel.py over HTTP: /api/tunnel/runs, /api/tunnel/run (whole run, ≤ 4000 points),
// /api/tunnel/window (a slice, every sample) and /api/reveal (Show in Finder). #results/<run name> opens a run.
import { h, fill, setText, setTone } from '../core/dom.js';
import { fixed, signed, isNum } from '../core/format.js';
import { TimeChart } from '../core/chart.js';
import { toast, button } from '../core/ui.js';

const SPANS = [2, 5, 10, 30, 60];          // seconds shown around the cursor
const FETCH_MS = 120;                      // wait this long after the last move before asking for a window
const RATE_MIN = 196;                      // the Wind Tunnel Logger marks a rate below 98 % of 200 Hz
const BRIEF = [2, 10];                     // m/s the brief scores
const LEVEL_TAG = { bad: 'Fix', warn: 'Check', info: 'Note' };
const nan = (a) => Float64Array.from(a || [], (v) => (v == null ? NaN : v));
const enc = encodeURIComponent;
const fileName = (p) => String(p).split('/').pop();

const S = { runs: [], run: null, cursor: 0, span: 10, win: null, seq: 0, want: null };
const dur = () => (S.run?.series ? S.run.series.t.at(-1) ?? 0 : 0);
const gtName = () => (S.run?.gt_name === 'GIGA' ? 'GIGA' : 'Tunnel');

function clockAt(t) {
  if (!S.run || !isNum(t)) return '–';
  const d = new Date((S.run.t0_epoch + t) * 1000);
  return `${d.toLocaleTimeString('en-GB', { hour12: false })}.${String(d.getMilliseconds()).padStart(3, '0')}`;
}

/** "35.2" (s from the start) or "12:38:30.5" (clock time on the run's day) → seconds from the start. */
function parseTime(text) {
  const s = String(text).trim();
  if (!s) return NaN;
  if (!s.includes(':')) return Number(s);
  const [hh, mm, ss = '0'] = s.split(':');
  const d = new Date(S.run.t0_epoch * 1000);
  d.setHours(Number(hh), Number(mm), 0, 0);
  return d.getTime() / 1000 + Number(ss) - S.run.t0_epoch;
}

async function reveal(path) {
  try {
    const res = await fetch('/api/reveal', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path }) });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'could not show it');
  } catch (e) {
    toast(`Show in Finder: ${e.message}`, 'bad');
  }
}

/** What the run says to do next, worst first (Recommendation items, as on the sensor pages). */
function verdicts(r) {
  const out = [];
  const add = (level, title, detail) => out.push({ level, title, detail });
  if (!isNum(r.mse)) return [{ level: 'bad', title: 'Nothing to score', detail: `Only ${r.pairs} paired samples.` }];
  if (isNum(r.board_rate_hz) && r.board_rate_hz < RATE_MIN) {
    add('bad', `Sensor at ${fixed(r.board_rate_hz, 1)} Hz`, 'The brief needs 200 Hz. Check the board runs the drag-plate build and nothing else reads its port.');
  }
  if (Math.abs(r.bias) > 0.2) {
    const k = isNum(r.k_scale) && Math.abs(r.k_scale - 1) > 0.02 ? ` Above 2 m/s it fits best with sqrt_k × ${fixed(r.k_scale, 3)} (Wind speed › Tunnel runs › Apply).` : '';
    add(Math.abs(r.bias) > 0.5 ? 'bad' : 'warn', `Reads ${fixed(Math.abs(r.bias), 2)} m/s ${r.bias > 0 ? 'high' : 'low'} on average`,
      `A steady offset is the drag area or F₀: tare with no wind, then Wind speed › Fit the calibration curve.${k}`);
  }
  const bb = (r.bands || []).filter((b) => b.n >= 50);
  if (bb.length >= 2) {
    const lo = bb.reduce((a, b) => (b.bias < a.bias ? b : a)), hi = bb.reduce((a, b) => (b.bias > a.bias ? b : a));
    if (hi.bias - lo.bias > 0.4) {
      add('warn', 'The error changes with speed',
        `${signed(hi.bias, 2)} m/s at ${hi.lo}–${hi.lo + 1} m/s, ${signed(lo.bias, 2)} at ${lo.lo}–${lo.lo + 1}. One scale cannot fix that: fit Cd·A and F₀ together over a full sweep.`);
    }
  }
  if (r.sigma > 0.3) {
    add('warn', `Noisy: σ ${fixed(r.sigma, 2)} m/s`, 'Scatter around the tunnel costs MSE even with no offset. Wind speed › Tune smoothing and lag, while the fan steps.');
  }
  if (isNum(r.drift_per_min) && Math.abs(r.drift_per_min) > 0.2) {
    add('warn', `Drifts ${signed(r.drift_per_min, 2)} m/s per minute`, `Bias ${signed(r.bias_first, 2)} in the first third, ${signed(r.bias_last, 2)} in the last: tare just before the run, and keep the cell out of the fan's warm air.`);
  }
  if (r.missing > 0.02 * (r.pairs + r.missing)) {
    add('warn', `${r.missing} tunnel samples have no sensor sample`, 'Nothing from the board within 150 ms of them: the board stopped or was unplugged for part of the run.');
  }
  const top = Math.max(...(r.bands || []).map((b) => b.lo + 1), 0), bottom = Math.min(...(r.bands || []).map((b) => b.lo), 99);
  if (r.bands?.length && (bottom > BRIEF[0] + 0.5 || top < BRIEF[1] - 0.5)) {
    add('info', `Covers ${bottom}–${top} m/s`, `The brief scores ${BRIEF[0]}–${BRIEF[1]} m/s. Sweep the whole range, holding each speed a few seconds.`);
  }
  const rank = { bad: 0, warn: 1, info: 2 };
  return out.sort((a, b) => rank[a.level] - rank[b.level]);
}

const cell = (label, unit) => {
  const v = h('span'), n = h('span.note');
  const el = h('div.readout', h('span.label', label), h('div.value', v, unit && h('span.unit', unit)), n);
  return { el, v, n };
};
const key = (label, color, dash) => h('span.row', { style: { gap: 'var(--sp-1)' } },
  h(`span.sw${dash ? '.dash' : ''}`, { style: { background: `var(${color})`, color: `var(${color})` } }), label);
const tileHead = (title, ...rest) => h('header.tile-head', h('h2', title), ...rest);

export default {
  id: 'results',
  title: 'Results',
  mount(root) {
    // ------------------------------------------------------------ run bar: the run, its score, the verdict
    const pick = h('select.select', { 'aria-label': 'Run' });
    const pill = h('span.pill', h('span.dot'), h('span'));
    const whole = { mse: cell('Score MSE', 'm²/s²'), rmse: cell('RMSE', 'm/s'), bias: cell('Bias', 'm/s'), sigma: cell('σ', 'm/s'),
      speed: cell('Speeds', 'm/s'), len: cell('Length', 's'), rate: cell('Rates', 'Hz') };
    const bar = h('section.tile.results-bar', { 'aria-label': 'Run' },
      h('header.tile-head', h('h2', 'Results'), pick, pill, h('span.spacer'),
        button('Refresh', () => list(S.run?.name), { cls: '.quiet.sm' })),
      h('div.tile-readouts', Object.values(whole).map((c) => c.el)));

    // ------------------------------------------------------------ timeline
    const overHost = h('div.chart');
    const over = new TimeChart(overHost);
    const scrub = h('input.scrub', { type: 'range', min: 0, max: 1, step: 0.01, value: 0, 'aria-label': 'Time in the run' });
    const goto = h('input.input', { placeholder: '25 or 12:38:30', 'aria-label': 'Go to time (seconds or clock time)', title: 'Seconds from the start, or a clock time', style: { width: '7.5rem' } });
    const spanSeg = h('div.segmented', { role: 'group', 'aria-label': 'Window' },
      SPANS.map((s) => h('button', { type: 'button', 'aria-pressed': String(s === S.span), onclick: () => setSpan(s) }, `${s} s`)));
    const gtKey = h('span');
    const timeline = h('section.tile', { 'aria-label': 'Timeline' },
      tileHead('Timeline', h('span.faint', 'click to go there'), h('span.spacer'),
        h('div.legend', gtKey, key('Sensor', '--s-sensor'), key('Error, 10 s mean', '--s-preview', true))),
      h('div.tile-chart', overHost),
      h('footer.tile-foot.results-controls', scrub,
        button('◀', () => setCursor(S.cursor - S.span / 2), { cls: '.sm', title: 'Back half a window (←, Shift for a whole one)' }),
        button('▶', () => setCursor(S.cursor + S.span / 2), { cls: '.sm', title: 'On half a window (→)' }),
        spanSeg, goto, button('Go', () => go(), { primary: true, cls: '.sm' }),
        button('Largest error', () => worst(), { cls: '.quiet.sm', title: 'Where the 10 s mean error is largest' })));

    // ------------------------------------------------------------ the moment
    const at = { gt: cell('Tunnel', 'm/s'), board: cell('Sensor', 'm/s'), err: cell('Error', 'm/s'),
      wmse: cell('Window MSE', 'm²/s²'), wbias: cell('Window bias', 'm/s') };
    const atTitle = h('h2', 'At –'), atSub = h('span.faint');
    const speedHost = h('div.chart'), errHost = h('div.chart');
    const speedChart = new TimeChart(speedHost), errChart = new TimeChart(errHost);
    const moment = h('section.tile.results-moment', { 'aria-label': 'The moment' },
      h('header.tile-head', atTitle, atSub),
      h('div.tile-readouts', Object.values(at).map((c) => c.el)),
      h('div.tile-chart.grow', h('div.tile-chart-head', h('span.label', 'Speed, every sample')), speedHost),
      h('div.tile-chart', h('div.tile-chart-head', h('span.label', 'Error: sensor − tunnel')), errHost));

    // ------------------------------------------------------------ right: verdicts, by speed, files
    const checks = h('ul.checks');
    const bands = h('div.results-scroll');
    const files = h('div.results-files');
    const side = h('div.results-col.side',
      h('section.tile.grow.todo', { 'aria-label': 'What to do' }, tileHead('What to do'), h('div.results-scroll', checks)),
      h('section.tile.grow', { 'aria-label': 'By speed' }, tileHead('By speed', h('span.faint', 'error per 1 m/s band')), bands),
      h('section.tile.files', { 'aria-label': 'Files' }, tileHead('Files', h('span.spacer'),
        button('Open folder', () => S.run && reveal(S.run.dir), { cls: '.quiet.sm' })), files));

    root.append(h('div.results',
      bar,
      h('div.results-body', h('div.results-col', timeline, moment), side)));

    goto.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
    scrub.addEventListener('input', () => setCursor(Number(scrub.value)));
    pick.addEventListener('change', () => open(S.runs[Number(pick.value)]));
    over.onPick = speedChart.onPick = errChart.onPick = (t) => setCursor(t);
    over.onHover = speedChart.onHover = errChart.onHover = () => drawAll();
    window.addEventListener('keydown', (e) => {
      if (root.hidden || !S.run || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.target.closest?.('input, select, textarea')) return;
      const step = e.shiftKey ? S.span : S.span / 10;
      if (e.key === 'ArrowLeft') { setCursor(S.cursor - step); e.preventDefault(); }
      if (e.key === 'ArrowRight') { setCursor(S.cursor + step); e.preventDefault(); }
    });
    window.addEventListener('resize', () => { if (!root.hidden) drawAll(); });

    // ------------------------------------------------------------ loading
    async function list(want) {
      try {
        const data = await (await fetch('/api/tunnel/runs')).json();
        S.runs = data.runs || [];
      } catch {
        return setVerdict('bad', 'Server not reachable');
      }
      const when = (r) => new Date(r.mtime * 1000).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
      fill(pick, S.runs.map((r, i) => h('option', { value: String(i) }, `${r.name} · ${when(r)}`)));
      if (!S.runs.length) {
        setVerdict(null, 'No saved runs');
        return fill(checks, h('li.all-clear', 'Log a run with the Wind Tunnel Logger (pnpm logger), or press Record with the GIGA connected.'));
      }
      let saved = null;
      try { saved = localStorage.getItem('calib.results.run'); } catch { /* storage blocked */ }
      const i = Math.max(0, S.runs.findIndex((r) => r.name === (want || saved)));
      pick.value = String(i);
      await open(S.runs[i]);
    }

    async function open(r) {
      if (!r) return;
      setVerdict(null, 'Loading…');
      const res = await fetch(`/api/tunnel/run?name=${enc(r.name)}&dir=${enc(r.dir)}`);
      const data = await res.json();
      if (!res.ok) return setVerdict('bad', data.error || 'Could not read the run');
      S.run = data;
      S.win = null;
      try { localStorage.setItem('calib.results.run', r.name); } catch { /* storage blocked */ }
      renderRun();
      scrub.max = String(dur());
      setCursor(Math.min(dur(), S.span / 2));
    }

    function setVerdict(tone, text) {
      setTone(pill, tone);
      setText(pill.children[1], text);
    }

    function renderRun() {
      const r = S.run;
      gtKey.replaceChildren(key(gtName(), '--s-truth'));
      setText(at.gt.el.querySelector('.label'), gtName());
      const items = verdicts(r);
      const worstLevel = items[0]?.level;
      setVerdict(worstLevel === 'bad' ? 'bad' : worstLevel === 'warn' ? 'warn' : 'ok',
        `MSE ${fixed(r.mse, 3)} · ${worstLevel ? items[0].title : 'Within the checks'}`);
      setText(whole.mse.v, fixed(r.mse, 3));
      setText(whole.mse.n, `${r.pairs} pairs${r.missing ? `, ${r.missing} unpaired` : ''}`);
      setText(whole.rmse.v, fixed(r.rmse, 3));
      setText(whole.bias.v, signed(r.bias, 3));
      setText(whole.bias.n, r.bias > 0 ? 'sensor reads high' : 'sensor reads low');
      setTone(whole.bias.el, Math.abs(r.bias) > 0.5 ? 'bad' : Math.abs(r.bias) > 0.2 ? 'warn' : 'ok', ['ok', 'warn', 'bad']);
      setText(whole.sigma.v, fixed(r.sigma, 3));
      setText(whole.sigma.n, 'scatter about the bias');
      const gtv = (r.series?.gt || []).filter(isNum);
      setText(whole.speed.v, gtv.length ? `${fixed(Math.min(...gtv), 1)}–${fixed(Math.max(...gtv), 1)}` : '–');
      setText(whole.speed.n, `${gtName()} range`);
      setText(whole.len.v, fixed(r.duration_s, 1));
      setText(whole.len.n, `from ${clockAt(0).slice(0, 8)} · ${r.source === 'dashboard' ? 'Record' : 'Logger'}`);
      setText(whole.rate.v, `${fixed(r.board_rate_hz, 0)} / ${fixed(r.gt_rate_hz, 0)}`);
      setText(whole.rate.n, `sensor / ${gtName()}`);
      setTone(whole.rate.el, isNum(r.board_rate_hz) && r.board_rate_hz < RATE_MIN ? 'bad' : null, ['ok', 'warn', 'bad']);

      fill(checks, items.length
        ? items.map((c) => h(`li.check-item.${c.level}`, h('div.head', h('span.tag', LEVEL_TAG[c.level]), h('span.title', c.title)), h('div.detail', c.detail)))
        : h('li.all-clear', h('span.dot.ok'), 'No offset, drift or noise above the checks.'));

      fill(bands, r.bands?.length
        ? h('table.table', h('thead', h('tr', ['m/s', 'Pairs', 'Bias', 'σ', ''].map((c, i) => h(`th${i ? '.r' : ''}`, c)))),
          h('tbody', r.bands.map((b) => {
            const tone = Math.abs(b.bias) > 0.5 ? 'bad' : Math.abs(b.bias) > 0.2 ? 'warn' : 'ok';
            return h('tr', h('td', `${b.lo}–${b.lo + 1}`), h('td.r', String(b.n)), h(`td.r.${tone}`, signed(b.bias, 2)), h('td.r', fixed(b.sigma, 2)),
              h('td.r', isNum(b.t_first) ? button('Go', () => setCursor(b.t_first + S.span / 2), { cls: '.quiet.sm', title: `First at ${fixed(b.t_first, 1)} s` }) : ''));
          })))
        : h('p.empty', 'Too few pairs in any band.'));

      const rows = [
        r.files.aligned && ['Scored pairs', r.files.aligned],
        r.files.vdas && [`${gtName()} readings`, r.files.vdas],
        r.files.student && ['Sensor samples', r.files.student],
        r.session && ['Dashboard session', r.session],
      ].filter(Boolean);
      fill(files, h('p.mono.faint.results-dir', { title: r.dir }, `…/${r.dir.split('/').slice(-2).join('/')}/`),
        h('ul.results-file-list', rows.map(([what, p]) => h('li', { title: p },
          h('span.label', what), h('span.mono.path', fileName(p)), button('Show', () => reveal(p), { cls: '.quiet.sm', title: `Show ${p} in Finder` })))));
    }

    // ------------------------------------------------------------ moving
    function setSpan(s) {
      S.span = s;
      for (const b of spanSeg.children) b.setAttribute('aria-pressed', String(b.textContent === `${s} s`));
      setCursor(S.cursor);
    }

    function go() {
      const t = parseTime(goto.value);
      if (!isNum(t)) return toast('Type seconds from the start (35.2) or a clock time (12:38:30).', 'warn');
      if (t < 0 || t > dur()) toast(`The run is 0–${fixed(dur(), 1)} s (${clockAt(0)}–${clockAt(dur())}).`, 'warn');
      setCursor(t);
    }

    function worst() {
      const sr = S.run?.series;
      if (!sr) return;
      // the mean covers a full window only after the first window_s; earlier it is a few samples, and noisy
      let best = -1, bi = -1;
      sr.error_mean.forEach((e, i) => { if (e != null && sr.t[i] >= sr.window_s && Math.abs(e) > best) { best = Math.abs(e); bi = i; } });
      if (bi >= 0) setCursor(sr.t[bi]);
    }

    let timer = null;
    function setCursor(t) {
      if (!S.run) return;
      S.cursor = Math.min(dur(), Math.max(0, t));
      if (document.activeElement !== scrub) scrub.value = String(S.cursor);
      const a = Math.max(0, Math.min(S.cursor - S.span / 2, dur() - S.span)), b = Math.min(dur(), a + S.span);
      S.want = [a, b];
      drawAll();
      clearTimeout(timer);
      timer = setTimeout(() => fetchWindow(a, b), FETCH_MS);
    }

    async function fetchWindow(a, b) {
      const seq = ++S.seq, r = S.run;
      try {
        const res = await fetch(`/api/tunnel/window?name=${enc(r.name)}&dir=${enc(r.dir)}&a=${a.toFixed(3)}&b=${b.toFixed(3)}`);
        const data = await res.json();
        if (seq !== S.seq || r !== S.run) return;   // a newer request or another run since
        if (!res.ok) throw new Error(data.error);
        S.win = { ...data, tt: nan(data.t), board: nan(data.board), gt: nan(data.gt), error: nan(data.error) };
        drawAll();
      } catch (e) {
        if (seq === S.seq) toast(`Could not read that part of the run: ${e.message}`, 'bad');
      }
    }

    // ------------------------------------------------------------ drawing
    function drawAll() {
      const r = S.run;
      if (!r?.series || root.hidden) return;
      const sr = r.series, tt = nan(sr.t), tip = (t) => `${fixed(t, 2)} s · ${clockAt(t)}`;
      over.draw({ t: tt, range: [0, Math.max(dur(), 1e-3)], unit: 'm/s', zero: true, cursor: S.cursor, shade: S.want,
        xFormat: (v) => `${Math.round(v)} s`, tipFormat: tip,
        lines: [
          { label: gtName(), color: '--s-truth', data: nan(sr.gt), width: 1.5 },
          { label: 'Sensor', color: '--s-sensor', data: nan(sr.board), width: 1.25 },
          { label: 'Error, 10 s mean', color: '--s-preview', data: nan(sr.error_mean), dash: true, width: 1.75 },
        ] });
      const w = S.win;
      const [a, b] = S.want || [0, 1];
      const fresh = w && Math.abs(w.a - a) < 1e-6 && Math.abs(w.b - b) < 1e-6;
      setText(atTitle, `At ${clockAt(S.cursor)}`);
      setText(atSub, `t = ${fixed(S.cursor, 2)} s · ${fixed(a, 1)}–${fixed(b, 1)} s shown${w?.decimated > 1 ? ` · every ${w.decimated}th sample` : ''}${fresh ? '' : ' · loading…'}`);
      if (!w) { speedChart.empty('Loading…'); errChart.empty('Loading…'); return; }
      const xf = (v) => `${fixed(v, S.span <= 5 ? 1 : 0)} s`;
      speedChart.draw({ t: w.tt, range: [w.a, w.b], unit: 'm/s', cursor: S.cursor, xFormat: xf, tipFormat: tip, emptyText: 'No samples here',
        lines: [
          { label: gtName(), color: '--s-truth', data: w.gt, width: 2 },
          { label: 'Sensor', color: '--s-sensor', data: w.board, width: 1.5 },
        ] });
      errChart.draw({ t: w.tt, range: [w.a, w.b], unit: 'm/s', zero: true, cursor: S.cursor, xFormat: xf, tipFormat: tip, minSpan: 0.4,
        emptyText: 'No samples here', lines: [{ label: 'Error', color: '--s-preview', data: w.error, width: 1.5 }] });

      // the samples nearest the cursor, and the score of the pairs in the window
      let lo = 0, hi = w.tt.length - 1;
      while (lo < hi) { const k = (lo + hi) >> 1; if (w.tt[k] < S.cursor) lo = k + 1; else hi = k; }
      const e = w.error[lo];
      setText(at.gt.v, fixed(w.gt[lo], 2));
      setText(at.board.v, fixed(w.board[lo], 2));
      setText(at.err.v, signed(e, 2));
      setTone(at.err.el, !isNum(e) ? null : Math.abs(e) > 0.5 ? 'bad' : Math.abs(e) > 0.2 ? 'warn' : 'ok', ['ok', 'warn', 'bad']);
      setText(at.err.n, isNum(e) && isNum(w.gt[lo]) && w.gt[lo] > 0.5 ? `${signed((100 * e) / w.gt[lo], 1)} % of the tunnel` : '');
      const st = w.stats || {};
      setText(at.wmse.v, fixed(st.mse, 3));
      setText(at.wmse.n, `${st.pairs ?? 0} pairs in view`);
      setText(at.wbias.v, signed(st.bias, 3));
      setText(at.wbias.n, isNum(st.sigma) ? `σ ${fixed(st.sigma, 2)} · tunnel ${fixed(st.gt_mean, 2)}` : '');
    }

    list();
    return {
      update() {},
      redraw() { if (S.run) requestAnimationFrame(drawAll); },
      show(name) { if (name && name !== S.run?.name) list(decodeURIComponent(name)); },
    };
  },
};
