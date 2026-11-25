#!/usr/bin/env node
// One-command launcher for the calibration bench, used by the package.json scripts:
//
//   pnpm dev                 dashboard on the Nucleo, with the white tunnel's VDAS box as ground truth (each waits
//                            until plugged in; fan off at start); --gt-giga for the wood tunnel's GIGA, --no-gt for none
//   pnpm dev:board           the same (kept for habit); BENCH_PORT=/dev/… picks the port
//   pnpm dev:sim             dashboard on the simulator (every reading synthetic, and labelled as such)
//   pnpm test                calibration tests
//   pnpm logger              the course's Wind Tunnel Logger (tools/wind-tunnel-logger), runs saved in data/tunnel/
//   pnpm flash <profile>     flash a calibration profile's build (env airspeed_drag_plate_<profile>), then read the
//                            board back and check it runs that profile. pnpm flash:default / pnpm flash:new are the
//                            saved 4 Nov default and the 5 Nov tunnel fit; pnpm flash alone lists the profiles
//
// Extra arguments go straight to `python -m calib serve`, e.g. pnpm dev:board --gt-cmd "python3 gt.py".
//
// Live reload: open pages reload when a dashboard file changes (the server watches calibration/app/static),
// and the server restarts when a toolkit .py file changes (watched here), after which the page reloads itself.
// Firmware is never flashed automatically. BENCH_RELOAD=0 turns it all off.
// On first run it creates .venv and installs calibration/requirements.txt; it reinstalls only when that
// file changes. If port 8765 is taken (another dashboard is running), it uses the next free one.
import { spawn, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync, watch, writeFileSync } from 'node:fs';
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
const LOGGER = join(ROOT, 'tools/wind-tunnel-logger');
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
  ensureRequirements(REQS, STAMP, 'calibration requirements');
}

// Installs a requirements file into .venv, again only when the file changes.
function ensureRequirements(file, stamp, label) {
  const want = createHash('sha256').update(readFileSync(file)).digest('hex');
  const have = existsSync(stamp) ? readFileSync(stamp, 'utf8').trim() : '';
  if (want !== have) {
    say(`installing ${label} (first run, or its requirements.txt changed)`);
    run(PY, ['-m', 'pip', 'install', '--quiet', '--upgrade', 'pip']);
    run(PY, ['-m', 'pip', 'install', '--quiet', '--requirement', file]);
    writeFileSync(stamp, want + '\n');
  }
}

// A fixed port when one is set or exactly one is plugged in; otherwise `auto`, which makes the server wait
// for a Nucleo (and show "No Nucleo found" on the dashboard) instead of failing or faking data.
function findBoardPort() {
  if (process.env.BENCH_PORT) return process.env.BENCH_PORT;
  if (WIN) return 'auto';
  const ports = readdirSync('/dev').filter((n) => /^(cu\.usbmodem|ttyACM)/.test(n)).map((n) => `/dev/${n}`);
  if (!ports.length) {
    say('no Nucleo plugged in yet: the dashboard will say so and connect when one appears (pnpm dev:sim for the simulator)');
    return 'auto';
  }
  if (ports.length > 1) {
    // a GIGA pitot tube is a usbmodem too: let the server pick the Nucleo by its ST-LINK USB id
    say(`several boards found (${ports.join(', ')}); using the one with ST-LINK's USB id. Set BENCH_PORT to choose.`);
    return 'auto';
  }
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

// Ground truth: the white tunnel's VDAS box unless another source is named (--gt-giga, --gt-cmd, …) or --no-gt is given.
function withGroundTruth(extra) {
  if (extra.includes('--no-gt')) return extra.filter((a) => a !== '--no-gt');
  if (extra.some((a) => a.startsWith('--gt-') && !['--gt-p-pa', '--gt-tair-c', '--gt-baud', '--gt-pressure'].includes(a))) return extra;
  if (extra.includes('--sim') || extra.includes('--replay')) return extra;
  return [...extra, '--gt-vdas'];
}

async function serve(board, extra) {
  ensurePython();
  if (board) extra = withGroundTruth(extra);
  const args = ['-m', 'calib', 'serve', ...extra];
  if (board && !extra.includes('--port') && !extra.includes('--sim') && !extra.includes('--replay')) args.push('--port', findBoardPort());
  if (!board && !extra.includes('--sim') && !extra.includes('--port') && !extra.includes('--replay')) args.push('--sim');
  if (!extra.includes('--http-port')) args.push('--http-port', String(await freeHttpPort()));
  const reload = process.env.BENCH_RELOAD !== '0';
  if (reload && !extra.includes('--live-reload')) args.push('--live-reload');
  say(`python ${args.join(' ')}`);

  let child = null, restarting = false, quitting = false;
  const start = (again) => {
    child = spawn(PY, again ? [...args, '--reopen'] : args, { cwd: CAL, stdio: 'inherit' });
    child.on('exit', (code) => {
      if (restarting) { restarting = false; return start(true); }
      if (quitting || !reload) process.exit(code ?? 0);
      say(`server exited (${code}); fix the error and save, it restarts on the next change`);
      child = null;
    });
  };
  start(false);
  for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => { quitting = true; child ? child.kill(sig) : process.exit(0); });
  if (!reload) return;

  let timer = null;
  const onChange = (_event, file) => {
    if (!file || !file.endsWith('.py') || /(^|[\\/])(tests|runs|exports)[\\/]|__pycache__/.test(file)) return;
    clearTimeout(timer);
    timer = setTimeout(() => {  // debounce: an editor saves several files at once
      say(`${file} changed: restarting the server`);
      if (child) { restarting = true; child.kill('SIGTERM'); } else start(true);
    }, 300);
  };
  try {
    watch(CAL, { recursive: true }, onChange);
  } catch (e) {
    say(`cannot watch Python files (${e.message}); dashboard files still live-reload`);
  }
}

