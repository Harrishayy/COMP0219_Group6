// The dashboard: every sensor on one screen. On a laptop it fills the viewport exactly (no page scroll): one tile
// per sensor module (js/sensors/index.js) with its status, a few readouts, its live chart, the next calibration
// step and the most serious check. A tile opens the sensor's full page (core/sensor-page.js).
//
// Tiles reuse the module's own pieces, so a number means the same thing on the tile and on the sensor page:
//   tile: { readouts: [labels from `readouts`], view: id from `views`, wide?: true, footer?(ctx) -> Node }
// A module without `tile` shows its first three readouts and its first view.
//
// overviewStrip() is the slim row of readings shown under the app bar on the sensor pages and Runs, so the
// other sensors stay in view while one is being calibrated.
import { h, fill, setText, setTone } from './dom.js';
import { TimeChart, XYChart } from './chart.js';
import { store, actions } from './store.js';
import { toast } from './ui.js';
import { progress, isLive } from './sensor-page.js';

const LEVEL_TAG = { bad: 'Fix', warn: 'Check', info: 'Note' };
const LEVEL_RANK = { bad: 0, warn: 1, info: 2 };
const SETTLED = new Set(['done', 'skip']);
const CHART_MS = 50;   // tiles redraw at most 20 times a second: three charts at 60 Hz is wasted work

/** Text of a readout as the sensor page shows it, or a dash with no live data. */
function readoutText(r, s) {
  if (!s.streaming) return '–';
  const v = r.value(s);
  return r.format ? r.format(v) : Number.isFinite(v) ? v.toFixed(r.digits ?? 2) : '–';
}

