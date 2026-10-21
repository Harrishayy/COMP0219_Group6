// App shell: app bar (mascot, one tab per sensor from js/sensors/index.js, then Runs), the board banner, hash
// routing (#<page> or #<page>/<sub-tab>) and the update loop. Tabs show step progress; a part the running
// firmware does not read is dimmed with a dash. The browser tab gets Sock as its icon, with a status dot.
import { h, setText, setTone, $ } from './core/dom.js';
import { store, actions, connectServer } from './core/store.js';
import { toast } from './core/ui.js';
import { mountSensorPage, progress, isLive } from './core/sensor-page.js';
import { sock, favicon } from './core/sock.js';
import { command } from './core/reference.js';
import { mountConnection } from './pages/connection.js';
import { duration } from './core/format.js';
import sensors from './sensors/index.js';
import runs from './pages/runs.js';

const pages = [
  ...sensors.map((def) => ({ id: def.id, title: def.title, def, mount: (root) => mountSensorPage(root, def, { onSub: (sub) => setHash(def.id, sub) }) })),
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

// ------------------------------------------------------------------------------- board banner
// Says plainly when nothing on screen is live: no board, a silent board, the simulator or a replay. Hidden
// only while the real board streams.
const FLASH = 'cd firmware && pio run -e airspeed_drag_plate -t upload';
const bannerTitle = h('strong'), bannerText = h('span'), bannerAct = h('div.row');
const banner = h('div.banner', { role: 'status', 'aria-live': 'polite', hidden: true },
  h('div.wrap', h('span.dot'), h('div.banner-body', bannerTitle, bannerText), bannerAct));
let bannerSig = '';

function bannerState() {
  const t = store.tele, st = store.boardState;
  const useBoard = ['Use the board', () => actions.connect({ source: 'serial', sensor_port: 'auto', baud: 115200, gt_kind: 'none' })];
  switch (st) {
    case 'offline': return { tone: 'bad', title: 'Calibration server not running.', text: 'Start it with pnpm dev in the repo, then this page reconnects by itself.' };
    case 'sim': return { tone: 'warn', title: 'Simulator.', text: 'Every reading on this page is synthetic, not from hardware.', act: useBoard };
    case 'replay': return { tone: 'warn', title: 'Replaying a recorded run.', text: `${t.port_sensor || ''}: not live.`, act: useBoard };
    case 'absent': return { tone: 'bad', title: 'No Nucleo found.', text: 'Plug the ST-LINK USB (CN1, by the reset button) into this laptop with a data cable. The bench connects as soon as it appears; until then nothing here is live.' };
    case 'closed': return { tone: 'bad', title: 'Cannot open the board’s port.', text: `${t.board_detail || ''}. Close any serial monitor (pio device monitor) that holds it.` };
    case 'opening': return { tone: 'warn', title: 'Connecting to the board…', text: t.board_detail || '' };
    case 'silent': return { tone: 'bad', title: 'The board sends nothing.', text: `${t.board_detail || ''}. It may be running a bench app or no firmware: flash the competition build.`, cmd: FLASH };
    case 'no_data': return { tone: 'warn', title: 'The board answers but streams no readings.', text: 'It may be in MODE V (velocity only) or running another app.', act: ['Send MODE D', () => actions.command('MODE D')], cmd: FLASH };
    case 'none': return { tone: 'warn', title: 'No board port configured.', text: 'Only the pitot is connected. Open Connection to pick the Nucleo.', act: useBoard };
    default: return null;
  }
}

function updateBanner() {
  const b = bannerState();
  banner.hidden = !b;
  if (!b) { bannerSig = ''; return; }
  const sig = `${b.tone}|${b.title}|${b.text}`;
  if (sig === bannerSig) return;
  bannerSig = sig;
  banner.className = `banner ${b.tone}`;
  setTone(banner.querySelector('.dot'), b.tone);
  setText(bannerTitle, b.title);
  setText(bannerText, b.text);
  bannerAct.replaceChildren(...[b.cmd && command(b.cmd), b.act && h('button.btn.sm', { type: 'button', onclick: b.act[1] }, b.act[0])].filter(Boolean));
}

// -------------------------------------------------------------------------- browser tab: icon and title
const icon = document.querySelector('link[rel="icon"]') || document.head.appendChild(h('link', { rel: 'icon', type: 'image/svg+xml' }));
let iconSig = '';
function updateIcon(md) {
  const tone = { streaming: 'ok', sim: 'warn', replay: 'warn', opening: 'warn', no_data: 'warn' }[store.boardState] || 'bad';
  const sig = `${md}|${tone}`;
  if (sig === iconSig) return;
  iconSig = sig;
  icon.href = favicon(md, tone);
}

document.body.append(
  h('header.topbar', h('div.wrap',
    h('div.brand', mascot, h('b', 'Bench'), context),
    tablist,
    h('div.topbar-end', recChip, conn.chip))),
  banner,
  h('main.main', { id: 'main' }, h('div.wrap', { id: 'pages' })));

// ------------------------------------------------------------------------------------------ pages
const mounted = new Map();
let current = null;

function setHash(id, sub) {
  const want = `#${id}${sub && sub !== 'bench' ? `/${sub}` : ''}`;
  if (location.hash !== want) history.replaceState(null, '', want);
}

function go(route) {
  let [id, sub] = String(route || '').split('/');
  if (!pages.some((p) => p.id === id)) id = pages[0].id;
  if (sub || !location.hash.startsWith(`#${id}/`)) setHash(id, sub);
  if (id === current) { if (sub) mounted.get(id)?.page.show?.(sub); return; }
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
  if (sub) mounted.get(id).page.show?.(sub);
  document.title = `${pages.find((p) => p.id === id).title} · Airspeed Bench`;
}

window.addEventListener('hashchange', () => go(location.hash.slice(1)));
let saved = null;
try { saved = localStorage.getItem('calib.tab'); } catch { /* storage blocked */ }
go(location.hash.slice(1) || saved || pages[0].id);
updateIcon('idle');

// ------------------------------------------------------------------- live reload (pnpm dev, SPEC §6 `reload`)
// A CSS change is swapped in place; anything else reloads the page on the same tab, sub-tab and scroll position.
store.on('reload', (m) => {
  if (m.css_only) {
    for (const link of document.querySelectorAll('link[rel="stylesheet"]')) {
      const u = new URL(link.href);
      u.searchParams.set('v', String(Date.now()));
      link.href = u.href;
    }
    toast(`Styles updated (${m.files.join(', ')}).`);
    return;
  }
  try { sessionStorage.setItem('calib.scroll', String(window.scrollY)); } catch { /* storage blocked */ }
  location.reload();
});
try {
  const y = sessionStorage.getItem('calib.scroll');
  if (y !== null) {
    sessionStorage.removeItem('calib.scroll');
    setTimeout(() => window.scrollTo(0, Number(y)), 60);
  }
} catch { /* storage blocked */ }

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
  const t = m.telemetry || {};
  toast(m.source === 'sim' ? 'Simulator: readings are synthetic.' : m.source === 'replay' ? 'Replaying a recorded run.'
    : t.sensor_connected ? `Connected to the board on ${t.port_sensor}.` : 'Waiting for the board.', m.source === 'serial' && !t.sensor_connected ? 'bad' : 'ok');
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
    updateBanner();
    updateIcon(md);
    const method = store.tele.method || store.boardMethod;
    setText(context, `Nucleo-F411RE${METHOD_LABEL[method] ? ' · ' + METHOD_LABEL[method] : ''}`);
    const rec = store.session?.recording;
    recChip.hidden = !rec;
    if (rec) setText(recChip.children[1], `Recording ${duration(store.session.elapsed_s)}`);
  }
}
requestAnimationFrame(loop);
connectServer();
