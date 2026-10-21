// LM35 on the breadboard, wired to the Nucleo's CN7 morpho header. Checked by js/wiring/breadboard.js
// (in the dashboard and by `pnpm check:wiring`): every leg must reach the signal in its `net`.
//
// TO-92, flat face towards you, legs down: +Vs, Vout, GND from left to right (datasheet §5, p. 3, drawn
// there from below). Seen from above that order only holds with the flat face towards you, so on the
// breadboard the flat face points at the centre channel (row f) with +Vs in the lower-numbered column.
export default {
  id: 'lm35',
  title: 'LM35 on a half-size breadboard',
  intro: 'Three wires from CN7 and a pull-down. The LM35 needs 4–30 V, so it runs from the 5V pin, not 3V3; its output is only 10 mV/°C, safely inside the 3.3 V ADC range.',
  firmware: [{ file: 'firmware/include/board_config.h', name: 'kPinLm35', value: 'PC3' }],
  parts: [
    {
      id: 'u1', kind: 'to92', label: 'LM35', place: 'Push the LM35 into e10, e11, e12 with the flat face towards the centre channel and you. Looking at the flat face, the left leg (+Vs) goes in e10.',
      pins: [
        { name: '+Vs', at: 'e10', net: '+5V', supply: [4, 30] },
        { name: 'Vout', at: 'e11', net: 'PC3' },
        { name: 'GND', at: 'e12', net: 'GND' },
      ],
    },
    {
      id: 'r1', kind: 'resistor', label: '100 kΩ', place: 'Put the 100 kΩ resistor from b11 (Vout) into the top − rail. It pulls an unplugged LM35 to 0 V, so the board reports "not connected" instead of reading noise.',
      pins: [{ name: '1', at: 'b11', net: 'PC3' }, { name: '2', at: 'top-11', net: 'GND' }],
    },
    {
      id: 'c1', kind: 'capacitor', label: '0.1 µF', optional: true, place: 'Optional: a 0.1 µF ceramic across the top rails next to the LM35 (top + 15 to top − 15) steadies the reading if the wires are long.',
      pins: [{ name: '1', at: 'top+15', net: '+5V' }, { name: '2', at: 'top-15', net: 'GND' }],
    },
  ],
  wires: [
    { color: 'red', from: 'CN7-18', to: 'top+3', say: '5V to the top + rail.' },
    { color: 'black', from: 'CN7-20', to: 'top-3', say: 'GND to the top − rail.' },
    { color: 'red', from: 'top+10', to: 'a10', say: 'Short jumper: + rail to column 10 (+Vs).' },
    { color: 'black', from: 'top-12', to: 'a12', say: 'Short jumper: − rail to column 12 (GND).' },
    { color: 'yellow', from: 'CN7-37', to: 'a11', say: 'PC3 to column 11 (Vout). This is the ADC input.' },
  ],
  notes: [
    'Check before you plug in USB: PC3 is not 5 V tolerant in analog mode. If its wire lands on the + rail by mistake, the board can be damaged.',
    'Reversed (flat face towards row a), the LM35 sees its supply backwards: it can heat up and the reading is nonsense. If it feels warm, unplug the USB and turn it round.',
    'For an air temperature, put the LM35 in the airflow on longer leads, away from the Nucleo regulator.',
  ],
  verify: [
    'With a multimeter: about 5.0 V between columns 10 and 12.',
    'About 0.20–0.30 V between columns 11 and 12 at room temperature (10 mV per °C).',
  ],
};
