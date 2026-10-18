// Runs: record sessions, write the calibration into the firmware, browse and replay past runs, activity log.
import { h, fill, setText } from '../core/dom.js';
import { fixed, smart, duration, dateTime, clock, isNum } from '../core/format.js';
import { store, actions } from '../core/store.js';
import { toast, button, table } from '../core/ui.js';

const PARAM_LABELS = {
  sqrt_k: 'Drag area k = 2/(Cd·A)', sqrt_x0: 'Zero-flow force F₀', lc_offset: 'Load-cell zero', lc_n_per_count: 'Load-cell scale',
  filter_kind: 'Smoothing filter', ema_alpha: 'Smoothing α', lpf_hz: 'Butterworth cutoff', lead_tau: 'Lag compensation',
};

export default {
  id: 'runs',
  title: 'Runs',
  status() {
    if (store.session?.recording) return { tone: 'bad', text: 'Recording' };
    return { tone: 'idle', text: '' };
  },
  mount(root) {
    // ------------------------------------------------------------ recording
    const label = h('input.input', { placeholder: 'Label, e.g. sweep-after-fit', 'aria-label': 'Recording label', style: { flex: '1', minWidth: '12rem' } });
    const note = h('input.input', { placeholder: 'Note, e.g. fan to 8 m/s', 'aria-label': 'Note', style: { flex: '1', minWidth: '12rem' } });
    const startBtn = button('Start recording', () => actions.record(true, label.value.trim() || undefined), { primary: true });
    const stopBtn = button('Stop and save', () => actions.record(false), { cls: '.danger' });
    const addNote = () => { if (note.value.trim()) { actions.note(note.value.trim()); note.value = ''; } };
    note.addEventListener('keydown', (e) => { if (e.key === 'Enter') addNote(); });
    const stat = (lbl) => { const v = h('span'); return [h('div.readout', h('span.label', lbl), h('div.value', v)), v]; };
    const [elEl, elV] = stat('Elapsed'), [rowsEl, rowsV] = stat('Samples'), [segEl, segV] = stat('Segments');
    const segTable = h('div');
    const idleView = h('div.stack', h('p.muted', 'Saves every 200 Hz sample with the pitot, the board output, the PC preview and the settings in force. Each settings change starts a new segment, scored separately.'), h('div.row', label, startBtn));
    const recView = h('div.stack', h('div.readouts', elEl, rowsEl, segEl), h('div.row', note, button('Add note', addNote), h('span.spacer'), stopBtn), segTable);

    // ------------------------------------------------------------ calibration / export
    const paramTable = h('div');
    const install = h('input', { type: 'checkbox' });
    const exported = h('div');
    const calPanel = h('section.panel.stack',
      h('div.panel-head', h('h3', 'Calibration'), h('span.sub', 'What the PC has against what the board runs')),
      paramTable,
      h('div.row',
        button('Push to board', () => { actions.push(); toast('Settings sent to the board.'); }),
        button('Export files', () => actions.exportCalibration(install.checked), { primary: true }),
        h('label.check', install, 'Also install into firmware/include')),
      h('p.faint', 'Writes calibration.json, the C header and the SET commands to calibration/exports/. Installing overwrites firmware/include/calibration_generated.h so the next build boots calibrated.'),
      exported);

    // ------------------------------------------------------------ past runs
    const runsBody = h('div');
    const summary = h('div');
    const runsPanel = h('section.panel.stack',
      h('div.panel-head', h('h3', 'Past runs'), h('span.sub', 'calibration/runs/'), h('span.spacer'), button('Refresh', () => actions.listRuns(), { cls: '.sm' })),
      runsBody, summary);

    // ------------------------------------------------------------ activity
    const log = h('div.log', { role: 'log', 'aria-live': 'off' });
    const logPanel = h('section.panel', h('div.panel-head', h('h3', 'Activity'), h('span.sub', 'Commands, board replies and server messages')), log);

    root.append(h('div.page',
      h('header.page-head', h('div', h('h1', 'Runs'), h('p.part', 'Recordings · export · replay · activity')), h('p.intro', 'Record evidence for the report, write the calibration into the firmware, and replay earlier runs through the dashboard.')),
      h('div.cols-2',
        h('section.panel', h('div.panel-head', h('h3', 'Recording')), idleView, recView),
        calPanel),
      runsPanel,
      logPanel));

    // ------------------------------------------------------------ rendering
    function renderSegments(segs) {
      if (!segs?.length) return fill(segTable, h('p.empty', 'Change a setting while recording to compare segments here.'));
      fill(segTable, table(['#', 'Length', 'What changed', 'MSE preview', 'MSE board'],
        [...segs].reverse().map((sg) => [
          String(sg.pset), `${fixed(sg.duration_s, 1)} s`,
          h('span', (sg.changed || []).join(', ') || 'start'),
          fixed(sg.metrics_pc?.mse, 4), fixed(sg.metrics_board?.mse, 4),
        ]), { rightFrom: 3 }));
    }

    function renderParams() {
      const names = store.firmwareParams.filter((n) => isNum(store.params[n]) || isNum(store.board[n]));
      if (!names.length) return fill(paramTable, h('p.empty', 'Connect to see the settings.'));
      fill(paramTable, table(['Setting', 'PC', 'Board'], names.map((n) => {
        const dirty = store.isDirty(n);
        return [h('span', PARAM_LABELS[n] || n, h('span.faint.mono', ` ${n}`)), { v: smart(store.params[n], 6), cls: dirty ? 'warn' : '' }, smart(store.board[n], 6)];
      })));
    }

    function renderRuns() {
      const runs = store.runs;
      if (!runs.length) return fill(runsBody, h('p.empty', 'No recordings yet.'));
      fill(runsBody, table(['Run', 'Started', 'Length', 'Segments', 'Best MSE', ''], runs.slice(0, 30).map((r) => [
        h('span.mono', r.name),
        dateTime(r.started), isNum(r.duration_s) ? duration(r.duration_s) : '–', r.psets ?? '–', fixed(r.best_mse_pc, 4),
        h('span.row', { style: { justifyContent: 'flex-end', gap: 'var(--sp-1)' } },
          r.json && button('Results', () => actions.runSummary(r.name), { cls: '.quiet.sm' }),
          button('Replay', () => { actions.replay(r.name); toast(`Replaying ${r.name}`); }, { cls: '.quiet.sm' })),
      ]), { rightFrom: 2 }));
    }

    store.on('runs', renderRuns);
    store.on('run_summary', (m) => {
      const segs = m.segments || [];
      fill(summary, h('div.stack',
        h('div.row', h('strong', m.name), h('span.spacer'), button('Close', () => fill(summary), { cls: '.quiet.sm' })),
        segs.length ? table(['#', 'Length', 'What changed', 'MSE preview', 'MSE board'], segs.map((sg) => [
          String(sg.pset), `${fixed(sg.duration_s, 1)} s`, (sg.changed || []).join(', ') || 'start',
          fixed(sg.metrics_pc?.mse, 4), fixed(sg.metrics_board?.mse, 4),
        ]), { rightFrom: 3 }) : h('p.empty', 'This run has no segment scores.')));
    });
    store.on('session', (m) => renderSegments(m.segments));
    store.on('exported', (m) => {
      toast(m.installed ? 'Calibration exported and installed into firmware/include.' : 'Calibration exported.');
      fill(exported, h('div.result.ok', h('strong', `Wrote ${m.files.length} file${m.files.length > 1 ? 's' : ''}`),
        h('ul', { style: { margin: 0, paddingLeft: '1.2em' } }, m.files.map((f) => h('li.mono', f)))));
    });

    const appendLog = (e) => {
      log.prepend(h(`div${e.level === 'error' ? '.err' : ''}`, h('time', clock(e.t)), e.msg));
      while (log.childElementCount > 200) log.lastChild.remove();
    };
    store.log.forEach(appendLog);
    store.on('log', appendLog);
    store.on('params', renderParams);
    store.on('hello', () => { renderParams(); renderRuns(); });
    renderParams();
    renderRuns();
    renderSegments(store.session?.segments);

    let last = 0;
    return {
      update(now) {
        if (now - last < 250) return;
        last = now;
        const rec = store.session?.recording;
        idleView.hidden = !!rec;
        recView.hidden = !rec;
        if (rec) {
          setText(elV, duration(store.session.elapsed_s));
          setText(rowsV, (store.session.rows || 0).toLocaleString('en-GB'));
          setText(segV, String(store.session.psets ?? 1));
        }
      },
    };
  },
};
