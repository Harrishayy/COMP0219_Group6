// Shared UI pieces used by every page: toasts, async buttons, the settings field and small tables.
import { h, setText } from './dom.js';
import { isNum } from './format.js';
import { store, actions } from './store.js';

// ------------------------------------------------------------------------------------------- toasts
let toastHost = null;
export function toast(msg, tone = 'ok', ms = 4000) {
  if (!toastHost) {
    toastHost = h('div.toasts', { role: 'status', 'aria-live': 'polite' });
    document.body.append(toastHost);
  }
  const el = h('div.toast', h(`span.dot.${tone}`), h('span', msg));
  toastHost.append(el);
  setTimeout(() => el.remove(), ms);
}

/** A button that shows a busy label while its async handler runs; errors become toasts. */
export function asyncButton(label, run, { primary = false, busy = 'Working…', cls = '' } = {}) {
  const b = h(`button.btn${primary ? '.primary' : ''}${cls}`, { type: 'button' }, label);
  b.addEventListener('click', async () => {
    if (b.disabled) return;
    b.disabled = true;
    b.setAttribute('aria-busy', 'true');
    b.textContent = busy;
    try {
      await run(b);
    } catch (e) {
      toast(e.message || String(e), 'bad', 6000);
    } finally {
      b.disabled = false;
      b.removeAttribute('aria-busy');
      b.textContent = label;
    }
  });
  return b;
}

export function button(label, onclick, { primary = false, cls = '', title } = {}) {
  return h(`button.btn${primary ? '.primary' : ''}${cls}`, { type: 'button', onclick, title }, label);
}

export function numberInput({ value = '', step = 'any', min, max, width = '110px', label, onEnter } = {}) {
  const el = h('input.input.num', { type: 'number', step, min, max, value, 'aria-label': label, style: { width } });
  if (onEnter) el.addEventListener('keydown', (e) => { if (e.key === 'Enter') onEnter(el); });
  return el;
}

export function kv(pairs) {
  return h('dl.kv', pairs.filter(Boolean).flatMap(([k, v]) => [h('dt', k), h('dd', v)]));
}

export function table(headers, rows, { rightFrom = 1 } = {}) {
  return h('div.table-wrap', h('table.table',
    h('thead', h('tr', headers.map((c, i) => h(`th${i >= rightFrom ? '.r' : ''}`, c)))),
    h('tbody', rows.map((r) => h('tr', r.map((c, i) => {
      if (c && typeof c === 'object' && !(c instanceof Node) && 'v' in c) return h(`td${i >= rightFrom ? '.r' : ''}${c.cls ? '.' + c.cls : ''}`, c.v);
      return h(`td${i >= rightFrom ? '.r' : ''}`, c);
    }))))));
}

// --------------------------------------------------------------------------------------- settings
/**
 * One firmware / PC setting: slider + typed value, the board's value, and a "not on board" mark.
 * field: { name, label, help, kind: 'number' | 'choice', options: [[value, label]], view: { unit, min, max,
 *          step, to(v), from(x) }, showIf(params) }. `view` maps stored units to display units (Cd·A in cm²).
 */
export function settingField(field) {
  const spec = store.specs[field.name] || {};
  const view = {
    unit: field.view?.unit ?? spec.unit ?? '',
    min: field.view?.min ?? spec.min, max: field.view?.max ?? spec.max, step: field.view?.step ?? spec.step,
    digits: field.view?.digits ?? 4,
    to: field.view?.to || ((v) => v), from: field.view?.from || ((x) => x),
  };
  const fmt = (v) => (isNum(v) ? String(Number(view.to(v).toPrecision(view.digits))) : '');
  const boardEl = h('span.board');
  const root = h('div.setting');
  let update;

  if (field.kind === 'choice') {
    const seg = h('div.segmented', { role: 'group', 'aria-label': field.label },
      field.options.map(([v, lbl]) => h('button', { type: 'button', 'aria-pressed': 'false', dataset: { v }, onclick: () => actions.setParam(field.name, v) }, lbl)));
    root.append(h('div.top', h('label', field.label), boardEl), seg, field.help && h('p.help', field.help));
    update = () => {
      const cur = store.params[field.name] >= 0.5 ? 1 : 0;
      for (const b of seg.children) b.setAttribute('aria-pressed', String(Number(b.dataset.v) === cur));
    };
  } else {
    const id = `set-${field.name}`;
    const num = h('input.input.num', { id, type: 'number', step: 'any' });
    const range = h('input', { type: 'range', min: 0, max: 1000, step: 1, 'aria-label': field.label });
    const toRange = (x) => String(Math.round(((x - view.min) / (view.max - view.min)) * 1000));
    const fromRange = () => view.min + (Number(range.value) / 1000) * (view.max - view.min);
    const commit = (x) => {
      if (!isNum(x)) return;
      const v = view.from(x);
      if (isNum(v)) actions.setParam(field.name, v);
    };
    range.addEventListener('input', () => { num.value = String(Number(fromRange().toPrecision(view.digits))); });
    range.addEventListener('change', () => commit(Number(num.value)));
    num.addEventListener('change', () => commit(Number(num.value)));
    root.append(
      h('div.top', h('label', { for: id }, field.label), boardEl),
      h('div.ctl', range, h('span.unit-in', num, h('em', view.unit))),
      field.help && h('p.help', field.help),
    );
    update = () => {
      const v = store.params[field.name];
      if (document.activeElement !== num && document.activeElement !== range) {
        num.value = fmt(v);
        if (isNum(v)) range.value = toRange(view.to(v));
      }
    };
  }

  root.update = () => {
    root.hidden = field.showIf ? !field.showIf(store.params) : false;
    update();
    const dirty = store.isDirty(field.name);
    root.classList.toggle('dirty', dirty);
    const b = store.board[field.name];
    setText(boardEl, isNum(b) && store.firmwareParams.includes(field.name)
      ? `${dirty ? 'board has ' : 'on board '}${field.kind === 'choice' ? (field.options.find(([v]) => v === (b >= 0.5 ? 1 : 0))?.[1] ?? b) : fmt(b) + (view.unit ? ' ' + view.unit : '')}`
      : '');
  };
  root.update();
  return root;
}
