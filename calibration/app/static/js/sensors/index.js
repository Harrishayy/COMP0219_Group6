// Sensor registry: one tab per entry, in this order. Every part the COMP0219 brief lists has a tab; parts the
// running firmware does not read show their reference and how to bench-test them instead of live data.
//
// To add a sensor: copy _template.js to <name>.js, fill it in, import it here and add it to the list.
// To remove one: delete its line. Nothing else changes: core/sensor-page.js renders every module alike.
//
// {
//   id, title, part, intro,            tab id (also the URL hash), heading, hardware line, one-sentence purpose
//   allClear,                          shown when checks() returns nothing
//   status(store)   -> { tone: 'ok' | 'warn' | 'bad' | 'idle', text }      page pill and tab badge
//   available?(store) -> { live: true } | { live: false, title, detail, commands: [shell] }
//                                      false: the running firmware does not read this part; only the notice
//                                      and the reference show, and the tab is dimmed
//   readouts: [{ label, unit, value(store), digits | format(v), note?(store), tone?(store) }]   stat strip
//   views:    [{ id, label, title, sub, kind?: 'time' | 'xy', tall?, legend: [{ key, label, color, dash? }],
//                draw(chart, store, hiddenKeys) }]                         chart panel, one view at a time
//   panels?:  [{ title, sub?, render(ctx) -> Node, update?(body, store) }]  extra live content
//   steps:    [{ id, title, why, state(store) -> { tone: 'done' | 'todo' | 'blocked' | 'skip', text },
//                body(ctx) -> Node, key?(store), update?(body, store) }]   the calibration procedure
//   settings?: { title, sub, fields: [see core/ui.js settingField], footer?(ctx) -> Node }
//   checks(store, ctx) -> [{ id, level: 'bad' | 'warn' | 'info', title, detail, action?: { label, run(ctx) } }]
//   info?: { summary, brief, source, parts: [{ name, role, connect, spec }], test: [string], commands: [{ label, cmd }] }
//                                      the Reference panel at the foot of the tab
// }
//
// `store` is the live state (core/store.js), ctx = { store, actions, toast, refresh }. Keep a sensor's own
// progress in a module-level object so it survives tab switches.
//
// Rig-agnostic: sensors are tested on the open bench (no fan, no pitot) and in the tunnel. A step that needs
// airflow or the pitot returns { tone: 'skip' } when they are missing; it never blocks the bench steps.
import loadCell from './load-cell.js';
import environment from './environment.js';
import pitot from './pitot.js';
import airspeed from './airspeed.js';
import encoder from './encoder.js';
import hall from './hall.js';
import imu from './imu.js';

export default [loadCell, environment, pitot, airspeed, encoder, hall, imu];
