// Datasheet tab: the part's PDF in the browser's own viewer (scroll, zoom, search), with jumps to the pages
// the team needs. PDFs live in docs/datasheets/ and the server serves them at /datasheets/.
//
// A sensor module's `datasheet`: { file: 'lm35.pdf', title, source, pages: [{ page, label }] }.
import { h } from './dom.js';

export function datasheetPanel(ds) {
  const url = `/datasheets/${encodeURIComponent(ds.file)}`;
  const frameHost = h('div.pdf');
  let current = null;
  const open = (page) => {
    current = page;
    // A new frame per jump: viewers ignore a changed #page on a document they already show.
    frameHost.replaceChildren(h('iframe', { src: `${url}#page=${page}&view=FitH`, title: `${ds.title}, page ${page}`, loading: 'lazy' }));
    for (const b of jumps.querySelectorAll('button')) b.setAttribute('aria-pressed', String(Number(b.dataset.page) === page));
  };
  const jumps = h('nav.pdf-jumps', { 'aria-label': 'Datasheet sections' },
    h('span.label', 'Go to'),
    (ds.pages || []).map((p) => h('button', { type: 'button', dataset: { page: p.page }, onclick: () => open(p.page) },
      h('span', p.label), h('span.num', `p. ${p.page}`))));
  const root = h('section.panel.stack',
    h('div.panel-head', h('h3', ds.title), ds.source && h('span.sub', ds.source), h('span.spacer'),
      h('a.btn.quiet.sm', { href: url, target: '_blank', rel: 'noopener' }, 'Open in a new tab')),
    h('div.pdf-wrap', jumps, frameHost));
  return {
    root,
    /** Load the PDF the first time the tab is shown (they are 1–2 MB). */
    show() { if (current === null) open(ds.pages?.[0]?.page || 1); },
  };
}
