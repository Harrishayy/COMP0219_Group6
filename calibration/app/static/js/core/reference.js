// Reference panel: what a sensor is, how it is wired, what the brief asks of it and how to bench-test it.
// Rendered at the foot of every sensor tab from the module's `info` (see js/sensors/index.js), and on its own
// when the part is not in the running build.
import { h } from './dom.js';
import { table } from './ui.js';

/** A shell command with a copy button (clipboard may be refused: then the text is selected instead). */
export function command(cmd) {
  const code = h('code', cmd);
  const copy = h('button.btn.quiet.sm', {
    type: 'button',
    onclick: async () => {
      try {
        await navigator.clipboard.writeText(cmd);
        copy.textContent = 'Copied';
        setTimeout(() => { copy.textContent = 'Copy'; }, 1200);
      } catch {
        const r = document.createRange();
        r.selectNodeContents(code);
        getSelection().removeAllRanges();
        getSelection().addRange(r);
      }
    },
  }, 'Copy');
  return h('div.cmd', code, copy);
}

export function referencePanel(info) {
  if (!info) return null;
  const parts = info.parts?.length
    ? table(['Part', 'Role', 'Connects to', 'Spec'], info.parts.map((p) => [h('strong', p.name), p.role || '', h('span.mono', p.connect || ''), p.spec || '']), { rightFrom: 9 })
    : null;
  const test = info.test?.length && h('div.stack',
    h('span.label', 'Bench test'),
    h('ol', info.test.map((s) => h('li', h('span', s)))));
  const cmds = info.commands?.length && h('div.stack', { style: { gap: 'var(--sp-2)' } },
    h('span.label', 'Commands'),
    info.commands.map((c) => h('div.field', c.label && h('span.help', c.label), command(c.cmd))));
  return h('section.panel.stack',
    h('div.panel-head', h('h3', 'Reference'), info.source && h('span.sub', info.source)),
    h('div.ref',
      h('div.stack', info.summary && h('p.summary', info.summary), parts, info.brief && h('p.brief', h('b', 'Brief'), info.brief)),
      h('div.stack', test, cmds)));
}
