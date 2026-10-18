// Hall: the Allegro Hall-effect switch and the 3 mm neodymium magnets from the brief's kit, timing a rotor.
// The team's drag-plate build does not read it, and the calibration server only runs drag-plate builds, so
// this tab is the part's reference and bench test (firmware/src/apps/hall_test.cpp).

export default {
  id: 'hall',
  title: 'Hall',
  part: 'Allegro Hall switch + 3 mm magnets · D7 (PA8) · EXTI',
  intro: 'Times magnets passing on a rotor. Bench-test it with its own firmware and a serial monitor.',

  status: () => ({ tone: 'idle', text: 'Not in this build' }),
  available: () => ({
    live: false,
    title: 'The drag-plate firmware does not read the Hall switch',
    detail: 'It was for the cup anemometer, which the team dropped. To check the part, flash the Hall test, open a serial monitor and turn the rotor one revolution: edges should rise by kHallPulsesPerRev (one per magnet). Flash airspeed_drag_plate again afterwards.',
    commands: ['cd firmware && pio run -e hall_test -t upload && pio device monitor -b 115200', 'cd firmware && pio run -e airspeed_drag_plate -t upload'],
  }),
  readouts: [],
  views: [],
  steps: [],
  checks: () => [],

  info: {
    summary: 'An Allegro switch or latch pulls its open-drain output low while a magnet is near. The firmware timestamps each edge in an interrupt and averages over the last full revolution, so uneven magnet spacing cancels. No edge for 0.5 s means the rotor has stopped.',
    brief: 'Eclipse neodymium magnets (3 mm discs) and an Allegro through-hole Hall-effect sensor. Secure magnets with epoxy or heat-shrink and guard rotating parts.',
    source: 'Project brief · docs/hardware.md · firmware/lib/drivers/src/hall_sensor.h',
    parts: [
      { name: 'Allegro Hall switch', role: 'Digital switch or latch', connect: 'Out → D7 (PA8), internal pull-up', spec: 'Open-drain; EXTI on each edge' },
      { name: 'Neodymium magnets', role: 'Targets on the rotor', connect: 'Rotor rim', spec: '3 mm discs; epoxy or heat-shrink' },
      { name: 'kHallPulsesPerRev', role: 'Firmware constant', connect: 'board_config.h', spec: 'Magnets per revolution (1 now)' },
    ],
    test: [
      'Power the switch from 3V3 and wire its output to D7.',
      'Flash hall_test and open a serial monitor at 115200 baud.',
      'Pass one magnet slowly past the sensor: edges goes up by one.',
      'Turn the rotor one revolution: edges rise by kHallPulsesPerRev.',
      'If a latch is used, alternate the magnet poles so it toggles.',
    ],
    commands: [
      { label: 'Flash the Hall test and watch the edges', cmd: 'cd firmware && pio run -e hall_test -t upload && pio device monitor -b 115200' },
      { label: 'Cup + Hall competition build', cmd: 'cd firmware && pio run -e airspeed_cup_hall -t upload' },
    ],
  },
};
