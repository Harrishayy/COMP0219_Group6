// App shell: app bar (mascot, one tab per sensor from js/sensors/index.js, then Runs), hash routing and the
// update loop. Tabs show step progress; a part the running firmware does not read is dimmed with a dash.
import { h, setText, setTone, $ } from './core/dom.js';
import { store, connectServer } from './core/store.js';
import { toast } from './core/ui.js';
import { mountSensorPage, progress, isLive } from './core/sensor-page.js';
import { sock } from './core/sock.js';
import { mountConnection } from './pages/connection.js';
import { duration } from './core/format.js';
import sensors from './sensors/index.js';
import runs from './pages/runs.js';

const pages = [
  ...sensors.map((def) => ({ id: def.id, title: def.title, def, mount: (root) => mountSensorPage(root, def) })),
  { id: runs.id, title: runs.title, mount: (root) => runs.mount(root) },
];

// ---------------------------------------------------------------------------------------- app bar
const conn = mountConnection();
const mascot = h('span', { 'aria-live': 'off' });
const context = h('span', 'Nucleo-F411RE');
const recChip = h('button.pill.rec', { type: 'button', hidden: true, onclick: () => go('runs') }, h('span.dot.bad.live'), h('span'));
const tabs = pages.map((p) => {
  const badge = h('span.c');
  const el = h('button.tab', { type: 'button', role: 'tab', id: `tab-${p.id}`, 'aria-controls': `page-${p.id}`, 'aria-selected': 'false', onclick: () => go(p.id) }, p.title, p.def ? badge : null);
  return { p, el, badge };
});
const tablist = h('nav.tabs', { role: 'tablist', 'aria-label': 'Sensors' },
  tabs.map((t, i) => [i === tabs.length - 1 ? h('span.sep', { 'aria-hidden': 'true' }) : null, t.el]));
tablist.addEventListener('keydown', (e) => {
  if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
  const i = pages.findIndex((p) => p.id === current), j = (i + (e.key === 'ArrowRight' ? 1 : pages.length - 1)) % pages.length;
  go(pages[j].id);
  tabs[j].el.focus();
});

document.body.append(
  h('header.topbar', h('div.wrap',
    h('div.brand', mascot, h('b', 'Bench'), context),
    tablist,
    h('div.topbar-end', recChip, conn.chip))),
  h('main.main', { id: 'main' }, h('div.wrap', { id: 'pages' })));

// ------------------------------------------------------------------------------------------ pages
const mounted = new Map();
let current = null;

function go(id) {
  if (!pages.some((p) => p.id === id)) id = pages[0].id;
  if (location.hash !== `#${id}`) history.replaceState(null, '', `#${id}`);
  if (id === current) return;
  current = id;
  try { localStorage.setItem('calib.tab', id); } catch { /* storage blocked */ }
  for (const t of tabs) t.el.setAttribute('aria-selected', String(t.p.id === id));
  if (!mounted.has(id)) {
    const root = h('div', { id: `page-${id}`, role: 'tabpanel', 'aria-labelledby': `tab-${id}` });
    $('#pages').append(root);
    mounted.set(id, { root, page: pages.find((p) => p.id === id).mount(root) });
  }
  for (const [pid, m] of mounted) m.root.hidden = pid !== id;
  mounted.get(id).page.redraw?.();
  document.title = `${pages.find((p) => p.id === id).title} · Bench`;
}

window.addEventListener('hashchange', () => go(location.hash.slice(1)));
let saved = null;
try { saved = localStorage.getItem('calib.tab'); } catch { /* storage blocked */ }
go(location.hash.slice(1) || saved || pages[0].id);

// ---------------------------------------------------------------------- errors from the server as toasts
let lastErr = { msg: '', t: 0 };
store.on('log', (e) => {
  if (e.level !== 'error') return;
  const now = Date.now();
  if (e.msg === lastErr.msg && now - lastErr.t < 8000) return;
  lastErr = { msg: e.msg, t: now };
  toast(e.msg.replace(/^#err\s*/, 'Board: '), 'bad', 6000);
});
store.on('hello', (m) => {
  toast(m.source === 'sim' ? 'Connected to the simulator.' : m.source === 'replay' ? 'Replaying a recorded run.' : 'Connected to the board.');
  for (const [, x] of mounted) x.page.redraw?.();
});

// -------------------------------------------------------------------------------------------- loop
const METHOD_LABEL = { drag_plate: 'drag plate', cup_hall: 'cup + Hall', cup_encoder: 'cup + encoder' };
let lastChrome = 0, lastMood = '';

function mood() {
  if (!store.streaming) return 'idle';
  const def = pages.find((p) => p.id === current)?.def;
  if (!def || !isLive(def, store)) return 'stream';
  if (def.status(store).tone === 'bad') return 'fault';
  const p = progress(def, store);
  return p.total && p.done === p.total ? 'done' : 'stream';
}

function loop(now) {
  requestAnimationFrame(loop);
  const m = mounted.get(current);
  if (m) {
    try { m.page.update(now); } catch (e) { console.error(e); }
  }
  if (now - lastChrome > 250) {
    lastChrome = now;
    conn.update();
    for (const t of tabs) {
      if (!t.p.def) continue;
      const live = isLive(t.p.def, store), st = t.p.def.status(store), p = progress(t.p.def, store);
      t.el.classList.toggle('off', !live);
      setText(t.badge, !live ? '–' : st.tone === 'bad' ? '!' : p.total ? `${p.done}/${p.total}` : '·');
      setTone(t.badge, !live ? null : st.tone === 'bad' ? 'bad' : p.total && p.done === p.total ? 'ok' : null);
      t.el.title = live ? `${t.p.title}: ${st.text}` : `${t.p.title}: not in this build`;
    }
    const md = mood();
    if (md !== lastMood) { lastMood = md; mascot.innerHTML = sock(md, 30); }
    const method = store.tele.method || store.boardMethod;
    setText(context, `Nucleo-F411RE${METHOD_LABEL[method] ? ' · ' + METHOD_LABEL[method] : ''}`);
    const rec = store.session?.recording;
    recChip.hidden = !rec;
    if (rec) setText(recChip.children[1], `Recording ${duration(store.session.elapsed_s)}`);
  }
}
requestAnimationFrame(loop);
connectServer();
