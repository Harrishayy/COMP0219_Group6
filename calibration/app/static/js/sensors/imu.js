// IMU: the motion sensors on the X-NUCLEO-IKS4A1 shield (the shield's barometer and thermometers are used by
// the Environment tab). No firmware here reads them yet, so this tab is their reference and bus check.

export default {
  id: 'imu',
  title: 'IMU',
  part: 'X-NUCLEO-IKS4A1 · accelerometers, gyro, magnetometer · I²C',
  intro: 'Motion sensors on the shield. Not used by the airspeed estimate; useful for spotting rig vibration or a loose mount.',

  status: () => ({ tone: 'idle', text: 'Not in this build' }),
  available: () => ({
    live: false,
    title: 'No firmware reads the IMU yet',
    detail: 'The shield\'s motion sensors sit on the same I²C bus but nothing samples them. Run the I²C scan to confirm they answer at their addresses.',
    commands: ['cd firmware && pio run -e i2c_scan -t upload && pio device monitor -b 115200'],
  }),
  readouts: [],
  views: [],
  steps: [],
  checks: () => [],

  info: {
    summary: 'The IKS4A1 carries two 6-axis IMUs, a low-power accelerometer and a magnetometer. An accelerometer on the plate mount would show the plate\'s ringing frequency (useful when choosing the Butterworth cutoff) and whether the fan shakes the rig.',
    brief: 'X-NUCLEO-IKS4A1 (IMU + environmental: accel/gyro/mag + baro).',
    source: 'Project brief · docs/hardware.md',
    parts: [
      { name: 'LSM6DSV16X', role: '6-axis IMU', connect: 'I²C 0x6B', spec: 'Accel + gyro' },
      { name: 'LSM6DSO16IS', role: '6-axis IMU', connect: 'I²C 0x6A', spec: 'Accel + gyro' },
      { name: 'LIS2DUXS12', role: 'Accelerometer', connect: 'I²C 0x19', spec: 'Low power; MCP9808 must not use 0x19' },
      { name: 'LIS2MDL', role: 'Magnetometer', connect: 'I²C 0x1E', spec: '3-axis' },
    ],
    test: [
      'Keep the shield in Mode 1 (factory jumpers on J4/J5) so the sensors sit on the main bus.',
      'Flash i2c_scan and open a serial monitor.',
      'Expect 0x19, 0x1E, 0x6A and 0x6B alongside the environment sensors.',
      'Do not wire anything to A2–A5, D4 or D6: the shield\'s interrupt lines use them.',
    ],
    commands: [{ label: 'Scan the I²C bus', cmd: 'cd firmware && pio run -e i2c_scan -t upload && pio device monitor -b 115200' }],
  },
};
