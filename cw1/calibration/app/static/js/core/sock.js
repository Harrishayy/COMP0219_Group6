// Sock, the windsock mascot in the app bar. Its mood says what the bench is doing: no wind (idle), streaming,
// all steps done for this sensor, or something to fix. Colours are tokens, so it follows any restyle.
const MOODS = {
  idle: { f: 0, col: '--idle', label: 'not connected' },
  stream: { f: 0.45, col: '--accent', label: 'streaming', sway: true },
  done: { f: 1, col: '--ok', label: 'calibrated' },
  fault: { f: 0.18, col: '--bad', label: 'needs a fix' },
};

export function sock(mood = 'idle', size = 32) {
  const m = MOODS[mood] || MOODS.idle;
  const L = 34 + 56 * m.f, ang = ((1 - m.f) * 72 * Math.PI) / 180;   // droops when there is no wind
  const bx = 22, by = 22, h0 = 11, h1 = 4.5;
  const pt = (d, side) => {
    const hh = h0 + ((h1 - h0) * d) / L, x = bx + d * Math.cos(ang), y = by + d * Math.sin(ang);
    return [x - side * hh * Math.sin(ang), y + side * hh * Math.cos(ang)].map((v) => v.toFixed(1)).join(',');
  };
  let bands = '';
  for (let i = 0; i < 4; i++) {
    const a = (L * i) / 4, b = (L * (i + 1)) / 4;
    bands += `<polygon points="${pt(a, -1)} ${pt(b, -1)} ${pt(b, 1)} ${pt(a, 1)}" fill="var(${i % 2 ? '--surface' : m.col})" stroke="var(--ink)" stroke-width="2.4" stroke-linejoin="round"/>`;
  }
  return `<svg width="${size}" height="${size}" viewBox="0 0 110 110" role="img" aria-label="Bench: ${m.label}">
    <rect x="18" y="16" width="6" height="88" rx="2" fill="var(--ink)"/><rect x="9" y="99" width="24" height="6" rx="2" fill="var(--ink)"/>
    <g class="${m.sway ? 'sock-sway' : ''}">${bands}</g></svg>`;
}

/**
 * Browser-tab icon: Sock in its current mood with a status dot (tone: ok | warn | bad | idle) in the corner.
 * A favicon is drawn outside the page, so token colours are resolved to their values here.
 */
export function favicon(mood = 'idle', tone = 'idle') {
  const css = getComputedStyle(document.documentElement);
  const svg = sock(mood, 64)
    .replace(/ role="img" aria-label="[^"]*"/, ' xmlns="http://www.w3.org/2000/svg"')
    .replace('</svg>', `<circle cx="86" cy="86" r="20" fill="var(--${tone})" stroke="var(--surface)" stroke-width="7"/></svg>`)
    .replace(/var\((--[\w-]+)\)/g, (_, name) => css.getPropertyValue(name).trim() || '#000');
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}
