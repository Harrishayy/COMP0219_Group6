# Product

## Register

product

## Users
The project team (UCL COMP0219 students) at a bench beside the mini wind tunnel, in a bright lab, laptop at
arm's length. Hands are often busy: hanging masses on the load cell, switching the fan, re-seating a sensor.
They glance between the rig and the screen and need to read a number or a verdict in one look.

## Product Purpose
The calibration bench (`calibration/`, served by `python -m calib serve`) measures each sensor on the
Nucleo on its own, walks through calibrating it, says what is wrong and what to change, and writes the
result back to the board and into the firmware build. Success: every sensor is calibrated and verified
against the pitot without opening a terminal, and the exported calibration is what the board runs.

## Brand Personality
Calm, exact, plain-spoken. A good bench instrument: numbers first, no decoration, every message says what
to do next in the team's own vocabulary (tare, F₀, Cd·A, pitot).

## Anti-references
- The previous one-screen dashboard: eight score tiles, four analysis charts and six tabs competing at once.
- Neon-on-black "mission control" dashboards and gradient SaaS analytics templates.
- Hidden state: settings that silently differ between the PC preview and the board.

## Design Principles
1. One sensor at a time. Each tab measures, calibrates and diagnoses one sensor; nothing from another sensor
   competes for attention.
2. Same shape for every sensor. Live readings, then calibration steps, then recommendations, in the same
   places, so a new sensor is one module and the team already knows how to use it.
3. Verdicts over raw data. Say "Noisy: 0.8 g σ, check the cable shield" before showing a chart.
4. The board is the truth. Always show what the board is running and whether the PC differs.
5. Readable at arm's length. Large numerals, generous spacing, nothing that needs a squint.

## Accessibility & Inclusion
WCAG 2.1 AA contrast. Status never relies on colour alone (always a word). Full keyboard use of tabs and
controls. Respects reduced motion.