function mountTile(def, onOpen) {
  const spec = def.tile || {};
  const ctx = { store, actions, toast, refresh: () => {}, show: () => onOpen(def.id) };
  const pick = spec.readouts
    ? spec.readouts.map((l) => def.readouts.find((r) => r.label === l)).filter(Boolean)
    : (def.readouts || []).slice(0, 3);
  const view = (def.views || []).find((v) => v.id === spec.view) || def.views?.[0];

  const pill = h('span.pill', h('span.dot'), h('span'));
  const count = h('span.count');
  const open = h('button.btn.sm', { type: 'button', onclick: () => onOpen(def.id) }, 'Calibrate →');
  const head = h('header.tile-head',
    h('button.tile-title', { type: 'button', onclick: () => onOpen(def.id), title: `Open ${def.title}` }, h('h2', def.title)),
    pill, h('span.spacer'), open);

  const cells = pick.map((r) => {
    const value = h('span'), note = h('span.note');
    const el = h('div.readout', h('span.label', r.label), h('div.value', value, h('span.unit', r.unit || '')), note);
    return { r, el, value, note };
  });

  const host = h('div.chart');
  const legend = view?.legend?.length > 1 && h('div.legend', view.legend.map((l) => h('span.row', { style: { gap: 'var(--sp-1)' } },
    h(`span.sw${l.dash ? '.dash' : ''}`, { style: { background: `var(${l.color})`, color: `var(${l.color})` } }), l.label)));
  const chartBox = view && h('div.tile-chart', h('div.tile-chart-head', h('span.label', view.title || view.label), legend || null), host);
  const chart = view && (view.kind === 'xy' ? new XYChart(host) : new TimeChart(host));
  let lastDraw = -1, lastDrawAt = 0;
  if (chart) chart.onHover = () => { lastDraw = -1; };

  const next = h('button.tile-next', { type: 'button', onclick: () => onOpen(def.id) });
  const check = h('div.tile-check');
  const off = h('p.faint', { hidden: true });
  const extra = spec.footer ? spec.footer(ctx) : null;
  const el = h('section.tile', { dataset: { sensor: def.id }, 'aria-label': def.title },
    head, off, h('div.tile-readouts', cells.map((c) => c.el)), chartBox || null,
    h('footer.tile-foot', next, check, extra));
  if (spec.wide) el.classList.add('wide');

  let checkSig = null;  // null: nothing rendered yet ('' is the all-clear signature)
  function update(now, text) {
    if (chart && (store.version !== lastDraw || view.always) && now - lastDrawAt > CHART_MS) {
      lastDraw = store.version;
      lastDrawAt = now;
      try { view.draw(chart, store, new Set()); } catch (e) { console.error(e); }
    }
    if (!text) return;
    const st = def.status(store), live = isLive(def, store);
    setTone(pill, st.tone);
    setText(pill.children[1], st.text);
    el.classList.toggle('off', !live);
    off.hidden = live;
    if (!live) setText(off, def.available(store).title || 'Not read by the running firmware');
    host.parentElement.classList.toggle('stale', !store.streaming);
    for (const c of cells) {
      setText(c.value, readoutText(c.r, store));
      setText(c.note, store.streaming && c.r.note ? c.r.note(store) || '' : '');
      setTone(c.el, store.streaming && c.r.tone ? c.r.tone(store) : null);
    }
    const p = progress(def, store);
    setText(count, p.total ? `${p.done}/${p.total} done` : '');
    const step = (def.steps || []).find((x) => !SETTLED.has(x.state(store).tone));
    const sst = step?.state(store);
    fill(next, step ? [h('span.label', 'Next'), h('span.tile-next-title', step.title), sst.text && h('span.faint', sst.text), count]
      : [h('span.label', 'Calibrated'), h('span.tile-next-title', 'Every step done'), count]);
    next.className = `tile-next ${step ? sst.tone : 'done'}`;
    const items = (def.checks ? def.checks(store, ctx) : []).sort((a, b) => LEVEL_RANK[a.level] - LEVEL_RANK[b.level]);
    const top = items[0], sig = top ? `${top.level}|${top.title}|${items.length}` : '';
    if (sig !== checkSig) {
      checkSig = sig;
      fill(check, top
        ? [h(`span.tag.${top.level}`, LEVEL_TAG[top.level]), h('span.tile-check-title', top.title),
          items.length > 1 && h('span.faint', `+${items.length - 1} more`),
          top.action && h('button.btn.sm.quiet', { type: 'button', onclick: async () => { try { await top.action.run(ctx); } catch (err) { toast(err.message, 'bad'); } } }, top.action.label)]
        : [h('span.dot.ok'), h('span.faint', def.allClear || 'All checks pass.')]);
      check.className = `tile-check ${top ? top.level : 'ok'}`;
    }
  }

  return { el, update, redraw: () => { lastDraw = -1; } };
}

export function mountDashboard(root, defs, { onOpen }) {
  const tiles = defs.map((d) => mountTile(d, onOpen));
  root.append(h('div.cockpit', tiles.map((t) => t.el)));
  let lastText = 0;
  return {
    update(now) {
      const text = now - lastText > 200;
      if (text) lastText = now;
      for (const t of tiles) t.update(now, text);
    },
    redraw: () => { for (const t of tiles) t.redraw(); },
  };
}

/** The slim row of readings under the app bar. items: [{ section, label, unit, digits?, format?, value, note?, tone? }] */
export function overviewStrip(items, onOpen) {
  const cells = items.map((r) => {
    const value = h('span'), note = h('span.note');
    const el = h('button.ov', { type: 'button', title: `Open ${r.section}`, onclick: () => onOpen(r.section) },
      h('span.label', r.label), h('span.value', value, h('span.unit', r.unit || '')), note);
    return { r, el, value, note };
  });
  const el = h('div.overview', { role: 'region', 'aria-label': 'Readings' }, h('div.wrap', cells.map((c) => c.el)));
  return {
    el,
    update() {
      for (const c of cells) {
        setText(c.value, readoutText(c.r, store));
        setText(c.note, store.streaming && c.r.note ? c.r.note(store) || '' : '');
        setTone(c.el, store.streaming && c.r.tone ? c.r.tone(store) : null);
      }
    },
  };
}
