# Bench dashboard: how to change it

Plain ES modules, no build step, served by `python -m calib serve` from this folder. Reload the page to see a
change. The product brief is `PRODUCT.md` at the repo root; the server protocol is `calibration/docs/SPEC.md`.

## Where things live

| To change                                   | Edit                                                    |
| ------------------------------------------- | ------------------------------------------------------- |
| A colour, font, size, radius or spacing     | `css/tokens.css` only                                   |
| How a component looks                       | `css/app.css` (tokens only, no literal values)          |
| One sensor's numbers, chart, steps, checks  | `js/sensors/<sensor>.js`                                |
| Which sensors exist and their order         | `js/sensors/index.js` (one line each)                   |
| The calibration page every sensor shares    | `js/core/sensor-page.js`                                |
| The one-screen dashboard (a tile per sensor) | `js/core/dashboard.js`; what a tile shows: the module's `tile` |
| The readings strip under the app bar        | `overviewStrip` in `js/core/dashboard.js`; which readouts: `OVERVIEW` in `js/main.js` |
| App bar, routing, keys 1–6, record button   | `js/main.js`, `js/core/sock.js`                         |
| Wind Tunnel Logger runs (Tunnel runs panel) | `runsPanel` in `js/sensors/wind-speed.js`; data from `calib/tunnel.py` |
| Connection popover and chip                 | `js/pages/connection.js`                                |
| Recording, export, replay, activity log     | `js/pages/runs.js`                                      |
| Results: a saved tunnel run, any moment of it | `js/pages/results.js`; data from `calib/tunnel.py` (`load`, `window`) |
| Talking to the server, the 200 Hz buffer    | `js/core/store.js`                                      |
| Board banner, browser-tab icon and title    | `js/main.js`, `js/core/sock.js` (`favicon`)             |
| A sensor's breadboard wiring diagram        | `js/wiring/parts/<sensor>.js` (data), `js/wiring/render.js` |
| A sensor's datasheet tab                    | the module's `datasheet`; PDF in `docs/datasheets/`     |

## Recipes

**Add a sensor.** Copy `js/sensors/_template.js` to `js/sensors/<name>.js`, fill it in, import it in
`js/sensors/index.js` and add it to the list. The contract is documented at the top of `index.js`. Do not add
CSS or layout code for it: it gets a tile on the dashboard, its own calibration page and a top-bar entry (with the
next number key). Choose what its tile shows with `tile: { readouts, view }`. If the firmware does not stream the
part yet, give it `available: () => ({ live: false, title, detail, commands })` and an `info` block; its page shows
its reference and bench test. To show one of its readouts in the strip under the app bar, add it to `OVERVIEW` in
`js/main.js` (by its label).

**The dashboard fits one screen.** At 1100 × 680 and up, `#dashboard` fills the viewport and never scrolls
(`body.fit`): tiles share the height and their charts take what is left. Keep a tile to about four readouts and one
chart; the full detail belongs on the sensor's page. Below that size the tiles stack and the page scrolls.

**Remove a sensor.** Delete its line in `js/sensors/index.js` (and any `OVERVIEW` entry for it).

**Add wiring or a datasheet.** Give the module `wiring` (a spec from `js/wiring/parts/`), optionally
`wiringLive(store)`, and `datasheet`; the page grows Bench / Wiring / Datasheet sub-tabs (`#<id>/<sub>`). The
`wiring-diagram` skill in `.claude/skills/` has the whole procedure; `pnpm check:wiring` must pass.

**No fake data.** With no live stream, the shared page shows dashes and marks charts as old readings; the
banner says why (`store.boardState`). Do not show a held or simulated value as if it were live.

**Restyle.** Change values in `css/tokens.css`. Keep the token names. The design direction is "Notebook"
(lab notebook on graph paper): one accent (`--accent`) used only for the primary action and the current
item, condensed caps display face for structure, monospace for every reading, 1 px rules, 4 px corners.

