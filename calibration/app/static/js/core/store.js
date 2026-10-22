// Live state mirrored from `python -m calib serve` (calibration/docs/SPEC.md §6) and the actions pages
// call. The server is the single source of truth: it runs the PC preview, the fits, the diagnosis and the
// recordings. This module only buffers the stream and forwards requests.

export const FS = 200;
export const GRAVITY = 9.80665;
const BUFFER_S = 150;
const ENV_HISTORY_S = 15 * 60;
const STREAM_KEYS = ['t', 'gt', 'est', 'dev', 'feature', 'rho', 'temp_c', 'pressure_pa', 'lab_raw', 'lab_weight'];

/** Column store of the 200 Hz stream with a fixed capacity (oldest quarter dropped when full). */
class Buffer {
  constructor(keys, cap) {
    this.keys = keys;
    this.cap = cap;
    this.n = 0;
    this.cols = Object.fromEntries(keys.map((k) => [k, new Float64Array(cap)]));
  }
  clear() { this.n = 0; }
  /** Drop every sample at or after time t (a replacement frame re-sends them). */
  truncateFrom(t) {
    const a = this.cols.t;
    let lo = 0, hi = this.n;
    while (lo < hi) {
      const m = (lo + hi) >> 1;
      if (a[m] < t) lo = m + 1; else hi = m;
    }
    this.n = lo;
  }
  push(row) {
    if (this.n === this.cap) {
      const drop = this.cap >> 2;
      for (const k of this.keys) this.cols[k].copyWithin(0, drop, this.n);
      this.n -= drop;
    }
    for (const k of this.keys) {
      const v = row[k];
      this.cols[k][this.n] = v == null ? NaN : v;
    }
    this.n++;
  }
  col(k) { return this.cols[k].subarray(0, this.n); }
  get now() { return this.n ? this.cols.t[this.n - 1] : NaN; }
  /** Index of the first sample in the last `sec` seconds. */
  since(sec) {
    const t = this.cols.t, end = this.now - sec;
    let lo = 0, hi = this.n;
    while (lo < hi) {
      const m = (lo + hi) >> 1;
      if (t[m] < end) lo = m + 1; else hi = m;
    }
    return lo;
  }
  /** Most recent finite value of a column. */
  last(k) {
    const a = this.cols[k];
    for (let i = this.n - 1; i >= Math.max(0, this.n - 400); i--) if (Number.isFinite(a[i])) return a[i];
    return NaN;
  }
  /** Mean and standard deviation of a column over the last `sec` seconds. */
  stats(k, sec, endAgo = 0) {
    const a = this.cols[k], t = this.cols.t, i0 = this.since(sec + endAgo), tEnd = this.now - endAgo;
    let n = 0, s = 0, s2 = 0;
    for (let i = i0; i < this.n && t[i] <= tEnd; i++) {
      const v = a[i];
      if (!Number.isFinite(v)) continue;
      n++; s += v; s2 += v * v;
    }
    if (!n) return { n: 0, mean: NaN, sd: NaN };
    const mean = s / n;
    return { n, mean, sd: Math.sqrt(Math.max(0, s2 / n - mean * mean)) };
  }
}

class Store extends EventTarget {
  constructor() {
    super();
    this.conn = 'connecting';        // connecting | open | closed
    this.mode = null;                // live | sim | replay (server mode)
    this.source = null;              // serial | sim | replay
    this.boardMethod = null;
    this.specs = {};                 // param name -> {min, max, step, unit, desc, group}
    this.firmwareParams = [];
    this.params = {};                // PC values (what the preview uses, what a push sends)
    this.board = {};                 // what the board reported on its last GET
    this.tele = {};
    this.metrics = null;
    this.envHistory = [];
    this.ports = null;
    this.profiles = [];
    this.sim = null;
    this.runs = [];
    this.session = null;
    this.lastFit = null;
    this.log = [];
    this.buf = new Buffer(STREAM_KEYS, BUFFER_S * FS);
    this.version = 0;                // bumps on every frame: pages redraw only when it changed
    this._envAge = Infinity;
  }
  on(type, fn) {
    const h = (e) => fn(e.detail);
    this.addEventListener(type, h);
    return () => this.removeEventListener(type, h);
  }
  emit(type, detail) { this.dispatchEvent(new CustomEvent(type, { detail })); }

