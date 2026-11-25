// MCP9808 breakout on the breadboard, on the shared I²C1 bus. Checked by js/wiring/breadboard.js (in the
// dashboard and by `pnpm check:wiring`). Pin order is the Adafruit #1782 breakout: VDD, GND, SCL, SDA,
// Alert, A2, A1, A0. Other breakouts order them differently: follow the silkscreen, keep the signals.
export default {
  id: 'mcp9808',
  title: 'MCP9808 breakout on the I²C bus',
  intro: 'Four wires: 3V3, GND and the two I²C lines, which it shares with the IKS4A1 and the NAU7802. Leave A0–A2 open: the breakout pulls them low, which gives address 0x18.',
  firmware: [{ file: 'firmware/include/board_config.h', name: 'kAddrMcp9808', value: '0x18' }],
  parts: [
    {
      id: 'u1', kind: 'breakout', label: 'MCP9808', place: 'Push the breakout\'s header into h12–h19, VDD in h12, with the board hanging over the bottom rails.',
      pins: [
        { name: 'VDD', at: 'h12', net: '+3V3', supply: [2.7, 5.5] },
        { name: 'GND', at: 'h13', net: 'GND' },
        { name: 'SCL', at: 'h14', net: 'PB8' },
        { name: 'SDA', at: 'h15', net: 'PB9' },
        { name: 'Alert', at: 'h16', net: null },
        { name: 'A2', at: 'h17', net: null },
        { name: 'A1', at: 'h18', net: null },
        { name: 'A0', at: 'h19', net: null },
      ],
    },
  ],
  wires: [
    { color: 'red', from: 'CN7-16', to: 'f12', say: '3V3 to VDD (column 12). 3.3 V keeps the I²C lines at the STM32\'s level.' },
    { color: 'black', from: 'CN7-22', to: 'f13', say: 'GND to GND (column 13).' },
    { color: 'yellow', from: 'CN10-3', to: 'f14', say: 'PB8 (D15, I²C1 SCL) to SCL (column 14).' },
    { color: 'blue', from: 'CN10-5', to: 'f15', say: 'PB9 (D14, I²C1 SDA) to SDA (column 15).' },
  ],
  notes: [
    'No pull-up resistors to add: the breakout, the IKS4A1 and the Qwiic boards already pull SDA and SCL up to 3.3 V.',
    'With the STEMMA QT / Qwiic version of the breakout, a Qwiic cable from the NAU7802\'s spare port makes all four connections.',
    'Do not tie A0 high: 0x19 clashes with the IKS4A1\'s LIS2DUXS12 accelerometer.',
    'Mount it in the airflow, away from the Nucleo: it resolves 0.0625 °C, so a warm board nearby shows up.',
  ],
  verify: [
    'Flash i2c_scan: 0x18 answers alongside 0x2A, 0x38, 0x44 and 0x5D.',
    'With a multimeter: 3.3 V between columns 12 and 13.',
  ],
};