**Add a component.** Only when no existing one fits. Add its rules to `css/app.css` using tokens, give it a
short class name, and add a row to the catalogue below.

## Rules

- Tokens only: no hex, rgb or px font sizes outside `css/tokens.css`.
- One primary button per step or panel. Status is always a word, never colour alone.
- Copy is plain and in the team's terms (tare, F₀, Cd·A, pitot). Say what to do next, not what happened.
- Every sensor works on the open bench and in the tunnel. A step that needs airflow or the ground truth returns
  `{ tone: 'skip' }` when they are missing; it never blocks.
- The board is the truth: show what the board runs and whether the PC differs.

## Component catalogue

| Component       | Class                                   | States                                   |
| --------------- | --------------------------------------- | ---------------------------------------- |
| Button          | `.btn` `.primary` `.quiet` `.danger` `.sm` | default, hover, disabled, `aria-busy` |
| Status pill     | `.pill` + `.ok` `.warn` `.bad`          | always a dot and a word                  |
| Connection chip | `.health` `.dots`                       | dots: board, pitot, environment          |
| Page link       | `.tab` `.c` `.off` `kbd`                | `aria-current`, progress, complete, blocked, not in build; key hint |
| Record button   | `.btn.sm.rec-btn` `.t`                  | `aria-pressed` while recording, with elapsed time |
| Readings strip  | `.overview` `.ov`                       | under the app bar on sensor pages and Runs; jumps to the page |
| Tile            | `.cockpit` `.tile` `.wide` `.tile-head` `.tile-readouts` `.tile-chart` `.tile-foot` | `.off` (not in build); chart `.stale` |
| Tile footer     | `.tile-next` `.tile-check` `.tile-run`  | next step tone; worst check; last tunnel run |
| Stat strip      | `.readouts` `.readout`                  | normal, `.ok` `.warn` `.bad`             |
| Panel           | `.panel` `.panel-head` `.flush`         | with head; flush for lists               |
| Step            | `.step` `.step-num` `.step-body`        | todo, open, done, blocked, skip          |
| Recommendation  | `.check-item` `.bad` `.warn` `.info`    | Fix, Check, Note; optional action        |
| Result          | `.result` `.ok` `.warn`                 | after an action                          |
| Coverage        | `.bands` span `.part` `.full`           | per 1 m/s band                           |
| Segmented       | `.segmented`                            | `aria-pressed`                           |
| Setting         | `.setting` `.dirty`                     | PC differs from board                    |
| Reference       | `.ref` `.cmd` `.brief`                  | from a module's `info`                   |
| Notice          | `.notice`                               | part not in this build                   |
| Table           | `.table` `.r` `.ok` `.warn` `.bad`      |                                          |
| Chart sizes     | `.chart` `.tall` `.short`               | TimeChart `range`, `cursor`, `shade`, `onPick` for recorded runs |
| Time slider     | `.scrub`                                | under a timeline (Results)               |
| File path       | `.path`                                 | breaks anywhere in a table cell          |
| Toast           | `.toast`                                | results and board errors                 |
| Banner          | `.banner` `.bad` `.warn`                | no board, silent board, simulator, replay |
| Sub-tabs        | `.segmented.subtabs`                    | Bench, Wiring, Datasheet; `aria-selected` |
| Wiring          | `.wiring` `.wiring-art` `.wiring-steps` | step `cur`, `done`; drawing items `future`, `dim` |
| Datasheet       | `.pdf-wrap` `.pdf-jumps` `.pdf`         | jump `aria-pressed`                      |

## Check a change

```bash
cd calibration && ../.venv/bin/python -m pytest -q          # server, fits, protocol
../.venv/bin/python -m calib serve --sim --http-port 8799   # simulator on a spare port
```

Port 8765 may already be serving the real board; use a spare port for the simulator. In the simulator,
Connection › Rig switches between the tunnel and the bench (no fan, no pitot).
