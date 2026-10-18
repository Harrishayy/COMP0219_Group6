# Bench dashboard: how to change it

Plain ES modules, no build step, served by `python -m calib serve` from this folder. Reload the page to see a
change. The product brief is `PRODUCT.md` at the repo root; the server protocol is `calibration/docs/SPEC.md`.

## Where things live

| To change                                   | Edit                                                    |
| ------------------------------------------- | ------------------------------------------------------- |
| A colour, font, size, radius or spacing     | `css/tokens.css` only                                   |
| How a component looks                       | `css/app.css` (tokens only, no literal values)          |
| One sensor's numbers, chart, steps, checks  | `js/sensors/<sensor>.js`                                |
| Which tabs exist and their order            | `js/sensors/index.js` (one line each)                   |
| The page layout every sensor shares         | `js/core/sensor-page.js`                                |
| App bar, routing, tab badges, mascot        | `js/main.js`, `js/core/sock.js`                         |
| Connection popover and chip                 | `js/pages/connection.js`                                |
| Recording, export, replay, activity log     | `js/pages/runs.js`                                      |
| Talking to the server, the 200 Hz buffer    | `js/core/store.js`                                      |

## Recipes

**Add a sensor.** Copy `js/sensors/_template.js` to `js/sensors/<name>.js`, fill it in, import it in
`js/sensors/index.js` and add it to the list. The contract is documented at the top of `index.js`. Do not add
CSS or layout code for it: the shared page renders it. If the firmware does not stream the part yet, give it
`available: () => ({ live: false, title, detail, commands })` and an `info` block; it still gets a tab with
its reference and bench test.

**Remove a sensor.** Delete its line in `js/sensors/index.js`.

**Restyle.** Change values in `css/tokens.css`. Keep the token names. The design direction is "Notebook"
(lab notebook on graph paper): one accent (`--accent`) used only for the primary action and the current
item, condensed caps display face for structure, monospace for every reading, 1 px rules, 4 px corners.

**Add a component.** Only when no existing one fits. Add its rules to `css/app.css` using tokens, give it a
short class name, and add a row to the catalogue below.

## Rules

- Tokens only: no hex, rgb or px font sizes outside `css/tokens.css`.
- One primary button per step or panel. Status is always a word, never colour alone.
- Copy is plain and in the team's terms (tare, F₀, Cd·A, pitot). Say what to do next, not what happened.
- Every sensor works on the open bench and in the tunnel. A step that needs airflow or the pitot returns
  `{ tone: 'skip' }` when they are missing; it never blocks.
- The board is the truth: show what the board runs and whether the PC differs.

## Component catalogue

| Component       | Class                                   | States                                   |
| --------------- | --------------------------------------- | ---------------------------------------- |
| Button          | `.btn` `.primary` `.quiet` `.danger` `.sm` | default, hover, disabled, `aria-busy` |
| Status pill     | `.pill` + `.ok` `.warn` `.bad`          | always a dot and a word                  |
| Connection chip | `.health` `.dots`                       | dots: board, pitot, environment          |
| Tab             | `.tab` `.c` `.off`                      | current, progress, complete, blocked, not in build |
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
| Toast           | `.toast`                                | results and board errors                 |

## Check a change

```bash
cd calibration && ../.venv/bin/python -m pytest -q          # server, fits, protocol
../.venv/bin/python -m calib serve --sim --http-port 8799   # simulator on a spare port
```

Port 8765 may already be serving the real board; use a spare port for the simulator. In the simulator,
Connection › Rig switches between the tunnel and the bench (no fan, no pitot).
