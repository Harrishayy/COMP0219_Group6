// Connection: the app-bar chip (dots for board · pitot · environment, then the source in words) and its
// popover to switch between the board and the simulator, pick the pitot source and send raw board commands.
import { h, fill, setText, setTone } from '../core/dom.js';
import { fixed } from '../core/format.js';
import { store, actions } from '../core/store.js';
import { button } from '../core/ui.js';

const GT_KINDS = [
  ['cmd', 'Lab script (command)', 'python3 gt.py'],
  ['module', 'Python function', 'gt.py:read_sample'],
  ['port', 'Serial port', '/dev/cu.usbserial-…'],
  ['none', 'None', ''],
];
const PROFILE_LABELS = { steps: 'Steps 2–10 m/s', ramps: 'Ramps', mixed: 'Mixed steps and ramps', sweep: 'Slow sweep', gusts: 'Gusts' };

export function chipState() {
  const t = store.tele;
  if (store.conn !== 'open') return { tone: 'bad', text: store.conn === 'connecting' ? 'Connecting…' : 'Server offline' };
  if (store.source === 'sim') return { tone: 'ok', text: store.hasPitot ? 'Simulator' : 'Simulator · bench' };
  if (store.source === 'replay') return { tone: 'warn', text: 'Replaying a run' };
  if (!t.sensor_connected) return { tone: 'bad', text: 'Board disconnected' };
  return { tone: t.sensor_hz > 190 ? 'ok' : 'warn', text: `Board · ${fixed(t.sensor_hz, 0)} Hz${store.hasPitot ? '' : ' · bench'}` };
}

