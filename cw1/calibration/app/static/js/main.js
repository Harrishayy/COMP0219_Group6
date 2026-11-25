// App shell: app bar (mascot, Dashboard, one page per sensor from js/sensors/index.js, Runs, the record button),
// the strip of readings under it, the board banner, hash routing, keyboard keys and the update loop.
//
//   #dashboard   every sensor on one screen (core/dashboard.js); the default
//   #<sensor>    that sensor's calibration page (core/sensor-page.js), #<sensor>/<sub-tab> for wiring or datasheet
//   #runs        recordings, export, replay
//   #results     saved tunnel runs: files, score, any moment at full resolution (#results/<run>)
//
// Keys 1–6 open the pages in top-bar order (not while typing). The strip keeps every sensor's readings in view on
// the sensor pages and Runs. The browser tab gets Sock as its icon, with a status dot.
import { h, setText, setTone, $ } from './core/dom.js';
import { store, actions, connectServer } from './core/store.js';
import { toast } from './core/ui.js';
import { mountSensorPage, progress, isLive } from './core/sensor-page.js';
import { mountDashboard, overviewStrip } from './core/dashboard.js';
import { sock, favicon } from './core/sock.js';
import { command } from './core/reference.js';
import { mountConnection } from './pages/connection.js';
import { duration } from './core/format.js';
import sensors from './sensors/index.js';
import runs from './pages/runs.js';
import results from './pages/results.js';
import loadCellLab from './sensors/load-cell-lab.js';

function sensorDef(def) {
  if (!store.tele.loadcell_lab) return def;
  if (def.id === 'load-cell') return loadCellLab;
  return { ...def, available: () => ({ live: false, title: 'Load-cell lab firmware is running',
    detail: 'This firmware reads the load cell. Open the Load cell tab for live readings and calibration.', commands: [] }) };
}

// The strip reuses the sensors' own readouts, so a number means the same thing everywhere.
function fromReadout(section, label, as) {
  const r = sensors.find((d) => d.id === section)?.readouts?.find((x) => x.label === label);
  return r && { ...r, section, label: as || r.label };
}
const OVERVIEW = [
  fromReadout('temperature', 'Air temperature'),
  fromReadout('temperature', 'Air density'),
  fromReadout('load-cell', 'Load'),
  fromReadout('wind-speed', 'Sensor', 'Wind speed'),
  fromReadout('wind-speed', 'Tunnel'),
  fromReadout('wind-speed', 'Error'),
].filter(Boolean);

const DASH = 'dashboard';
const pages = [
  { id: DASH, title: 'Dashboard', mount: (root) => mountDashboard(root, sensors.map(sensorDef), { onOpen: (id) => go(id) }) },
  ...sensors.map((def) => ({ id: def.id, title: def.title, get def() { return sensorDef(def); }, mount: (root) => mountSensorPage(root, sensorDef(def), { onSub: (sub) => setHash(def.id, sub) }) })),
  { id: runs.id, title: runs.title, mount: (root) => runs.mount(root) },
  { id: results.id, title: results.title, mount: (root) => results.mount(root) },
];
// Older links land on the page that now holds them.
const ALIAS = { airspeed: 'wind-speed', mcp9808: 'temperature', environment: 'temperature' };

// ---------------------------------------------------------------------------------------- app bar
const conn = mountConnection();
const mascot = h('span', { 'aria-live': 'off' });
const context = h('span', 'Nucleo-F411RE');
// Record: always in the bar. It records what the dashboard sees (board, force, ρ, ground truth) into calibration/runs/;
// with tunnel ground truth it is also saved as a Wind Tunnel Logger run (data/tunnel/), listed in Tunnel runs.
const recTime = h('span.t');
const recBtn = h('button.btn.sm.rec-btn', {
  type: 'button', 'aria-pressed': 'false', title: 'Record a session (Runs lists them)',
  onclick: () => {
    const on = !store.session?.recording;
    actions.record(on);
    if (on) toast('Recording. Press Stop to save it in Runs.');
  },
}, h('span.dot'), h('span.l', 'Record'), recTime);
const tabs = pages.map((p, i) => {
  const badge = h('span.c');
  const el = h('button.tab', { type: 'button', id: `tab-${p.id}`, title: `${p.title} (key ${i + 1})`, onclick: () => go(p.id) },
    p.title, p.def ? badge : null, h('kbd', String(i + 1)));
  return { p, el, badge };
});
const tablist = h('nav.tabs', { 'aria-label': 'Pages' },
  tabs.map((t, i) => [t.p.id === runs.id || i === 1 ? h('span.sep', { 'aria-hidden': 'true' }) : null, t.el]));
