import test from 'node:test';
import assert from 'node:assert/strict';
import def, { confirmedCommand } from '../app/static/js/sensors/load-cell-lab.js';

function context(reply, fields = {}) {
  const listeners = new Set();
  const store = {
    streaming: true, tele: { loadcell_lab: { tare_seq: 0, calibration_seq: 0, error_seq: 0, ...fields } },
    on(type, fn) { listeners.add(fn); return () => listeners.delete(fn); },
    emit() { for (const fn of listeners) fn(); },
  };
  return { store, actions: { command(line) { queueMicrotask(() => reply(store, line)); return true; } }, listeners };
}

test('tare completes only after a new board confirmation', async () => {
  let sent;
  const ctx = context((s, line) => { sent = line; s.tele.loadcell_lab.tare_seq++; s.emit(); });
  await confirmedCommand(ctx, 't', 'tare_seq');
  assert.equal(sent, 't');
  assert.equal(ctx.listeners.size, 0);
});

test('board rejection propagates without optimistic success', async () => {
  const ctx = context((s) => {
    Object.assign(s.tele.loadcell_lab, { error_seq: 1, last_error: 'Load change too small' }); s.emit();
  });
  await assert.rejects(confirmedCommand(ctx, 'k 249', 'calibration_seq'), /Load change too small/);
  assert.equal(ctx.listeners.size, 0);
});

test('disconnect during calibration rejects and allows another attempt', async () => {
  const ctx = context((s) => { s.streaming = false; s.emit(); });
  await assert.rejects(confirmedCommand(ctx, 'k 249', 'calibration_seq'), /disconnected/);
  const retry = context((s) => { s.tele.loadcell_lab.calibration_seq++; s.emit(); });
  await confirmedCommand(retry, 'k 249', 'calibration_seq');
});

test('grams remain blank until the board confirms calibration and a weight', () => {
  const s = { tele: { loadcell_lab: { calibrated: false, weight_g: 249 } } };
  assert.ok(Number.isNaN(def.readouts[0].value(s)));
  s.tele.loadcell_lab.calibrated = true;
  assert.equal(def.readouts[0].value(s), 249);
  s.tele.loadcell_lab.weight_g = null;
  assert.ok(Number.isNaN(def.readouts[0].value(s)));
});
