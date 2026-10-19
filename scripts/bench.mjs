#!/usr/bin/env node
// One-command launcher for the calibration bench, used by the package.json scripts:
//
//   pnpm dev                 dashboard on the simulator
//   pnpm dev:board           dashboard on the Nucleo (port found automatically, or BENCH_PORT=/dev/…)
//   pnpm test                calibration tests
//
// Extra arguments go straight to `python -m calib serve`, e.g. pnpm dev:board --gt-cmd "python3 gt.py".
// On first run it creates .venv and installs calibration/requirements.txt; it reinstalls only when that
// file changes. If port 8765 is taken (another dashboard is running), it uses the next free one.
import { spawn, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const CAL = join(ROOT, 'calibration');
const VENV = join(ROOT, '.venv');
const WIN = process.platform === 'win32';
const PY = join(VENV, WIN ? 'Scripts/python.exe' : 'bin/python');
const REQS = join(CAL, 'requirements.txt');
const STAMP = join(VENV, '.bench-requirements');
const DEFAULT_HTTP_PORT = 8765;

const fail = (msg) => { console.error(`\nbench: ${msg}\n`); process.exit(1); };
const say = (msg) => console.log(`bench: ${msg}`);

function run(cmd, args, opts = {}) {
  const r = spawnSync(cmd, args, { stdio: 'inherit', ...opts });
  if (r.error) fail(`could not run ${cmd}: ${r.error.message}`);
  if (r.status !== 0) fail(`${cmd} ${args.join(' ')} exited with ${r.status}`);
}

function ensurePython() {
  if (!existsSync(PY)) {
    const sys = WIN ? 'python' : 'python3';
    const v = spawnSync(sys, ['-c', 'import sys; print("%d.%d" % sys.version_info[:2]); sys.exit(sys.version_info < (3, 10))'], { encoding: 'utf8' });
    if (v.error) fail(`${sys} not found. Install Python 3.10 or newer.`);
    if (v.status !== 0) fail(`Python ${v.stdout.trim()} is too old: the bench needs 3.10 or newer.`);
    say(`creating .venv with Python ${v.stdout.trim()}`);
    run(sys, ['-m', 'venv', VENV]);
  }
  const want = createHash('sha256').update(readFileSync(REQS)).digest('hex');
  const have = existsSync(STAMP) ? readFileSync(STAMP, 'utf8').trim() : '';
  if (want !== have) {
    say('installing calibration requirements (first run, or requirements.txt changed)');
    run(PY, ['-m', 'pip', 'install', '--quiet', '--upgrade', 'pip']);
    run(PY, ['-m', 'pip', 'install', '--quiet', '--requirement', REQS]);
    writeFileSync(STAMP, want + '\n');
  }
}

function findBoardPort() {
  if (process.env.BENCH_PORT) return process.env.BENCH_PORT;
  if (WIN) fail('pass the COM port: pnpm dev:board --port COM5 (or set BENCH_PORT)');
  const ports = readdirSync('/dev').filter((n) => /^(cu\.usbmodem|ttyACM)/.test(n)).map((n) => `/dev/${n}`);
  if (!ports.length) fail('no Nucleo found (no /dev/cu.usbmodem* or /dev/ttyACM*). Plug in the ST-LINK USB port (CN1) with a data cable, or run pnpm dev for the simulator.');
  if (ports.length > 1) say(`several boards found (${ports.join(', ')}); using ${ports[0]}. Set BENCH_PORT to choose.`);
  return ports[0];
}

const portFree = (port) => new Promise((resolve) => {
  const s = createServer().once('error', () => resolve(false)).once('listening', () => s.close(() => resolve(true)));
  s.listen(port, '127.0.0.1');
});

async function freeHttpPort() {
  for (let p = DEFAULT_HTTP_PORT; p < DEFAULT_HTTP_PORT + 20; p++) {
    if (await portFree(p)) {
      if (p !== DEFAULT_HTTP_PORT) say(`port ${DEFAULT_HTTP_PORT} is in use (another dashboard?), using ${p}`);
      return p;
    }
  }
  fail(`ports ${DEFAULT_HTTP_PORT}–${DEFAULT_HTTP_PORT + 19} are all in use; pass --http-port`);
}

async function serve(board, extra) {
  ensurePython();
  const args = ['-m', 'calib', 'serve', ...extra];
  if (board && !extra.includes('--port')) args.push('--port', findBoardPort());
  if (!board && !extra.includes('--sim') && !extra.includes('--port') && !extra.includes('--replay')) args.push('--sim');
  if (!extra.includes('--http-port')) args.push('--http-port', String(await freeHttpPort()));
  say(`python ${args.join(' ')}`);
  const child = spawn(PY, args, { cwd: CAL, stdio: 'inherit' });
  for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => child.kill(sig));
  child.on('exit', (code) => process.exit(code ?? 0));
}

const [cmd = 'dev', ...rest] = process.argv.slice(2);
const extra = rest[0] === '--' ? rest.slice(1) : rest;
if (cmd === 'dev') await serve(false, extra);
else if (cmd === 'board') await serve(true, extra);
else if (cmd === 'test') { ensurePython(); run(PY, ['-m', 'pytest', '-q', ...extra], { cwd: CAL }); }
else fail(`unknown command ${cmd}: use dev, board or test`);
