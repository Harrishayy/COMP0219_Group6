// Renders any sensor module (js/sensors/*.js) into the same page, so every sensor looks and works alike:
//
//   header       title, part, status pill, one-line intro
//   stat strip   the module's readouts
//   desk         chart panel (switchable views) and recommendations | calibrate (the ordered steps)
//   extras       optional panels, then settings, then the reference (parts, wiring, brief, bench test)
//
// A module with `wiring` (js/wiring/parts/*.js) or `datasheet` gets sub-tabs: Bench (all of the above),
// Wiring (breadboard drawing, build steps, circuit check) and Datasheet (the PDF). URL: #<id>/<sub>.
//
// With no live data (no board, silent board) readouts show a dash and charts are marked as old readings,
// so nothing on the page passes for a live value. When the module says it is not live in this build
// (`available`), only a notice and the reference show.
// A sensor module is a plain object; js/sensors/index.js documents the full contract.
import { h, fill, setText, setTone } from './dom.js';
import { TimeChart, XYChart } from './chart.js';
import { store, actions } from './store.js';
import { toast, settingField } from './ui.js';
import { referencePanel, command } from './reference.js';
import { datasheetPanel } from './datasheet.js';
import { wiringPanel } from '../wiring/render.js';

const LEVEL_TAG = { bad: 'Fix', warn: 'Check', info: 'Note' };
const LEVEL_RANK = { bad: 0, warn: 1, info: 2 };
const SETTLED = new Set(['done', 'skip']);  // 'skip': not possible on this rig (e.g. needs airflow), not a failure

/** Steps done out of the steps this rig can run (skipped ones do not count). Used for the tab badge. */
export function progress(def, s) {
  const states = (def.steps || []).map((st) => st.state(s).tone).filter((t) => t !== 'skip');
  return { done: states.filter((t) => t === 'done').length, total: states.length };
}

export const isLive = (def, s) => !def.available || def.available(s).live !== false;