const strip = overviewStrip(OVERVIEW, (id) => go(id));
let selected = null;
function markTab(id) {
  if (id === selected) return;
  selected = id;
  for (const t of tabs) {
    if (t.p.id === id) t.el.setAttribute('aria-current', 'location'); else t.el.removeAttribute('aria-current');
  }
}

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
    h('div.topbar-end', recBtn, conn.chip)),
    strip.el),
  banner,
  h('main.main', { id: 'main' }, h('div.wrap', { id: 'pages' })));

// ------------------------------------------------------------------------------------------ pages
const mounted = new Map();
let current = null;

function setHash(id, sub) {
  const want = `#${id}${sub && sub !== 'bench' ? `/${sub}` : ''}`;
  if (location.hash !== want) history.replaceState(null, '', want);
}

function showPage(id) {
  if (id === current) return;
  current = id;
  try { localStorage.setItem('calib.tab', id); } catch { /* storage blocked */ }
  if (!mounted.has(id)) {
    const root = h('div', { id: `page-${id}` });
    $('#pages').append(root);
    mounted.set(id, { root, page: pages.find((p) => p.id === id).mount(root) });
  }
  for (const [pid, m] of mounted) m.root.hidden = pid !== id;
  mounted.get(id).page.redraw?.();
  document.title = `${pages.find((p) => p.id === id).title} · Airspeed Bench`;
}

/** #<page> or #<page>/<sub-tab>; anything unknown is the dashboard. */
function go(route) {
  let [id, sub] = String(route || '').split('/');
  id = ALIAS[id] || id;
  if (!pages.some((p) => p.id === id)) id = DASH;
  setHash(id, sub);
  const changed = id !== current;
  showPage(id);
  if (sub) mounted.get(id).page.show?.(sub);
  markTab(id);
  // the dashboard and Results fill the screen with no page scroll (Results from a taller screen: it holds more); the
  // other pages scroll from the top. Results is about a saved run, so the live readings strip stays off there too
  document.body.classList.toggle('fit', id === DASH);
  document.body.classList.toggle('fit-results', id === results.id);
  strip.el.hidden = id === DASH || id === results.id;
  if (changed) window.scrollTo(0, 0);
}

window.addEventListener('hashchange', () => go(location.hash.slice(1)));
// 1–6: the pages in top-bar order. Not while typing, and not with a modifier (browser shortcuts keep working).
window.addEventListener('keydown', (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey || e.repeat) return;
  if (e.target.closest?.('input, select, textarea, [contenteditable]')) return;
  const i = Number(e.key) - 1;
  if (Number.isInteger(i) && i >= 0 && i < pages.length) { go(pages[i].id); tabs[i].el.focus({ preventScroll: true }); }
});
let saved = null;
try { saved = localStorage.getItem('calib.tab'); } catch { /* storage blocked */ }
go(location.hash.slice(1) || saved || DASH);
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
store.on('session', (m) => {
  if (m.recording !== false) return;
  const run = m.tunnel_run;
  toast(run ? `Recording saved in Runs, and as tunnel run ${run.name} (Wind speed › Tunnel runs).` : 'Recording saved: see Runs.', 'ok', run ? 7000 : 4000);
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
let labMode = false;

function mood() {
  if (!store.streaming) return 'idle';
  const live = sensors.map(sensorDef).filter((d) => isLive(d, store));
  if (live.some((d) => d.status(store).tone === 'bad')) return 'fault';
  const ps = live.map((d) => progress(d, store));
  return ps.length && ps.every((p) => p.total && p.done === p.total) ? 'done' : 'stream';
}

function loop(now) {
  requestAnimationFrame(loop);
  const nextLabMode = !!store.tele.loadcell_lab;
  if (nextLabMode !== labMode) {
    labMode = nextLabMode;
    for (const [id, m] of mounted) {
      m.root.replaceChildren();
      m.page = pages.find((p) => p.id === id).mount(m.root);
    }
  }
  const m = mounted.get(current);
  if (m) {
    try { m.page.update(now); } catch (e) { console.error(e); }
  }
  if (now - lastChrome > 250) {
    lastChrome = now;
    conn.update();
    if (current !== DASH) strip.update();
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
    setText(context, `Nucleo-F411RE${labMode ? ' · load-cell lab' : METHOD_LABEL[method] ? ' · ' + METHOD_LABEL[method] : ''}`);
    const rec = !!store.session?.recording;
    recBtn.setAttribute('aria-pressed', String(rec));
    setText(recBtn.querySelector('.l'), rec ? 'Stop' : 'Record');
    setText(recTime, rec ? duration(store.session.elapsed_s) : '');
    setTone(recBtn.querySelector('.dot'), rec ? 'bad' : null);
    recBtn.querySelector('.dot').classList.toggle('live', rec);
  }
}
requestAnimationFrame(loop);
connectServer();
