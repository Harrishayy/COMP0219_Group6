// Wiring tab: a top-view drawing of the Nucleo (morpho headers, shield) beside a half-size breadboard with the
// part, its passives and every jumper, plus a numbered build list that plays one step at a time. Below it the
// circuit check (js/wiring/breadboard.js) says which signal each leg really reaches. Colours are tokens.
import { h, fill, setText, setTone } from '../core/dom.js';
import { table } from '../core/ui.js';
import { NUCLEO, headerPin } from './nucleo.js';
import { COLS, ROWS, parseHole, checkWiring, endLabel } from './breadboard.js';

// ------------------------------------------------------------------------------------------ geometry
const W = 980, H = 470;
const BOARD = { x: 70, y: 30, w: 230, h: 420 };
const HEADER_X = { CN7: [96, 112], CN10: [258, 274] };  // odd, even column
const PIN_Y0 = 80, PIN_DY = 19;
const BB = { x: 364, y: 44, w: 592, h: 348 };
const COL_X0 = 384, COL_DX = 19;
const ROW_Y = { a: 116, b: 135, c: 154, d: 173, e: 192, f: 236, g: 255, h: 274, i: 293, j: 312 };
const RAIL_Y = { 'top+': 64, 'top-': 82, 'bot-': 346, 'bot+': 364 };

const colX = (c) => COL_X0 + (c - 1) * COL_DX;
const WIRE = (c) => `var(--wire-${c || 'white'})`;

function holeXY(addr) {
  const hp = headerPin(addr);
  if (hp) {
    const [odd, even] = HEADER_X[hp.header.id];
    return { x: hp.pin.n % 2 ? odd : even, y: PIN_Y0 + Math.floor((hp.pin.n - 1) / 2) * PIN_DY, header: hp };
  }
  const p = parseHole(addr);
  if (!p) return null;
  return { x: colX(p.col), y: p.kind === 'rail' ? RAIL_Y[p.rail] : ROW_Y[p.row] };
}

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// --------------------------------------------------------------------------------------- the drawing
function drawBoard(usedPins) {
  let s = `<rect x="${BOARD.x}" y="${BOARD.y}" width="${BOARD.w}" height="${BOARD.h}" rx="8" fill="var(--pcb)" stroke="var(--line-strong)" stroke-width="1.5"/>`;
  s += `<rect x="160" y="${BOARD.y + 4}" width="50" height="20" rx="2" fill="var(--sunken)" stroke="var(--line-strong)"/>`;
  s += `<text x="185" y="${BOARD.y + 38}" class="w-cap" text-anchor="middle">ST-LINK USB</text>`;
  s += `<rect x="128" y="74" width="114" height="356" rx="4" fill="var(--pcb-shield)" stroke="var(--line)" />`;
  s += `<text x="185" y="252" class="w-cap" text-anchor="middle" transform="rotate(-90 185 252)">${esc(NUCLEO.shield)}</text>`;
  s += `<text x="${BOARD.x + BOARD.w / 2}" y="${BOARD.y + BOARD.h - 8}" class="w-cap" text-anchor="middle">${esc(NUCLEO.title)} · top view</text>`;
  for (const hd of Object.values(NUCLEO.headers)) {
    const [odd] = HEADER_X[hd.id];
    s += `<text x="${odd + 8}" y="${PIN_Y0 - 14}" class="w-cap" text-anchor="middle">${hd.id}</text>`;
    for (const p of hd.pins) {
      const { x, y } = holeXY(`${hd.id}-${p.n}`);
      const on = usedPins.has(`${hd.id}-${p.n}`);
      s += `<rect x="${x - 4}" y="${y - 4}" width="8" height="8" rx="1" fill="${on ? 'var(--ink)' : 'var(--pin)'}"><title>${esc(`${hd.id} pin ${p.n}: ${p.name}${p.alt ? ' · ' + p.alt : ''}`)}</title></rect>`;
    }
  }
  return s;
}

