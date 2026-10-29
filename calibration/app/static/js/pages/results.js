// Results: re-read a saved tunnel run (the Wind Tunnel Logger's own, or the copy Record saves): where its files are,
// the whole run with its score, and any moment of it at full resolution with the error there. Data from
// calib/tunnel.py over HTTP: /api/tunnel/runs, /api/tunnel/run (whole run, ≤ 4000 points), /api/tunnel/window
// (a slice, every sample) and /api/reveal (Show in Finder). #results/<run name> opens a run.
import { h, fill, setText, setTone } from '../core/dom.js';
import { fixed, signed, isNum } from '../core/format.js';
import { TimeChart } from '../core/chart.js';
import { toast, button, table } from '../core/ui.js';

const SPANS = [2, 5, 10, 30, 60];          // seconds shown around the cursor
const FETCH_MS = 120;                      // wait this long after the last move before asking for a window
const nan = (a) => Float64Array.from(a || [], (v) => (v == null ? NaN : v));
const enc = encodeURIComponent;

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

const readout = (label, unit) => {
  const v = h('span'), n = h('div.note');
  const el = h('div.readout', h('span.label', label), h('div.value', v, unit && h('span.unit', unit)), n);
  return { el, v, n };
};
const key = (label, color, dash) => h('span.row', { style: { gap: 'var(--sp-1)' } },
  h(`span.sw${dash ? '.dash' : ''}`, { style: { background: `var(${color})`, color: `var(${color})` } }), label);

