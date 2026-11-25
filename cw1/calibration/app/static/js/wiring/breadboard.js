// A half-size breadboard (30 columns) as a netlist, plus the circuit check every wiring diagram runs.
// Pure: no DOM, so scripts/check-wiring.mjs runs the same check in Node as the dashboard does in the browser.
//
// Hole addresses: 'a1'..'j30' for the terminal strips, 'top+5', 'top-5', 'bot+5', 'bot-5' for the rails.
// Nets: a–e of one column are joined, f–j of one column are joined, each rail is one net along its length.
// Header pins are 'CN7-18'; every header pin with the same signal is the same net (all the GNDs).
import { NUCLEO, headerPin, pinLabel } from './nucleo.js';

export const COLS = 30;
export const ROWS = 'abcdefghij';

/** 'e12' | 'top+3' -> { kind: 'hole', row, col } | { kind: 'rail', rail: 'top+', col } | null. */
export function parseHole(addr) {
  let m = /^([a-j])(\d{1,2})$/.exec(addr || '');
  if (m && +m[2] >= 1 && +m[2] <= COLS) return { kind: 'hole', row: m[1], col: +m[2] };
  m = /^(top|bot)([+-])(\d{1,2})$/.exec(addr || '');
  if (m && +m[3] >= 1 && +m[3] <= COLS) return { kind: 'rail', rail: m[1] + m[2], col: +m[3] };
  return null;
}

/** The breadboard net a hole belongs to, or null for a bad address. */
export function holeNet(addr) {
  const h = parseHole(addr);
  if (!h) return null;
  if (h.kind === 'rail') return `rail:${h.rail}`;
  return `${ROWS.indexOf(h.row) < 5 ? 'top' : 'bot'}:${h.col}`;
}

/** Plain words for an endpoint: 'CN7 pin 18 · +5V', 'breadboard e12', 'top + rail'. */
export function endLabel(ref, board = NUCLEO) {
  if (headerPin(ref, board)) return pinLabel(ref, board);
  const h = parseHole(ref);
  if (!h) return ref;
  if (h.kind === 'rail') return `${h.rail.startsWith('top') ? 'top' : 'bottom'} ${h.rail.endsWith('+') ? '+ (red)' : '− (blue)'} rail, column ${h.col}`;
  return `breadboard ${h.row}${h.col}`;
}

class Nets {
  constructor() { this.parent = new Map(); }
  find(x) {
    if (!this.parent.has(x)) this.parent.set(x, x);
    let r = x;
    while (this.parent.get(r) !== r) r = this.parent.get(r);
    this.parent.set(x, r);
    return r;
  }
  join(a, b) { this.parent.set(this.find(a), this.find(b)); }
}

const nodeOf = (ref, board) => {
  const hp = headerPin(ref, board);
  if (hp) return `sig:${hp.pin.signal}`;
  return holeNet(ref);
};

/**
 * Check a wiring spec (see js/wiring/parts/*.js) as a circuit: which board signal each part pin really reaches
 * through the breadboard strips, rails and wires, against the signal it should reach.
 * Returns { ok, errors: [text], warnings: [text], pins: [{ part, pin, want, got: [signal], ok }] }.
 */
export function checkWiring(spec, { includeOptional = true } = {}) {
  const board = NUCLEO;
  const errors = [], warnings = [];
  const nets = new Nets();
  const used = new Map();  // hole -> 'part.pin', to catch two legs in one hole

  const parts = spec.parts.filter((p) => includeOptional || !p.optional);
  for (const part of parts) {
    for (const pin of part.pins) {
      const where = `${part.label} ${pin.name}`;
      if (!parseHole(pin.at)) { errors.push(`${where}: '${pin.at}' is not a breadboard hole`); continue; }
      if (used.has(pin.at)) errors.push(`${where} and ${used.get(pin.at)} are both in hole ${pin.at}`);
      used.set(pin.at, where);
      nets.find(holeNet(pin.at));
    }
  }
  for (const [i, w] of spec.wires.entries()) {
    const a = nodeOf(w.from, board), b = nodeOf(w.to, board);
    if (!a) errors.push(`wire ${i + 1}: '${w.from}' is not a header pin or hole`);
    if (!b) errors.push(`wire ${i + 1}: '${w.to}' is not a header pin or hole`);
    for (const end of [w.from, w.to]) {
      const hp = headerPin(end, board);
      if (hp && hp.pin.signal === 'NC') errors.push(`wire ${i + 1}: ${end} is not connected on this board`);
      if (!hp && parseHole(end) && used.has(end)) errors.push(`wire ${i + 1}: hole ${end} already holds ${used.get(end)}`);
    }
    if (a && b) nets.join(a, b);
  }

  // Signals present on every net
  const signals = new Map();
  for (const key of nets.parent.keys()) {
    if (!key.startsWith('sig:')) continue;
    const root = nets.find(key);
    if (!signals.has(root)) signals.set(root, new Set());
    signals.get(root).add(key.slice(4));
  }
  for (const set of signals.values()) {
    const sup = [...set].filter((s) => s !== 'NC');
    if (sup.length > 1) errors.push(`short circuit: ${sup.join(' and ')} are wired together`);
  }

  const pins = [];
  for (const part of parts) {
    for (const pin of part.pins) {
      const net = holeNet(pin.at);
      if (!net) continue;
      const got = [...(signals.get(nets.find(net)) || [])];
      const want = pin.net ?? null;
      const ok = want === null ? got.length === 0 : got.length === 1 && got[0] === want;
      pins.push({ part: part.label, pin: pin.name, want, got, ok, optional: !!part.optional });
      const where = `${part.label} ${pin.name}`;
      if (want === null && got.length) errors.push(`${where} should be left open but reaches ${got.join(', ')}`);
      else if (want !== null && !got.length) errors.push(`${where} is not connected (should reach ${want})`);
      else if (want !== null && !ok) errors.push(`${where} reaches ${got.join(', ')}, not ${want}`);
      if (pin.supply && got.length === 1) {
        const v = Object.values(board.headers).flatMap((hd) => hd.pins).find((p) => p.signal === got[0])?.volts;
        const [lo, hi] = pin.supply;
        if (v !== undefined && (v < lo || v > hi)) errors.push(`${where} needs ${lo}–${hi} V but ${got[0]} is ${v} V`);
      }
      if (pin.maxVolts !== undefined && got.length === 1) {
        const v = Object.values(board.headers).flatMap((hd) => hd.pins).find((p) => p.signal === got[0])?.volts;
        if (v !== undefined && v > pin.maxVolts) errors.push(`${where} must stay below ${pin.maxVolts} V but reaches ${got[0]} (${v} V)`);
      }
    }
  }
  for (const w of spec.wires) {
    if (!w.color) warnings.push(`wire ${w.from} → ${w.to} has no colour`);
  }
  return { ok: errors.length === 0, errors, warnings, pins };
}