export function mountConnection() {
  const dotBoard = h('span.dot'), dotPitot = h('span.dot'), dotEnv = h('span.dot'), chipText = h('span');
  const chip = h('button.health', { type: 'button', popovertarget: 'connection', 'aria-haspopup': 'dialog', title: 'Board · pitot · environment' },
    h('span.dots', { 'aria-hidden': 'true' }, dotBoard, dotPitot, dotEnv), chipText);

  // ---- status
  const st = {};
  const statusRow = (k, label) => { st[k] = h('dd'); return [h('dt', label), st[k]]; };
  const status = h('dl.status-list', statusRow('server', 'Server'), statusRow('board', 'Board'), statusRow('pitot', 'Pitot'), statusRow('drops', 'Dropped samples'));

  // ---- board form
  const port = h('select.select', { 'aria-label': 'Nucleo serial port', style: { flex: '1' } });
  const gtKind = h('select.select', { 'aria-label': 'Pitot source' }, GT_KINDS.map(([v, l]) => h('option', { value: v }, l)));
  const gtValue = h('input.input', { 'aria-label': 'Pitot source detail', style: { flex: '1' } });
  const gtPressure = h('input', { type: 'checkbox' });
  const syncGt = () => {
    const k = GT_KINDS.find(([v]) => v === gtKind.value);
    gtValue.placeholder = k[2];
    gtValue.disabled = k[0] === 'none';
  };
  gtKind.addEventListener('change', syncGt);
  gtKind.value = 'none';
  syncGt();
  const boardForm = h('div.stack',
    h('div.field', h('label', 'Nucleo port'), h('div.row', port, button('Refresh', () => actions.ports(), { cls: '.sm' }))),
    h('div.field', h('label', 'Pitot (ground truth)'), h('div.row', gtKind, gtValue)),
    h('label.check', gtPressure, 'The script prints pressure in Pa, not m/s'),
    h('div.row', button('Connect', () => {
      if (!port.value) return actions.log('error', 'Pick the Nucleo port first');
      actions.connect({
        source: 'serial', sensor_port: port.value, baud: 115200, gt_kind: gtKind.value,
        gt_value: gtValue.value.trim() || undefined, gt_pressure: gtPressure.checked,
      });
    }, { primary: true })));

  // ---- simulator form
  const profile = h('select.select', { 'aria-label': 'Simulated fan profile', style: { flex: '1' } });
  // Sensors are tested both on the open bench and in the tunnel; the simulator can play either.
  const RIGS = [['tunnel', 'Tunnel: fan and pitot'], ['bench', 'Bench: no airflow, no pitot']];
  const rigSeg = h('div.segmented', { role: 'group', 'aria-label': 'Simulated rig' },
    RIGS.map(([v, l]) => h('button', { type: 'button', dataset: { v }, onclick: () => setRig(v) }, l)));
  const showRig = () => {
    const rig = store.sim?.pitot === 'off' ? 'bench' : 'tunnel';
    for (const b of rigSeg.children) b.setAttribute('aria-pressed', String(b.dataset.v === rig));
  };
  const setRig = (v) => { actions.sim(v === 'bench' ? { fan: 'off', pitot: 'off' } : { fan: 'on', pitot: 'on' }); showRig(); };
  store.on('hello', showRig);
  const simForm = h('div.stack',
    h('p.muted', 'A simulated drag plate, 1 kg load cell and sensor stack, in the tunnel or on the bench. Use it to learn the steps without hardware.'),
    h('div.field', h('label', 'Fan profile'), h('div.row', profile, button('Apply', () => actions.sim({ profile: profile.value }), { cls: '.sm' }))),
    h('div.field', h('label', 'Rig'), rigSeg),
    h('div.row', button('Start simulator', () => actions.connect({ source: 'sim', profile: profile.value || undefined }), { primary: true })));

  let tab = 'board';
  const seg = h('div.segmented', { role: 'group', 'aria-label': 'Data source' },
    [['board', 'Board'], ['sim', 'Simulator']].map(([v, l]) => h('button', { type: 'button', dataset: { v }, onclick: () => { tab = v; renderTab(); } }, l)));
  const renderTab = () => {
    for (const b of seg.children) b.setAttribute('aria-pressed', String(b.dataset.v === tab));
    boardForm.hidden = tab !== 'board';
    simForm.hidden = tab !== 'sim';
  };

  // ---- raw command
  const cmd = h('input.input', { placeholder: 'TARE, GET, ENV, STATS, MODE D…', 'aria-label': 'Board command', style: { flex: '1' } });
  const sendCmd = () => { if (cmd.value.trim()) { actions.command(cmd.value.trim()); cmd.value = ''; } };
  cmd.addEventListener('keydown', (e) => { if (e.key === 'Enter') sendCmd(); });

  const pop = h('div.popover', { id: 'connection', popover: 'auto', role: 'dialog', 'aria-label': 'Connection' },
    h('h2', 'Connection'), status, h('hr'),
    h('div.row', h('span.label', 'Source'), seg), boardForm, simForm, h('hr'),
    h('div.field', h('label', 'Send a board command'), h('div.row', cmd, button('Send', sendCmd))));
  document.body.append(pop);

  store.on('ports', (m) => {
    const cur = m.sensor?.port || port.value;
    const ports = m.ports || [];
    const guess = cur || ports.find((p) => /stm|stlink|usbmodem/i.test(`${p.device} ${p.description}`))?.device || '';
    fill(port, h('option', { value: '' }, ports.length ? 'Choose a port' : 'No serial ports found'),
      ports.map((p) => h('option', { value: p.device, selected: p.device === guess },
        p.description && p.description !== 'n/a' ? `${p.device} · ${p.description}` : p.device)));
    if (m.gt && store.source === 'serial' && GT_KINDS.some(([v]) => v === m.gt.kind)) {
      gtKind.value = m.gt.kind;
      gtValue.value = m.gt.value || '';
      syncGt();
    }
  });
  store.on('hello', (m) => {
    tab = m.source === 'sim' ? 'sim' : 'board';
    gtPressure.checked = !!m.gt_pressure;
    fill(profile, (m.profiles?.length ? m.profiles : Object.keys(PROFILE_LABELS)).map((p) => h('option', { value: p, selected: p === m.sim?.profile }, PROFILE_LABELS[p] || p)));
    renderTab();
  });
  renderTab();

  return {
    chip,
    update() {
      const c = chipState(), t = store.tele;
      const up = store.conn === 'open';
      setTone(dotBoard, up ? c.tone : 'idle');
      setTone(dotPitot, up && store.hasPitot ? 'ok' : 'idle');
      setTone(dotEnv, up && store.env && store.env.age_s < 5 ? 'ok' : 'idle');
      setText(chipText, c.text);
      setText(st.server, store.conn === 'open' ? `Connected · ${store.mode || ''}` : store.conn === 'connecting' ? 'Connecting…' : 'Not running: python -m calib serve');
      setText(st.board, store.conn !== 'open' ? '–' : `${t.sensor_status || '–'}${t.sensor_hz ? ` · ${fixed(t.sensor_hz, 0)} Hz` : ''}`);
      setText(st.pitot, store.conn !== 'open' ? '–' : `${t.gt_status || '–'}${t.gt_hz ? ` · ${fixed(t.gt_hz, 0)} Hz` : ''}`);
      setText(st.drops, store.conn !== 'open' ? '–' : `${t.drops ?? 0}${t.bad_lines ? ` · ${t.bad_lines} bad lines` : ''}`);
    },
  };
}