function drawBreadboard(usedHoles) {
  let s = `<rect x="${BB.x}" y="${BB.y}" width="${BB.w}" height="${BB.h}" rx="6" fill="var(--bb-body)" stroke="var(--line)"/>`;
  s += `<rect x="${BB.x}" y="${ROW_Y.e + 12}" width="${BB.w}" height="${ROW_Y.f - ROW_Y.e - 24}" fill="var(--bb-channel)"/>`;
  for (const [rail, y] of Object.entries(RAIL_Y)) {
    const pos = rail.endsWith('+');
    const ly = y + (rail.startsWith('top') ? (pos ? -10 : 10) : (pos ? 10 : -10));
    s += `<line x1="${BB.x + 10}" x2="${BB.x + BB.w - 10}" y1="${ly}" y2="${ly}" stroke="var(${pos ? '--bb-rail-pos' : '--bb-rail-neg'})" stroke-width="1.5"/>`;
    s += `<text x="${BB.x + 8}" y="${y + 3}" class="w-cap" text-anchor="start" fill="var(${pos ? '--bb-rail-pos' : '--bb-rail-neg'})">${pos ? '+' : '−'}</text>`;
  }
  for (let c = 1; c <= COLS; c++) {
    const x = colX(c);
    if (c === 1 || c % 5 === 0) {
      s += `<text x="${x}" y="${ROW_Y.a - 10}" class="w-num" text-anchor="middle">${c}</text>`;
      s += `<text x="${x}" y="${ROW_Y.j + 18}" class="w-num" text-anchor="middle">${c}</text>`;
    }
    for (const addr of [...ROWS].map((r) => r + c).concat(Object.keys(RAIL_Y).map((r) => r + c))) {
      const { y } = holeXY(addr);
      const on = usedHoles.has(addr);
      s += `<rect x="${x - 3}" y="${y - 3}" width="6" height="6" rx="1" fill="${on ? 'var(--ink)' : 'var(--bb-hole)'}"/>`;
    }
  }
  for (const r of ROWS) {
    s += `<text x="${COL_X0 - 14}" y="${ROW_Y[r] + 3}" class="w-num" text-anchor="middle">${r}</text>`;
  }
  return s;
}