export function mountSensorPage(root, def, { onSub } = {}) {
  const ctx = { store, actions, toast, refresh: () => renderSteps(true), show: (sub) => showSub(sub) };
  let lastDraw = -1, lastText = 0;

  // ---------------------------------------------------------------- header
  const pill = h('span.pill', h('span.dot'), h('span'));
  const head = h('header.page-head',
    h('div', h('h1', def.title), def.part && h('p.part', def.part)),
    pill,
    def.intro && h('p.intro', def.intro));

  // ---------------------------------------------------------------- stat strip
  const readouts = (def.readouts || []).map((r) => {
    const value = h('span'), unit = h('span.unit', r.unit || ''), note = h('div.note');
    const el = h('div.readout', h('span.label', r.label), h('div.value', value, unit), note);
    return { r, el, value, note };
  });

  // ---------------------------------------------------------------- chart panel with view switch
  const views = def.views || [];
  const chartHost = h('div.chart');
  const legend = h('div.legend');
  const viewSub = h('span.sub');
  const viewTitle = h('h3');
  const hidden = new Set();
  let view = views[0], chart = null;
  const switcher = views.length > 1 && h('div.segmented', { role: 'group', 'aria-label': 'Chart view' },
    views.map((v) => h('button', { type: 'button', 'aria-pressed': String(v === view), onclick: () => selectView(v) }, v.label)));

  function selectView(v) {
    view = v;
    hidden.clear();
    if (switcher) for (const [i, b] of [...switcher.children].entries()) b.setAttribute('aria-pressed', String(views[i] === v));
    chartHost.className = 'chart' + (v.tall ? ' tall' : '');
    chartHost.replaceChildren();
    chart = v.kind === 'xy' ? new XYChart(chartHost) : new TimeChart(chartHost);
    chart.onHover = () => { lastDraw = -1; };
    setText(viewTitle, v.title || v.label);
    setText(viewSub, v.sub || '');
    fill(legend, (v.legend?.length > 1 ? v.legend : []).map((l) => h('button', {
      type: 'button', 'aria-pressed': 'true',
      onclick: (e) => {
        const on = hidden.has(l.key);
        on ? hidden.delete(l.key) : hidden.add(l.key);
        e.currentTarget.setAttribute('aria-pressed', String(on));
        lastDraw = -1;
      },
    }, h(`span.sw${l.dash ? '.dash' : ''}`, { style: { background: `var(${l.color})`, color: `var(${l.color})` } }), l.label)));
    lastDraw = -1;
  }

  const staleNote = h('div.stale-note', { hidden: true });
  const chartPanel = views.length && h('section.panel.chart-panel',
    h('div.panel-head', viewTitle, viewSub, legend, h('span.spacer'), switcher || null),
    h('div.chart-frame', chartHost, staleNote));

  const extras = (def.panels || []).map((p) => {
    const body = h('div');
    const panel = h('section.panel', h('div.panel-head', h('h3', p.title), p.sub && h('span.sub', p.sub)), body);
    body.append(p.render(ctx));
    return { p, body, panel };
  });

  // ---------------------------------------------------------------- calibrate (steps)
  const steps = def.steps || [];
  const stepList = h('ol.steps');
  const stepCount = h('span.count');
  const stepEls = steps.map((s) => {
    const num = h('span.step-num'), state = h('span.step-state');
    const btn = h('button.step-head', { type: 'button', 'aria-expanded': 'false', onclick: () => { chosen = true; open = open === s.id ? null : s.id; renderSteps(true); } },
      num, h('span.step-title', s.title), state);
    const body = h('div.step-body');
    const li = h('li.step', btn, body);
    stepList.append(li);
    return { s, li, btn, num, state, body, key: null };
  });
  // The open step follows progress (first step not settled) until the user picks one themselves.
  let open = null, chosen = false;

  function renderSteps(force = false) {
    if (!chosen) {
      const next = steps.find((s) => !SETTLED.has(s.state(store).tone))?.id ?? null;  // all settled: none open
      if (next !== open) { open = next; force = true; }
    }
    let n = 0;
    for (const e of stepEls) {
      const st = e.s.state(store), isOpen = e.s.id === open;
      if (st.tone !== 'skip') n++;
      e.li.className = `step ${st.tone}${isOpen ? ' open' : ''}`;
      setText(e.num, st.tone === 'skip' ? '–' : st.tone === 'done' && !isOpen ? '✓' : String(n));
      setText(e.state, st.text || '');
      e.btn.setAttribute('aria-expanded', String(isOpen));
      e.body.hidden = !isOpen;
      if (!isOpen) {
        if (e.key !== null) e.body.replaceChildren();  // closed steps keep no hidden forms around
        e.key = null;
        continue;
      }
      const key = e.s.key ? String(e.s.key(store)) : '';
      if (force || e.key !== key) {
        fill(e.body, e.s.why && h('p.why', e.s.why), e.s.body(ctx));
        e.key = key;
      }
      e.s.update?.(e.body, store);
    }
    const p = progress(def, store);
    setText(stepCount, `${p.done}/${p.total}`);
  }

  // ---------------------------------------------------------------- recommendations
  const checkList = h('ul.checks');
  const checkCount = h('span.count');
  let checkSig = null, checkEls = [];
  function renderChecks() {
    const items = (def.checks ? def.checks(store, ctx) : []).sort((a, b) => LEVEL_RANK[a.level] - LEVEL_RANK[b.level]);
    setText(checkCount, String(items.length));
    const sig = items.map((c) => `${c.id}|${c.level}|${c.title}|${c.action?.label || ''}`).join('\n');
    if (sig === checkSig) {
      items.forEach((c, i) => setText(checkEls[i], c.detail || ''));
      return;
    }
    checkSig = sig;
    checkEls = [];
    if (!items.length) {
      fill(checkList, h('li.all-clear', h('span.dot.ok'), def.allClear || 'All checks pass.'));
      return;
    }
    fill(checkList, items.map((c) => {
      const detail = h('p.detail', c.detail || '');
      checkEls.push(detail);
      return h(`li.check-item.${c.level}`,
        h('div.head', h('span.tag', LEVEL_TAG[c.level]), h('span.title', c.title)),
        detail,
        c.action && h('div.row', h('button.btn.sm', {
          type: 'button',
          onclick: async (e) => {
            const b = e.currentTarget;
            b.disabled = true;
            try { await c.action.run(ctx); } catch (err) { toast(err.message, 'bad'); } finally { b.disabled = false; }
          },
        }, c.action.label)));
    }));
  }

  // ---------------------------------------------------------------- settings
  const settings = def.settings;
  const fields = settings ? settings.fields.map(settingField) : [];
  const settingsSection = settings && h('section',
    h('h2.section-title', settings.title || 'Settings', settings.sub && h('small', settings.sub)),
    h('div.panel.stack', h('div.settings', fields), settings.footer && settings.footer(ctx)));

  // ---------------------------------------------------------------- not in this build
  const offTitle = h('h3'), offDetail = h('p'), offCmds = h('div.stack', { style: { gap: 'var(--sp-2)' } });
  const offPanel = h('section.panel.notice', offTitle, offDetail, offCmds);
  let offSig = null;

  // ---------------------------------------------------------------- assemble
  const liveView = h('div.page',
    readouts.length && h('div.readouts', readouts.map((r) => r.el)),
    h('div.desk',
      h('div.desk-left',
        chartPanel || null,
        h('section.panel.flush', h('div.panel-head', h('h3', 'Recommendations'), checkCount), checkList)),
      h('section.panel.flush', { 'aria-label': 'Calibrate' }, h('div.panel-head', h('h3', 'Calibrate'), stepCount, def.calibrateSub && h('span.sub', def.calibrateSub)), stepList)),
    extras.map((x) => x.panel),
    settingsSection || null);
  const bench = h('div.page', liveView, offPanel, referencePanel(def.info));

  // ---------------------------------------------------------------- sub-tabs: bench, wiring, datasheet
  const wiring = def.wiring && wiringPanel(def.wiring, { live: def.wiringLive });
  const sheet = def.datasheet && datasheetPanel(def.datasheet);
  const subs = [
    { id: 'bench', label: 'Bench', el: bench },
    wiring && { id: 'wiring', label: 'Wiring', el: wiring.root },
    sheet && { id: 'datasheet', label: 'Datasheet', el: sheet.root },
  ].filter(Boolean);
  let sub = 'bench';
  const subNav = subs.length > 1 && h('div.segmented.subtabs', { role: 'tablist', 'aria-label': `${def.title} sections` },
    subs.map((x) => h('button', { type: 'button', role: 'tab', dataset: { sub: x.id }, onclick: () => showSub(x.id, true) }, x.label)));
  function showSub(id, fromUser = false) {
    if (!subs.some((x) => x.id === id)) id = 'bench';
    sub = id;
    for (const x of subs) x.el.hidden = x.id !== id;
    if (subNav) for (const b of subNav.children) b.setAttribute('aria-selected', String(b.dataset.sub === id));
    if (id === 'datasheet') sheet.show();
    if (id === 'bench') lastDraw = -1;
    if (fromUser) onSub?.(id);
  }
  root.append(h('div.page', head, subNav || null, subs.map((x) => x.el)));
  showSub('bench');

  if (views.length) selectView(view);

  // ---------------------------------------------------------------- update loop (driven by main.js)
  function update(now) {
    if (now - lastText > 200) {
      lastText = now;
      if (wiring && sub === 'wiring') wiring.update(store);
      const st = def.status(store);
      setTone(pill, st.tone);
      setText(pill.children[1], st.text);
      const av = def.available ? def.available(store) : { live: true };
      liveView.hidden = av.live === false;
      offPanel.hidden = av.live !== false;
      if (av.live === false) {
        const sig = `${av.title}|${av.detail}|${(av.commands || []).join()}`;
        if (sig !== offSig) {
          offSig = sig;
          setText(offTitle, av.title || 'Not read by the running firmware');
          setText(offDetail, av.detail || '');
          fill(offCmds, (av.commands || []).map(command));
        }
        return;
      }
      const live = store.streaming;
      if (chartPanel) {
        chartPanel.classList.toggle('stale', !live);
        staleNote.hidden = live || !(store.buf.n || store.envHistory.length);  // empty charts say so themselves
        setText(staleNote, store.isSim ? '' : store.conn !== 'open' ? 'Server offline: not live' : 'No live data from the board: last readings shown');
      }
      for (const r of readouts) {
        if (!live) {  // never show a held value as if it were live
          setText(r.value, '–');
          setText(r.note, r.r.note ? 'no live data' : '');
          setTone(r.el, null);
          continue;
        }
        const v = r.r.value(store);
        setText(r.value, r.r.format ? r.r.format(v) : Number.isFinite(v) ? v.toFixed(r.r.digits ?? 2) : '–');
        setText(r.note, r.r.note ? r.r.note(store) || '' : '');
        setTone(r.el, r.r.tone ? r.r.tone(store) : null);
      }
      for (const x of extras) x.p.update?.(x.body, store);
      renderSteps();
      renderChecks();
      for (const f of fields) f.update();
    }
    if (!liveView.hidden && view && chart && (store.version !== lastDraw || view.always)) {
      lastDraw = store.version;
      try { view.draw(chart, store, hidden); } catch (e) { console.error(e); }
    }
  }

  return { update, redraw: () => { lastDraw = -1; }, show: (id) => showSub(id || 'bench') };
}