  // ---- derived values used by several pages
  get env() { return this.tele.env || null; }
  get isSim() { return this.source === 'sim'; }
  /** Readings are arriving: the server's board_state when it sends one (a 2 Hz board counts), else the rate. */
  get streaming() {
    if (this.conn !== 'open') return false;
    const b = this.tele.board_state;
    if (b && b !== 'sim' && b !== 'replay') return b === 'streaming';
    return this.tele.sensor_hz > 1;
  }
  get hasPitot() { return this.tele.gt_hz > 1; }
  /** sim | replay | none | absent | closed | opening | silent | no_data | streaming (server's board_state). */
  get boardState() {
    if (this.conn !== 'open') return 'offline';
    if (this.tele.board_state) return this.tele.board_state;
    // A server from before board_state: still never present the simulator or a replay as the board.
    if (this.source === 'sim' || this.source === 'replay') return this.source;
    return this.tele.sensor_connected && this.tele.sensor_hz > 1 ? 'streaming' : 'silent';
  }
  boardValue(name) { return this.board[name]; }
  /** A firmware setting whose PC value differs from what the board runs. */
  isDirty(name) {
    if (name === 'lc_offset' || !this.firmwareParams.includes(name)) return false;
    const a = this.params[name], b = this.board[name];
    if (!Number.isFinite(a) || !Number.isFinite(b)) return false;
    return Math.abs(a - b) > 1e-6 * Math.max(1, Math.abs(a), Math.abs(b));
  }
  dirtyParams() {
    const smoother = this.params.filter_kind >= 0.5 ? 'lpf_hz' : 'ema_alpha';  // the inactive one is not pushed
    return this.firmwareParams.filter((n) => n !== smoother && this.isDirty(n));
  }
}

export const store = new Store();

// ------------------------------------------------------------------------------------ websocket link
let ws = null;
const pendingFits = [];

function isLocal() {
  return /^https?:$/.test(location.protocol) && /^(localhost|127\.0\.0\.1|\[::1\]|0\.0\.0\.0)$/.test(location.hostname);
}

export function connectServer() {
  if (!isLocal()) {
    store.conn = 'closed';
    store.emit('change');
    return;
  }
  store.conn = 'connecting';
  try {
    ws = new WebSocket(`ws://${location.host}/ws`);
  } catch {
    setTimeout(connectServer, 2000);
    return;
  }
  ws.onopen = () => { store.conn = 'open'; store.emit('change'); };
  ws.onclose = () => {
    ws = null;
    const was = store.conn;
    store.conn = 'closed';
    store.tele = {};
    if (was === 'open') addLog('error', 'Lost the calibration server. Retrying…');
    store.emit('change');
    setTimeout(connectServer, 2000);
  };
  ws.onmessage = (ev) => {
    let m;
    try { m = JSON.parse(ev.data); } catch { return; }
    onMessage(m);
  };
}

function send(msg) {
  if (ws && ws.readyState === 1) {
    ws.send(JSON.stringify(msg));
    return true;
  }
  addLog('error', 'Not connected to the calibration server');
  return false;
}

function addLog(level, msg) {
  const entry = { t: new Date(), level, msg: String(msg) };
  store.log.push(entry);
  if (store.log.length > 400) store.log.splice(0, store.log.length - 400);
  store.emit('log', entry);
}