// Flash a calibration profile's firmware build and check the board took it. Every profile build is the competition
// firmware (the USER-button tare included) with that profile's numbers; the build is cached, so a flash takes seconds.
async function flash(profile) {
  ensurePython();
  const FW = join(ROOT, 'firmware');
  const envs = [...readFileSync(join(FW, 'platformio.ini'), 'utf8').matchAll(/^\[env:airspeed_drag_plate_(\w+)\]/gm)].map((m) => m[1]);
  if (!profile || !envs.includes(profile)) {
    if (profile) say(`no firmware build for profile "${profile}"`);
    fail(`pnpm flash <profile>, one of: ${envs.join(', ')}\n       (pnpm flash:default = default_20261104, pnpm flash:new = new_calibration_20261105)`);
  }
  const PIO = join(VENV, WIN ? 'Scripts/pio.exe' : 'bin/pio');
  if (!existsSync(PIO)) fail(`PlatformIO not found at ${PIO}: ${PY} -m pip install platformio`);
  say(`flashing airspeed_drag_plate_${profile} (the flashed calibration stays until the next flash; reset keeps it)`);
  run(PIO, ['run', '-e', `airspeed_drag_plate_${profile}`, '-t', 'upload'], { cwd: FW });
  say('reading the board back');
  run(PY, ['-m', 'calib', 'board-check', profile, ...(process.env.BENCH_PORT ? ['--port', process.env.BENCH_PORT] : [])], { cwd: CAL });
}

const [cmd = 'dev', ...rest] = process.argv.slice(2);
const extra = rest[0] === '--' ? rest.slice(1) : rest;
if (cmd === 'dev' || cmd === 'board') await serve(true, extra);
else if (cmd === 'sim') await serve(false, extra);
else if (cmd === 'test') {
  ensurePython();
  run(PY, ['-m', 'pytest', '-q', ...extra], { cwd: CAL });
  run(process.execPath, ['--test', join(CAL, 'tests/load-cell-lab.test.mjs')]);
  run(process.execPath, [join(ROOT, 'scripts/check-wiring.mjs')]);
} else if (cmd === 'logger') {
  ensurePython();
  ensureRequirements(join(LOGGER, 'requirements.txt'), join(VENV, '.logger-requirements'), 'Wind Tunnel Logger requirements');
  say('runs are saved in data/tunnel/. With pnpm dev running, pick the port "Board via the dashboard"; on the white tunnel start it as pnpm dev --no-gt so the logger can open the VDAS box. Otherwise pick the board itself.');
  run(PY, [join(LOGGER, 'run.py'), ...extra], { cwd: ROOT });
} else if (cmd === 'flash') {
  await flash(extra[0]);
} else fail(`unknown command ${cmd}: use dev, board, sim, test, logger or flash`);
