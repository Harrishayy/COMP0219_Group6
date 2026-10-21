// Nucleo-F411RE ST morpho headers (ST UM1724, NUCLEO-F411RE pin table), top view with the ST-LINK USB at the
// top. The morpho pins stay reachable with the X-NUCLEO-IKS4A1 on the Arduino headers, so every wiring
// diagram uses them. Pure data: imported by the dashboard and by scripts/check-wiring.mjs.
//
// Pin numbers: odd pins in the left column, even in the right, pin 1 at the top. A pin's `signal` is its net:
// pins with the same signal are the same copper (every GND is one net). `volts` marks a supply.

const SUPPLY = { '+3V3': 3.3, VDD: 3.3, AVDD: 3.3, IOREF: 3.3, '+5V': 5, U5V: 5, E5V: 5, VIN: 7, VBAT: 3.3, GND: 0, AGND: 0 };
const NET_ALIAS = { AGND: 'GND' };
const ALT = {
  PB8: 'D15 · I²C1 SCL', PB9: 'D14 · I²C1 SDA', PC2: 'ADC1_IN12 · TMP36', PC3: 'ADC1_IN13 · LM35',
  PA0: 'A0 · encoder A', PA1: 'A1 · encoder B', PA2: 'USART2 TX (ST-LINK)', PA3: 'USART2 RX (ST-LINK)',
  PA5: 'D13 · LD2', PA8: 'D7 · Hall', PA4: 'A2 · IKS4A1 interrupt', PB0: 'A3 · IKS4A1 interrupt',
  PC1: 'A4 · IKS4A1 interrupt', PC0: 'A5 · IKS4A1 interrupt', PB5: 'D4 · IKS4A1 interrupt', PB10: 'D6 · IKS4A1 interrupt',
  PA13: 'SWDIO (ST-LINK)', PA14: 'SWCLK (ST-LINK)',
};

const CN7 = ['PC10', 'PC11', 'PC12', 'PD2', 'VDD', 'E5V', 'BOOT0', 'GND', 'NC', 'NC', 'NC', 'IOREF', 'PA13', 'RESET',
  'PA14', '+3V3', 'PA15', '+5V', 'GND', 'GND', 'PB7', 'GND', 'PC13', 'VIN', 'PC14', 'NC', 'PC15', 'PA0', 'PH0', 'PA1',
  'PH1', 'PA4', 'VBAT', 'PB0', 'PC2', 'PC1', 'PC3', 'PC0'];
const CN10 = ['PC9', 'PC8', 'PB8', 'PC6', 'PB9', 'PC5', 'AVDD', 'U5V', 'GND', 'NC', 'PA5', 'PA12', 'PA6', 'PA11', 'PA7',
  'PB12', 'PB6', 'NC', 'PC7', 'GND', 'PA9', 'PB2', 'PA8', 'PB1', 'PB10', 'PB15', 'PB4', 'PB14', 'PB5', 'PB13', 'PB3',
  'AGND', 'PA10', 'PC4', 'PA2', 'NC', 'PA3', 'NC'];

const header = (id, side, names) => ({
  id, side, label: `${id} (${side} morpho)`,
  pins: names.map((name, i) => ({
    n: i + 1, name, signal: NET_ALIAS[name] || name, volts: SUPPLY[name], alt: ALT[name] || '',
  })),
});

export const NUCLEO = {
  id: 'nucleo-f411re',
  title: 'Nucleo-F411RE',
  source: 'ST UM1724 · NUCLEO-F411RE morpho pin table',
  headers: { CN7: header('CN7', 'left', CN7), CN10: header('CN10', 'right', CN10) },
  shield: 'X-NUCLEO-IKS4A1 on the Arduino headers',
};

/** 'CN7-18' -> { header, pin } or null. */
export function headerPin(ref, board = NUCLEO) {
  const m = /^(CN\d+)-(\d+)$/.exec(ref || '');
  const hd = m && board.headers[m[1]];
  const pin = hd && hd.pins[Number(m[2]) - 1];
  return pin ? { header: hd, pin } : null;
}

/** Human label for a header pin: 'CN7 pin 18 · +5V'. */
export function pinLabel(ref, board = NUCLEO) {
  const p = headerPin(ref, board);
  return p ? `${p.header.id} pin ${p.pin.n} · ${p.pin.name}${p.pin.alt ? ` (${p.pin.alt})` : ''}` : ref;
}