function drawPart(part, step) {
  const xy = part.pins.map((p) => holeXY(p.at));
  const xs = xy.map((p) => p.x), ys = xy.map((p) => p.y);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  let g = '';
  if (part.kind === 'to92') {
    // Top view: the flat face is the straight edge, towards the centre channel; the round back towards row a.
    const cx = (x0 + x1) / 2, w = x1 - x0 + 20, y = y0;
    g += `<path d="M ${cx - w / 2} ${y + 5} H ${cx + w / 2} A ${w / 2} 13 0 0 0 ${cx - w / 2} ${y + 5} Z" fill="var(--part-body)" stroke="var(--line-strong)"/>`;
    part.pins.forEach((p, i) => { g += `<circle cx="${xs[i]}" cy="${ys[i]}" r="2.2" fill="var(--lead)"/>`; });
    g += `<text x="${cx + w / 2 + 5}" y="${y + 3}" class="w-pin" text-anchor="start">${esc(part.label)} · flat face ↓</text>`;
    part.pins.forEach((p, i) => { g += `<text x="${xs[i]}" y="${y + 20 + (i % 2) * 9}" class="w-pin" text-anchor="middle">${esc(p.name)}</text>`; });
  } else if (part.kind === 'breakout') {
    const pad = 12;
    g += `<rect x="${x0 - pad}" y="${y0 - 10}" width="${x1 - x0 + 2 * pad}" height="${H - 14 - (y0 - 10)}" rx="4" fill="var(--pcb-part)" stroke="var(--line-strong)" opacity="0.94"/>`;
    part.pins.forEach((p, i) => {
      g += `<circle cx="${xs[i]}" cy="${ys[i]}" r="4" fill="var(--pin-gold)" stroke="var(--line-strong)"/>`;
      g += `<text x="${xs[i] + 3}" y="${ys[i] + 12}" class="w-pin on-dark" text-anchor="end" transform="rotate(-90 ${xs[i] + 3} ${ys[i] + 12})">${esc(p.name)}</text>`;
    });
    g += `<text x="${(x0 + x1) / 2}" y="${H - 34}" class="w-part on-dark" text-anchor="middle">${esc(part.label)}</text>`;
  } else {
    // two-legged passives: resistor (beige body, bands) or capacitor (disc)
    const [a, b] = xy, mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
    const vertical = Math.abs(a.y - b.y) > Math.abs(a.x - b.x);
    g += `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="var(--lead)" stroke-width="2"/>`;
    if (part.kind === 'resistor') {
      const bw = vertical ? 9 : 22, bh = vertical ? 22 : 9;
      g += `<rect x="${mx - bw / 2}" y="${my - bh / 2}" width="${bw}" height="${bh}" rx="3" fill="var(--part-resistor)" stroke="var(--line-strong)"/>`;
    } else {
      g += `<ellipse cx="${mx}" cy="${my}" rx="7" ry="6" fill="var(--part-cap)" stroke="var(--line-strong)"/>`;
    }
    if (vertical) {  // label out to the right, clear of the jumpers that crowd a part's column
      const lx = mx + 2.6 * COL_DX;
      g += `<line x1="${mx + 5}" x2="${lx - 3}" y1="${my}" y2="${my}" stroke="var(--ink-3)" stroke-width="0.8" stroke-dasharray="2 2"/>`;
      g += `<text x="${lx}" y="${my + 3}" class="w-pin halo" text-anchor="start">${esc(part.label)}</text>`;
    } else {
      g += `<text x="${mx}" y="${my - 9}" class="w-pin halo" text-anchor="middle">${esc(part.label)}</text>`;
    }
  }
  return `<g data-step="${step}" class="w-item${part.optional ? ' optional' : ''}">${g}</g>`;
}

function wirePath(a, b) {
  if (!a.header && !b.header) {  // short breadboard jumper: a low arch
    const lift = Math.max(16, Math.abs(a.x - b.x) * 0.25);
    return `M ${a.x} ${a.y} C ${a.x} ${Math.min(a.y, b.y) - lift}, ${b.x} ${Math.min(a.y, b.y) - lift}, ${b.x} ${b.y}`;
  }
  const [p, q] = a.header ? [a, b] : [b, a];
  const left = p.header.header.side === 'left';
  const c1 = { x: p.x + (left ? -50 : 60), y: p.y - 24 };
  const c2 = { x: q.x - 90, y: q.y + (q.y > ROW_Y.e + 20 ? -70 : -60) };
  return `M ${p.x} ${p.y} C ${c1.x} ${c1.y}, ${c2.x} ${c2.y}, ${q.x} ${q.y}`;
}

function drawWire(w, step) {
  const a = holeXY(w.from), b = holeXY(w.to);
  if (!a || !b) return '';
  const d = wirePath(a, b);
  return `<g data-step="${step}" class="w-item w-wire">
    <path d="${d}" fill="none" stroke="var(--wire-edge)" stroke-width="6.5" stroke-linecap="round" pathLength="1" class="w-edge"/>
    <path d="${d}" fill="none" stroke="${WIRE(w.color)}" stroke-width="4" stroke-linecap="round" pathLength="1" class="w-core"/>
    <circle cx="${a.x}" cy="${a.y}" r="4.5" fill="${WIRE(w.color)}" stroke="var(--wire-edge)" stroke-width="1.5"/>
    <circle cx="${b.x}" cy="${b.y}" r="4.5" fill="${WIRE(w.color)}" stroke="var(--wire-edge)" stroke-width="1.5"/></g>`;
}

