// Sensor registry, in this order (the chain: temperature and ρ, then the force on the plate, then the wind speed the
// board computes from both). Each entry gets a tile on the one-screen dashboard and its own calibration page.
//
// To add a sensor: copy _template.js to <name>.js, fill it in, import it here and add it to the list.
// To remove one: delete its line. Nothing else changes: core/sensor-page.js renders every module alike.
//
// {
//   id, title, part, intro,            page id (also the URL hash), heading, hardware line, one-sentence purpose
//   allClear,                          shown when checks() returns nothing
//   status(store)   -> { tone: 'ok' | 'warn' | 'bad' | 'idle', text }      page and tile pill, top-bar badge
//   available?(store) -> { live: true } | { live: false, title, detail, commands: [shell] }
//                                      false: the running firmware does not read this part; only the notice
//                                      and the reference show, and its tile and top-bar entry are dimmed
//   readouts: [{ label, unit, value(store), digits | format(v), note?(store), tone?(store) }]   stat strip
//   views:    [{ id, label, title, sub, kind?: 'time' | 'xy', tall?, legend: [{ key, label, color, dash? }],
//                draw(chart, store, hiddenKeys) }]                         chart panel, one view at a time
//   panels?:  [{ title, sub?, render(ctx) -> Node, update?(body, store) }]  extra live content
//   steps:    [{ id, title, why, state(store) -> { tone: 'done' | 'todo' | 'blocked' | 'skip', text },
//                body(ctx) -> Node, key?(store), update?(body, store) }]   the calibration procedure
//   settings?: { title, sub, fields: [see core/ui.js settingField], footer?(ctx) -> Node }
//   checks(store, ctx) -> [{ id, level: 'bad' | 'warn' | 'info', title, detail, action?: { label, run(ctx) } }]
//   tile?: { readouts: [labels from readouts], view: id from views, wide?, footer?(ctx) -> Node }
//                                      its tile on the dashboard (core/dashboard.js); default: 3 readouts, 1st view
//   info?: { summary, brief, source, parts: [{ name, role, connect, spec }], test: [string], commands: [{ label, cmd }] }
//                                      the Reference panel at the foot of the tab
//   wiring?: spec from js/wiring/parts/<name>.js   a Wiring sub-tab: breadboard drawing, build steps, circuit check
//   wiringLive?(store) -> { tone, label?, text }    "on the board now" line on the Wiring tab
//   datasheet?: { file, title, source, pages: [{ page, label }] }   a Datasheet sub-tab (PDF in docs/datasheets/)
// }
//
// `store` is the live state (core/store.js), ctx = { store, actions, toast, refresh }. Keep a sensor's own
// progress in a module-level object so it survives a remount.
//
// Rig-agnostic: sensors are tested on the open bench (no fan, no pitot) and in the tunnel. A step that needs
// airflow or the pitot returns { tone: 'skip' } when they are missing; it never blocks the bench steps.
import temperature from './temperature.js';
import loadCell from './load-cell.js';
import windSpeed from './wind-speed.js';

export default [temperature, loadCell, windSpeed];
