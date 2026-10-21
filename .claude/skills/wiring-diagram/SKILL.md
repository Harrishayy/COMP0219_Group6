---
name: wiring-diagram
description: Add or change a sensor's breadboard wiring diagram (Wiring sub-tab) and datasheet viewer (Datasheet sub-tab) on the calibration dashboard. Use when the user wants to see how to wire a part to the Nucleo, wants a wiring picture, breadboard layout, pinout or "which wire goes where" for a sensor, wants a datasheet shown in the UI, or adds a new sensor that needs wiring. Also use when moving a sensor to a different Nucleo pin.
---

# Wiring diagram and datasheet for a sensor tab

The dashboard draws each part on a half-size breadboard beside the Nucleo-F411RE's morpho headers, lists the
build as numbered steps that play one at a time, and traces every leg through the breadboard as a circuit to
prove it reaches the right signal. The same check runs in `pnpm test`. A datasheet tab shows the PDF with jumps
to the useful pages.

Read these first, they are the contract:

- `calibration/app/static/js/wiring/parts/lm35.js` and `mcp9808.js`: complete specs to copy.
- `calibration/app/static/js/wiring/breadboard.js`: hole addresses, nets, `checkWiring`.
- `calibration/app/static/js/wiring/nucleo.js`: the CN7/CN10 morpho pin map (signals and supply volts).
- `firmware/include/board_config.h` and `docs/hardware.md`: the pins the firmware actually uses.

## 1. Get the datasheet and read the pinout from it, not from memory

```bash
mkdir -p docs/datasheets
curl -sSL -A "Mozilla/5.0" -o docs/datasheets/<part>.pdf "<manufacturer URL>"
file docs/datasheets/<part>.pdf               # must say "PDF document"; an HTML error page is a failed download
pdfinfo docs/datasheets/<part>.pdf | rg Pages
pdftotext -layout docs/datasheets/<part>.pdf - | rg -n -i "pin|supply|VDD|address|bottom view|top view"
```

Prefer the manufacturer's own PDF (TI, Microchip, ST, Analog Devices). Some sites (st.com) block scripted
downloads: then ask the user to save the PDF into `docs/datasheets/`. For section page numbers:

```bash
for i in $(seq 1 N); do pdftotext -f $i -l $i -layout docs/datasheets/<part>.pdf - | rg -o -m2 "^\s*[0-9]+(\.[0-9]+)* [A-Z][A-Za-z ,/()-]{4,60}$" | sed "s/^ */p$i: /"; done
```

**Package views mirror.** A TO-92 or SOT pinout drawn as "Bottom View" is mirrored when you look down on the
breadboard. Work out the order from the top: for TO-92, "flat face towards you, legs down" is the safe phrase;
on the breadboard that means the flat face points at the viewer's side of the part. State the orientation in the
part's `place` text and draw it the same way (the `to92` drawing puts the flat face towards the centre channel).
Breakouts: follow the silkscreen of the board the team has; say which board the order is for.

## 2. Choose pins the firmware reads

- A pin must already be in `board_config.h` (`kPin…`, `kAddr…`) or be added there and in `docs/hardware.md`.
  Never pick a pin only in the diagram.
- Avoid pins the IKS4A1 uses (A2–A5, D4, D6) and the ST-LINK/serial pins (PA2, PA3, PA13, PA14).
- Wire to the **morpho** pins (CN7, CN10): they stay reachable with the shield on. `nucleo.js` lists them;
  analog inputs free on CN7: PC2 (TMP36), PC3 (LM35), PC0/PC1 are IKS4A1 interrupts.
- I²C parts go on I²C1: SDA PB9 (CN10-5), SCL PB8 (CN10-3). Check the address against the I²C table in
  `docs/hardware.md` for clashes.
- Supply: use the part's datasheet range. 3V3 is CN7-16, 5V is CN7-18, GND is CN7-20/22 or CN10-9/20. Any
  signal that goes into the STM32 must stay under 3.3 V (analog pins are not 5 V tolerant).

## 3. Write the spec: `calibration/app/static/js/wiring/parts/<part>.js`

```js
export default {
  id: '<part>', title: '<Part> on a half-size breadboard',
  intro: 'One or two sentences: how many wires, which supply and why.',
  firmware: [{ file: 'firmware/include/board_config.h', name: 'kPin<Part>', value: 'PC3' }],  // checked by pnpm check:wiring
  parts: [
    { id: 'u1', kind: 'to92' | 'breakout' | 'resistor' | 'capacitor', label: 'LM35', optional?: true,
      place: 'Where it goes and which way round, in the team\'s words.',
      pins: [{ name: '+Vs', at: 'e10', net: '+5V', supply: [4, 30] },   // net: the board signal it must reach
             { name: 'Vout', at: 'e11', net: 'PC3' },                   // null: must stay unconnected
             { name: 'GND', at: 'e12', net: 'GND' }] },
  ],
  wires: [  // in build order; colours: red supply, black ground, yellow/blue/green/orange/white signals
    { color: 'red', from: 'CN7-18', to: 'top+3', say: 'What this wire does.' },
  ],
  notes: ['Safety and placement: what to check before plugging in USB.'],
  verify: ['What a multimeter should read, and where.'],
};
```

Holes: `a1`–`j30` (a–e of a column are one strip, f–j another), rails `top+N`, `top-N`, `bot+N`, `bot-N`.
Header pins: `CN7-18`. One leg or wire end per hole. Layout tips that keep the drawing readable:

- Parts in row e (top half) or row h (breakouts, body hanging over the bottom rails); wires into rows a or f.
- Leave a free column either side of a part; put passives where their label has room to the right.
- Power through the top rails with short jumpers when more than one part shares a supply.

Add the spec to `js/wiring/parts/index.js`, then:

```bash
pnpm check:wiring      # every leg reaches its net, no shorts, supplies in range, board_config.h agrees
```

Fix the spec until it passes. Then break it on purpose once (a 3V3 supply, a wire on the wrong rail) and check
the error is caught, so you know the check covers your part.

## 4. Hook it into the sensor tab

In `calibration/app/static/js/sensors/<part>.js`:

```js
import wiring from '../wiring/parts/<part>.js';
// …in the module:
wiring,
wiringLive(s) { /* { tone: 'ok' | 'warn' | 'bad' | 'idle', label?, text }: does the board see the part now? */ },
datasheet: { file: '<part>.pdf', title: '<Part> datasheet', source: 'Maker doc number', pages: [{ page: 3, label: 'Pinout' }] },
```

The page grows Bench / Wiring / Datasheet sub-tabs (`#<id>/wiring`). A first step that points at the wiring can
call `ctx.show('wiring')`. With no live data `wiringLive` must say so (`tone: 'idle'`), never guess.

## 5. See it

```bash
pnpm dev:sim                         # or pnpm dev with the board; open #<id>/wiring
```

Screenshot the Wiring tab (Playwright, `.wiring-art` element) and look for labels under wires, parts covering
holes that wires use, and the leg order against the datasheet's pinout page. Press Play and step through.
Open the Datasheet tab in a real browser: headless Chromium has no PDF viewer and shows a blank frame.

## Done means

- `pnpm test` passes (Python tests and the wiring check).
- The pin is in `board_config.h` and `docs/hardware.md`, and the firmware builds:
  `cd firmware && pio run -e airspeed_drag_plate`.
- You told the user which facts came from the datasheet and which (if any) you could not check, such as a
  breakout's pin order or a morpho pin number, so they can look at the silkscreen before powering up.
