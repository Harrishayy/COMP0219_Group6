// Encoder: the Bourns EM14 optical incremental encoder from the brief's kit, counted in hardware by TIM2.
// The team's drag-plate build does not read it, and the calibration server only runs drag-plate builds, so
// this tab is the part's reference and bench test (firmware/src/apps/encoder_test.cpp).

export default {
  id: 'encoder',
  title: 'Encoder',
  part: 'Bourns EM14 optical encoder · TIM2 on A0/A1 · x4 decoding',
  intro: 'Counts shaft rotation for a cup or vane rotor. Bench-test it with its own firmware and a serial monitor.',

  status: () => ({ tone: 'idle', text: 'Not in this build' }),
  available: () => ({
    live: false,
    title: 'The drag-plate firmware does not read the encoder',
    detail: 'It was for the cup anemometer, which the team dropped. To check the part, flash the encoder test, open a serial monitor and turn the shaft one full revolution: the count should change by kEncoderCountsPerRev (96 as set). Flash airspeed_drag_plate again afterwards.',
    commands: ['cd firmware && pio run -e encoder_test -t upload && pio device monitor -b 115200', 'cd firmware && pio run -e airspeed_drag_plate -t upload'],
  }),
  readouts: [],
  views: [],
  steps: [],
  checks: () => [],

  info: {
    summary: 'A quadrature encoder: two optical channels 90° apart, so the timer counts every edge of both (x4) and knows the direction. TIM2 is 32 bits, so the count never wraps in a run. Speed is the count rate over a window (rotor_window_ms) divided by counts per revolution.',
    brief: 'Bourns EM14 optical incremental encoder (panel-mount, shafted). Guard any rotating parts.',
    source: 'Project brief · docs/hardware.md · firmware/include/board_config.h',
    parts: [
      { name: 'Bourns EM14', role: 'Optical incremental encoder', connect: 'A → A0 (PA0), B → A1 (PA1)', spec: 'TIM2 CH1/CH2, AF1, x4' },
      { name: 'Supply', role: 'Encoder power', connect: '5 V', spec: 'Check outputs stay within the F411\'s input limits; divide or level-shift if they swing to 5 V' },
      { name: 'kEncoderCountsPerRev', role: 'Firmware constant', connect: 'board_config.h', spec: '4 × cycles/rev of your part (4 × 24 = 96 now)' },
    ],
    test: [
      'Check the output voltage with a meter before wiring to PA0/PA1.',
      'Flash encoder_test and open a serial monitor at 115200 baud.',
      'Turn the shaft exactly one revolution: the count changes by kEncoderCountsPerRev.',
      'If it counts backwards, swap A and B or pass invert=true.',
      'Spin steadily: rev/s should be smooth, not jumping between two values.',
    ],
    commands: [
      { label: 'Flash the encoder test and watch the counts', cmd: 'cd firmware && pio run -e encoder_test -t upload && pio device monitor -b 115200' },
      { label: 'Cup + encoder competition build', cmd: 'cd firmware && pio run -e airspeed -t upload' },
    ],
  },
};