function onMessage(m) {
  switch (m.type) {
    case 'hello': {
      // Live reload: a different server id means the server restarted, possibly with new code.
      if (store.serverId && m.server_id && m.server_id !== store.serverId && m.live_reload) {
        store.emit('reload', { css_only: false, files: ['server restarted'] });
        return;
      }
      store.serverId = m.server_id;
      store.mode = m.mode;
      store.source = m.source;
      store.boardMethod = m.method;     // drag_plate | cup_hall | cup_encoder (from the board's GET)
      const model = m.models?.[m.model];
      store.specs = Object.fromEntries((model?.params || []).map((p) => [p.name, p]));
      store.firmwareParams = m.firmware_params || [];
      store.params = { ...m.params };
      store.board = { ...(m.board || {}) };
      store.tele = m.telemetry || {};
      store.profiles = m.profiles || [];
      store.sim = m.sim;
      store.metrics = null;
      store.envHistory = [];
      store._envAge = Infinity;
      store.buf.clear();
      store.version++;
      send({ type: 'ports' });
      send({ type: 'list_runs' });
      store.emit('hello', m);
      break;
    }
    case 'frame': {
      const b = store.buf, raw = m.raw || {}, n = m.t.length;
      // a replacement re-sends the last 60 s with the new settings; older history is kept
      if (m.replace) n ? b.truncateFrom(m.t[0]) : b.clear();
      for (let i = 0; i < n; i++) {
        b.push({
          t: m.t[i], gt: m.gt?.[i], est: m.est?.[i], dev: m.dev?.[i], feature: raw.feature?.[i],
          rho: raw.rho?.[i], temp_c: raw.temp_c?.[i], pressure_pa: raw.pressure_pa?.[i],
          lab_raw: raw.lab_raw?.[i], lab_weight: raw.lab_weight?.[i],
        });
      }
      if (m.telemetry) {
        store.tele = m.telemetry;
        trackEnv(m.telemetry.env);
      }
      store.version++;
      break;
    }
    case 'reload':  // live reload (pnpm dev): a dashboard file changed on disk
      store.emit('reload', m);
      return;
    case 'telemetry':  // sent instead of frames while no data flows, so "no board" stays current
      store.tele = m.telemetry || {};
      trackEnv(store.tele.env);
      break;
    case 'params':
      store.params = { ...m.params };
      if (m.board && Object.keys(m.board).length) store.board = { ...m.board };
      store.emit('params');
      break;
    case 'metrics':
      store.metrics = m;
      break;
    case 'fit_result': {
      store.lastFit = m;
      const p = pendingFits.shift();
      if (p) p(m);
      store.emit('fit', m);
      break;
    }
    case 'ports':
      store.ports = m;
      store.emit('ports', m);
      break;
    case 'runs':
      store.runs = m.runs || [];
      store.emit('runs', store.runs);
      break;
    case 'run_summary':
      store.emit('run_summary', m);
      break;
    case 'session':
      store.session = m.recording === false ? null : m;
      if (m.recording === false) send({ type: 'list_runs' });
      store.emit('session', m);
      break;
    case 'exported':
      store.emit('exported', m);
      break;
    case 'log':
      addLog(m.level === 'error' || String(m.msg).startsWith('#err') ? 'error' : m.level || 'info', m.msg);
      break;
    default:
      break;
  }
  store.emit('change');
}

function trackEnv(env) {
  if (!env) return;
  const fresh = env.age_s < store._envAge;   // a new `#env` reply resets its age
  store._envAge = env.age_s;
  if (!fresh) return;
  const t = Date.now() / 1000;
  store.envHistory.push({ ...env, t });
  while (store.envHistory.length && store.envHistory[0].t < t - ENV_HISTORY_S) store.envHistory.shift();
}

// ------------------------------------------------------------------------------------------- actions
export const actions = {
  log: addLog,
  command(line) {
    const sent = send({ type: 'command', line });
    if (sent) addLog('info', `> ${line}`);
    return sent;
  },
  setParam(name, value) {
    store.params[name] = value;  // optimistic, the server echoes the clamped value
    send({ type: 'set_param', name, value });
  },
  setParams(values) {
    Object.assign(store.params, values);
    send({ type: 'set_params', params: values });
  },
  /** Set one firmware value on the PC and on the board, then read the board back. */
  setOnBoard(name, value) {
    actions.setParam(name, value);
    actions.command(`SET ${name} ${Number(value.toPrecision(9))}`);
    actions.command('GET');
  },
  push() { send({ type: 'push_params' }); },
  autoPush(on) { send({ type: 'auto_push', on }); },
  /** Resolves with the server's fit_result (SPEC §6). */
  fit(method, free, windowS = 60) {
    return new Promise((resolve) => {
      if (!send({ type: 'fit', method, free, window_s: windowS })) return resolve({ ok: false, message: 'not connected' });
      pendingFits.push(resolve);
    });
  },
  sim(msg) { store.sim = { ...(store.sim || {}), ...msg }; send({ type: 'sim', ...msg }); },
  connect(cfg) { send({ type: 'connect', ...cfg }); },
  ports() { send({ type: 'ports' }); },
  record(on, label) { send({ type: 'record', on, label }); },
  note(text) { send({ type: 'note', text }); },
  exportCalibration(install) { send({ type: 'export', install }); },
  listRuns() { send({ type: 'list_runs' }); },
  runSummary(name) { send({ type: 'run_summary', name }); },
  replay(name) { send({ type: 'connect', source: 'replay', replay_file: name }); },
  /** Average a stream column over the next `sec` seconds of new samples. */
  measure(key, sec = 3) {
    const b = store.buf, t0 = b.now, started = Date.now();
    return new Promise((resolve, reject) => {
      const iv = setInterval(() => {
        if (b.now >= t0 + sec) {
          clearInterval(iv);
          const s = b.stats(key, sec), gt = b.stats('gt', sec);
          if (!s.n) reject(new Error('No readings arrived. Is the board connected and in MODE D?'));
          else resolve({ ...s, gt: gt.mean });
        } else if (Date.now() - started > (sec + 6) * 1000) {
          clearInterval(iv);
          reject(new Error('No data arriving from the board.'));
        }
      }, 100);
    });
  },
};