export default {
  id: 'results',
  title: 'Results',
  mount(root) {
    // ------------------------------------------------------------ run and its files
    const pick = h('select.select', { 'aria-label': 'Run', style: { flex: '1', minWidth: '16rem' } });
    const files = h('div');
    const whole = { mse: readout('Score (MSE)', 'm²/s²'), rmse: readout('RMSE', 'm/s'), bias: readout('Bias', 'm/s'),
      sigma: readout('σ', 'm/s'), len: readout('Length', 's'), rate: readout('Rates', 'Hz') };

    // ------------------------------------------------------------ timeline (whole run) and controls
    const overHost = h('div.chart.short');
    const over = new TimeChart(overHost);
    const scrub = h('input.scrub', { type: 'range', min: 0, max: 1, step: 0.01, value: 0, 'aria-label': 'Time in the run' });
    const goto = h('input.input', { placeholder: 's, or 12:38:30', 'aria-label': 'Go to time', style: { width: '9rem' } });
    const spanSeg = h('div.segmented', { role: 'group', 'aria-label': 'Window' },
      SPANS.map((s) => h('button', { type: 'button', 'aria-pressed': String(s === S.span), onclick: () => setSpan(s) }, `${s} s`)));
    const posText = h('span.mono.muted');

    // ------------------------------------------------------------ the moment
    const at = { gt: readout('Tunnel', 'm/s'), board: readout('Sensor', 'm/s'), err: readout('Error', 'm/s'),
      wmse: readout('Window MSE', 'm²/s²'), wbias: readout('Window bias', 'm/s') };
    const atTitle = h('h3', 'At –');
    const atSub = h('span.sub');
    const speedHost = h('div.chart'), errHost = h('div.chart.short');
    const speedChart = new TimeChart(speedHost), errChart = new TimeChart(errHost);
    const bands = h('div');
    const gtKey = h('span');

    root.append(h('div.page',
      h('header.page-head', h('div', h('h1', 'Results'), h('p.part', 'Saved tunnel runs · data/tunnel/')),
        h('p.intro', 'Re-read any saved run: the Wind Tunnel Logger\'s, or the copy Record saves. Pick a time on the timeline (click, drag the slider, or type seconds or a clock time) to see the tunnel, your sensor and the error there at full resolution. ← → step through it.')),
      h('section.panel.stack',
        h('div.panel-head', h('h3', 'Run'), h('span.spacer'),
          button('Open data/tunnel', () => reveal(S.run?.dir || ''), { cls: '.quiet.sm' }),
          button('Refresh', () => list(S.run?.name), { cls: '.quiet.sm' })),
        h('div.row', pick),
        h('div.readouts', Object.values(whole).map((r) => r.el)),
        files),
      h('section.panel.stack',
        h('div.panel-head', h('h3', 'Timeline'), h('span.sub', 'whole run · click to go there'), h('span.spacer'),
          h('div.legend', gtKey, key('Sensor', '--s-sensor'), key('Error, 10 s mean', '--s-preview', true))),
        overHost, scrub,
        h('div.row',
          button('◀', () => setCursor(S.cursor - S.span / 2), { title: 'Back half a window (←)' }),
          button('▶', () => setCursor(S.cursor + S.span / 2), { title: 'On half a window (→)' }),
          h('span.label', 'Window'), spanSeg,
          h('span.spacer'),
          goto, button('Go', () => go(), { primary: true }),
          button('Largest error', () => worst(), { cls: '.quiet', title: 'Where the 10 s mean error is largest' })),
        posText),
      h('section.panel.stack',
        h('div.panel-head', atTitle, atSub),
        h('div.readouts', Object.values(at).map((r) => r.el)),
        speedHost,
        h('div.label', 'Error: sensor − tunnel'),
        errHost),
      h('section.panel.stack', h('div.panel-head', h('h3', 'By speed'), h('span.sub', 'the whole run, per 1 m/s band of the tunnel speed')), bands)));

    goto.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
    scrub.addEventListener('input', () => setCursor(Number(scrub.value)));
    pick.addEventListener('change', () => open(S.runs[Number(pick.value)]));
    over.onPick = (t) => setCursor(t);
    speedChart.onPick = errChart.onPick = (t) => setCursor(t);
    over.onHover = speedChart.onHover = errChart.onHover = () => drawAll();
    window.addEventListener('keydown', (e) => {
      if (root.hidden || !S.run || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.target.closest?.('input, select, textarea')) return;
      const step = e.shiftKey ? S.span : S.span / 10;
      if (e.key === 'ArrowLeft') { setCursor(S.cursor - step); e.preventDefault(); }
      if (e.key === 'ArrowRight') { setCursor(S.cursor + step); e.preventDefault(); }
    });

    // ------------------------------------------------------------ loading
    async function list(want) {
      try {
        const data = await (await fetch('/api/tunnel/runs')).json();
        S.runs = data.runs || [];
      } catch {
        return fill(files, h('p.faint', 'The calibration server is not reachable.'));
      }
      const when = (r) => new Date(r.mtime * 1000).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
      fill(pick, S.runs.map((r, i) => h('option', { value: String(i) }, `${r.name} · ${when(r)}`)));
      if (!S.runs.length) return fill(files, h('p.empty', 'No saved runs yet. Log one with the Wind Tunnel Logger (pnpm logger), or press Record with the GIGA connected.'));
      let saved = null;
      try { saved = localStorage.getItem('calib.results.run'); } catch { /* storage blocked */ }
      const i = Math.max(0, S.runs.findIndex((r) => r.name === (want || saved)));
      pick.value = String(i);
      await open(S.runs[i]);
    }

    async function open(r) {
      if (!r) return;
      fill(files, h('p.faint', 'Loading…'));
      const res = await fetch(`/api/tunnel/run?name=${enc(r.name)}&dir=${enc(r.dir)}`);
      const data = await res.json();
      if (!res.ok) return fill(files, h('p.faint', data.error || 'Could not read the run.'));
      S.run = data;
      S.win = null;
      try { localStorage.setItem('calib.results.run', r.name); } catch { /* storage blocked */ }
      renderRun();
      scrub.max = String(dur());
      setCursor(Math.min(dur(), S.span / 2));
    }

    function renderRun() {
      const r = S.run;
      setText(gtKey, '');
      gtKey.replaceChildren(key(gtName(), '--s-truth'));
      setText(at.gt.el.querySelector('.label'), gtName());
      const rows = [
        r.files.aligned && ['Pairs, as scored (Score.py reads this)', r.files.aligned],
        r.files.vdas && [`Every ${gtName()} reading`, r.files.vdas],
        r.files.student && ['Every sample from your sensor', r.files.student],
        r.session && ['Dashboard session: force, ρ, settings', r.session],
      ].filter(Boolean);
      fill(files, h('div.stack',
        h('p.muted', r.source === 'dashboard' ? 'Saved by Record in the dashboard, with the full session alongside.' : 'Saved by the Wind Tunnel Logger.'),
        table(['File', 'Where', ''], rows.map(([what, p]) => [what, h('span.mono.path', p), button('Show in Finder', () => reveal(p), { cls: '.quiet.sm' })]), { rightFrom: 2 })));
      setText(whole.mse.v, fixed(r.mse, 3));
      setText(whole.mse.n, `${r.pairs} pairs${r.missing ? `, ${r.missing} unpaired` : ''}`);
      setText(whole.rmse.v, fixed(r.rmse, 3));
      setText(whole.bias.v, signed(r.bias, 3));
      setText(whole.bias.n, r.bias > 0 ? 'sensor reads high' : 'sensor reads low');
      setText(whole.sigma.v, fixed(r.sigma, 3));
      setText(whole.len.v, fixed(r.duration_s, 1));
      setText(whole.len.n, `from ${clockAt(0)}`);
      setText(whole.rate.v, `${fixed(r.board_rate_hz, 0)} / ${fixed(r.gt_rate_hz, 0)}`);
      setText(whole.rate.n, `sensor / ${gtName()}`);
      fill(bands, r.bands?.length
        ? table(['Tunnel speed', 'Pairs', 'Bias', 'σ', ''], r.bands.map((b) => [
          `${b.lo}–${b.lo + 1} m/s`, String(b.n), { v: signed(b.bias, 3), cls: Math.abs(b.bias) > 0.5 ? 'bad' : Math.abs(b.bias) > 0.2 ? 'warn' : 'ok' },
          fixed(b.sigma, 3), isNum(b.t_first) ? button(`Go to ${fixed(b.t_first, 1)} s`, () => setCursor(b.t_first + S.span / 2), { cls: '.quiet.sm' }) : '',
        ]), { rightFrom: 1 })
        : h('p.empty', 'Too few pairs in any band.'));
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
      setText(posText, `t = ${fixed(S.cursor, 2)} s · ${clockAt(S.cursor)} · showing ${S.span} s`);
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
      if (!r?.series) return;
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
      setText(atSub, `t = ${fixed(S.cursor, 2)} s · window ${fixed(a, 1)}–${fixed(b, 1)} s${w?.decimated > 1 ? ` · every ${w.decimated}th sample` : ''}${fresh ? '' : ' · loading…'}`);
      if (!w) { speedChart.empty('Loading…'); errChart.empty('Loading…'); return; }
      const xf = (v) => `${fixed(v, S.span <= 5 ? 1 : 0)} s`;
      speedChart.draw({ t: w.tt, range: [w.a, w.b], unit: 'm/s', cursor: S.cursor, xFormat: xf, tipFormat: tip, emptyText: 'No samples in this window',
        lines: [
          { label: gtName(), color: '--s-truth', data: w.gt, width: 2 },
          { label: 'Sensor', color: '--s-sensor', data: w.board, width: 1.5 },
        ] });
      errChart.draw({ t: w.tt, range: [w.a, w.b], unit: 'm/s', zero: true, cursor: S.cursor, xFormat: xf, tipFormat: tip, minSpan: 0.4,
        emptyText: 'No samples in this window',
        lines: [{ label: 'Error', color: '--s-preview', data: w.error, width: 1.5 }] });

      // the samples nearest the cursor, and the score of the pairs in the window
      let i = 0, lo = 0, hi = w.tt.length - 1;
      while (lo < hi) { const k = (lo + hi) >> 1; if (w.tt[k] < S.cursor) lo = k + 1; else hi = k; }
      i = lo;
      const e = w.error[i];
      setText(at.gt.v, fixed(w.gt[i], 2));
      setText(at.board.v, fixed(w.board[i], 2));
      setText(at.err.v, signed(e, 2));
      setTone(at.err.el, !isNum(e) ? null : Math.abs(e) > 0.5 ? 'bad' : Math.abs(e) > 0.2 ? 'warn' : 'ok', ['ok', 'warn', 'bad']);
      setText(at.err.n, isNum(e) && isNum(w.gt[i]) && w.gt[i] > 0.5 ? `${signed((100 * e) / w.gt[i], 1)} % of the tunnel` : '');
      const st = w.stats || {};
      setText(at.wmse.v, fixed(st.mse, 3));
      setText(at.wmse.n, `${st.pairs ?? 0} pairs`);
      setText(at.wbias.v, signed(st.bias, 3));
      setText(at.wbias.n, isNum(st.sigma) ? `σ ${fixed(st.sigma, 3)} · tunnel mean ${fixed(st.gt_mean, 2)}` : '');
    }

    list();
    return {
      update() {},
      redraw() { if (S.run) drawAll(); },
      show(name) { if (name && name !== S.run?.name) list(decodeURIComponent(name)); },
    };
  },
};
