#!/usr/bin/env node
// Checks every wiring diagram in calibration/app/static/js/wiring/parts/ as a circuit (the same check the
// dashboard's Wiring tab runs), and that the pins it uses are the ones the firmware reads (board_config.h).
// Exits 1 on any error. Run by `pnpm test` and `pnpm check:wiring`.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const WIRING = join(ROOT, 'calibration/app/static/js/wiring');
const { checkWiring } = await import(join(WIRING, 'breadboard.js'));
const { default: specs } = await import(join(WIRING, 'parts/index.js'));

let failed = 0;
for (const spec of specs) {
  const r = checkWiring(spec);
  const problems = [...r.errors];
  for (const f of spec.firmware || []) {
    const src = readFileSync(join(ROOT, f.file), 'utf8');
    const m = new RegExp(`\\b${f.name}\\s*=\\s*([^;]+);`).exec(src);
    if (!m) problems.push(`${f.file} has no ${f.name}`);
    else if (m[1].trim() !== f.value) problems.push(`${f.file}: ${f.name} is ${m[1].trim()}, the diagram uses ${f.value}`);
  }
  const ok = problems.length === 0;
  if (!ok) failed++;
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${spec.id}: ${r.pins.filter((p) => p.ok).length}/${r.pins.length} pins reach their signal`);
  for (const p of problems) console.log(`     ${p}`);
  for (const w of r.warnings) console.log(`     warning: ${w}`);
}
if (failed) process.exit(1);