function pinLabels(spec) {
  let s = '';
  for (const w of spec.wires) {
    for (const end of [w.from, w.to]) {
      const p = holeXY(end);
      if (!p?.header) continue;
      // Labels sit left of each header's odd column (outside the board for CN7, over the shield for CN10),
      // clear of the wires, which leave towards the breadboard on the right.
      const odd = p.header.pin.n % 2 === 1;
      const x0 = HEADER_X[p.header.header.id][0] - 7;
      s += `<text x="${x0 - 3}" y="${p.y + 3}" class="w-label" text-anchor="end">${esc(`${p.header.pin.name} · ${p.header.pin.n}`)}</text>`;
      if (!odd) s += `<line x1="${x0}" x2="${p.x - 4}" y1="${p.y}" y2="${p.y}" stroke="var(--ink-3)" stroke-width="0.8" stroke-dasharray="2 2"/>`;
    }
  }
  return s;
}

export function wiringSvg(spec) {
  const usedPins = new Set(), usedHoles = new Set();
  for (const w of spec.wires) for (const e of [w.from, w.to]) (headerPin(e) ? usedPins : usedHoles).add(e);
  for (const p of spec.parts) for (const pin of p.pins) usedHoles.add(pin.at);
  let step = 0;
  const parts = spec.parts.map((p) => drawPart(p, step++)).join('');
  const wires = spec.wires.map((w) => drawWire(w, step++)).join('');
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(spec.title)}">
    ${drawBoard(usedPins)}${drawBreadboard(usedHoles)}${parts}${pinLabels(spec)}${wires}</svg>`;
}

// ------------------------------------------------------------------------------------------- the tab
/** Build steps: place each part, then each wire, in the spec's order (the drawing's data-step indices). */
export function buildSteps(spec) {
  return [
    ...spec.parts.map((p) => ({ kind: 'part', title: `Place the ${p.label}${p.optional ? ' (optional)' : ''}`, text: p.place || '', color: null })),
    ...spec.wires.map((w) => ({ kind: 'wire', title: `${endLabel(w.from)} → ${endLabel(w.to)}`, text: w.say || '', color: w.color })),
  ];
}

/**
 * The Wiring tab for one sensor. `live(store)` (optional, from the sensor module) says whether the board
 * actually sees the part: { tone, text }.
 */
export function wiringPanel(spec, { live } = {}) {
  const steps = buildSteps(spec);
  const art = h('div.wiring-art');
  art.innerHTML = wiringSvg(spec);
  const svg = art.firstElementChild;
  const items = [...svg.querySelectorAll('[data-step]')];
  let cur = -1, timer = null;  // -1: everything shown

  const counter = h('span.count');
  const list = h('ol.wiring-steps', steps.map((s, i) => h('li', {
    dataset: { step: i },
    onclick: () => { stop(); show(i); },
    onmouseenter: () => { if (cur < 0) peek(i); },
    onmouseleave: () => { if (cur < 0) peek(-1); },
  },
  h('span.n', String(i + 1)),
  h('span.sw', { class: s.color ? 'wire' : 'part', style: s.color ? { background: WIRE(s.color) } : null, 'aria-hidden': 'true' }),
  h('div', h('strong', s.title), s.text && h('p', s.text)))));

  function peek(i) {
    for (const el of items) el.classList.toggle('dim', i >= 0 && Number(el.dataset.step) !== i);
  }
  function show(i) {
    cur = i;
    for (const el of items) {
      const k = Number(el.dataset.step);
      el.classList.toggle('future', i >= 0 && k > i);
      el.classList.toggle('cur', k === i);
      el.classList.remove('dim');
      if (k === i && el.classList.contains('w-wire')) {  // restart the draw-in animation
        el.classList.remove('draw');
        void el.getBoundingClientRect();
        el.classList.add('draw');
      }
    }
    [...list.children].forEach((li, k) => {
      li.classList.toggle('cur', k === i);
      li.classList.toggle('done', i >= 0 && k < i);
    });
    setText(counter, i < 0 ? `${steps.length} steps` : `Step ${i + 1} of ${steps.length}`);
    playBtn.textContent = timer ? 'Pause' : i >= 0 && i < steps.length - 1 ? 'Play from here' : 'Play';
    if (i >= 0) list.children[i]?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
  function stop() { clearInterval(timer); timer = null; }
  const playBtn = h('button.btn.primary.sm', {
    type: 'button',
    onclick: () => {
      if (timer) { stop(); return show(cur); }
      show(cur < 0 || cur >= steps.length - 1 ? 0 : cur);
      timer = setInterval(() => {
        if (cur >= steps.length - 1) { stop(); return show(cur); }
        show(cur + 1);
      }, 1800);
      show(cur);
    },
  }, 'Play');
  const controls = h('div.row',
    playBtn,
    h('button.btn.sm', { type: 'button', onclick: () => { stop(); show(Math.max(0, cur - 1)); } }, 'Back'),
    h('button.btn.sm', { type: 'button', onclick: () => { stop(); show(Math.min(steps.length - 1, cur + 1)); } }, 'Next'),
    h('button.btn.quiet.sm', { type: 'button', onclick: () => { stop(); show(-1); } }, 'Show all'),
    h('span.spacer'), counter);

  // ---- circuit check
  const res = checkWiring(spec);
  const checkTable = table(['Leg', 'Should reach', 'Reaches', 'Check'], res.pins.map((p) => [
    h('span', h('strong', p.part), ` ${p.pin}`, p.optional ? h('span.faint', ' · optional') : ''),
    p.want ?? 'nothing (leave open)',
    p.got.length ? p.got.join(', ') : 'nothing',
    { v: p.ok ? 'OK' : 'Wrong', cls: p.ok ? 'ok' : 'bad' },
  ]), { rightFrom: 3 });
  const verdict = h(`div.result${res.ok ? '.ok' : '.warn'}`,
    h('strong', res.ok ? `Circuit check: all ${res.pins.length} legs reach the right signal.` : `Circuit check: ${res.errors.length} problem${res.errors.length > 1 ? 's' : ''} in this diagram.`),
    res.errors.length ? h('ul', res.errors.map((e) => h('li', e))) : null);

  const liveRow = h('div.result');
  const livePill = h('span.pill', h('span.dot'), h('span'));

  const root = h('div.page',
    h('section.panel.stack',
      h('div.panel-head', h('h3', spec.title), h('span.sub', 'Hover a step to pick out its part or wire; Play builds it one step at a time')),
      spec.intro && h('p.muted', spec.intro),
      h('div.wiring', art, h('div.stack', controls, list)),
      h('div.wiring-legend',
        ...['red', 'black', 'yellow', 'blue'].filter((c) => spec.wires.some((w) => w.color === c)).map((c) => h('span', h('span.sw', { style: { background: WIRE(c) } }), { red: 'supply', black: 'ground', yellow: 'signal', blue: 'signal' }[c])),
        h('span.faint', 'Hover a header pin for its name. Jumpers on the morpho pins need a female end.'))),
    h('div.cols-2',
      h('section.panel.stack',
        h('div.panel-head', h('h3', 'Circuit check'), h('span.sub', 'Each leg traced through the breadboard strips and wires')),
        verdict, checkTable,
        live && h('div.stack', { style: { gap: 'var(--sp-2)' } }, h('span.label', 'On the board now'), h('div.row', livePill, liveRow))),
      h('section.panel.stack',
        h('div.panel-head', h('h3', 'Before you plug in USB')),
        spec.notes?.length && h('ul.plain', spec.notes.map((n) => h('li', n))),
        spec.verify?.length && h('div.stack', { style: { gap: 'var(--sp-2)' } }, h('span.label', 'Measure'), h('ul.plain', spec.verify.map((n) => h('li', n)))))));
  show(-1);

  return {
    root,
    update(store) {
      if (!live) return;
      const st = live(store);
      setTone(livePill, st.tone);
      setText(livePill.children[1], st.label || { ok: 'Seen', warn: 'Check', bad: 'Not seen', idle: 'Waiting' }[st.tone] || '');
      fill(liveRow, st.text);
    },
  };
}
