# Hardware and wiring

All pin and address choices live in [`firmware/include/board_config.h`](../firmware/include/board_config.h).
Change them there, not in the apps.

## Stack

```
 X-NUCLEO-IKS4A1   (Arduino headers)
 Nucleo-F411RE     (USB = power + ST-LINK programmer + virtual COM port)
```

## Pin map

| Signal                         | Nucleo pin             | Notes                                                        |
| ------------------------------ | ---------------------- | ------------------------------------------------------------ |
| I2C1 SDA / SCL                 | D14 (PB9) / D15 (PB8)  | IKS4A1 + load-cell amplifier (Weight I2C or NAU7802) + MCP9808, 400 kHz |
| Encoder A / B (EM14)           | A0 (PA0) / A1 (PA1)    | TIM2 CH1/CH2, AF1, hardware x4 decoding                      |
| TMP36 Vout                     | PC2 (CN7 pin 35)       | ADC1_IN12, VDDA measured via VREFINT                         |
| LM35 Vout                      | PC3 (CN7 pin 37)       | ADC1_IN13; LM35 powered from 5V (CN7 pin 18), 100 kΩ Vout to GND |
| Hall switch output             | D7 (PA8)               | EXTI, internal pull-up (Allegro switches are open-drain)    |
| Serial to PC                   | PA2 / PA3 (USART2)     | ST-LINK virtual COM port, no wiring needed                   |
| LED                            | LD2 (PA5)              | Heartbeat blink while streaming; on while taring, 10 Hz flicker when a tare is refused |
| USER button (blue, B1)         | PC13                   | On the Nucleo, no wiring. Zeroes the drag plate's load cell (fan off) |

**Pins the IKS4A1 already uses** (sensor interrupt lines, from ST's Zephyr shield definition):
A2, A3, A4, A5, D4, D6. Do not wire anything else to them. Most IKS4A1 interrupt outputs are push-pull
and would fight an external signal.

## I2C addresses (7-bit)

| Device                 | Address | Board            |
| ---------------------- | ------- | ---------------- |
| LIS2DUXS12 accel       | 0x19    | IKS4A1 (unused)  |
| LIS2MDL magnetometer   | 0x1E    | IKS4A1 (unused)  |
| Unit Weight I2C (HX711)| 0x26    | M5Stack U177 (load cell, default) |
| NAU7802 (Qwiic Scale)  | 0x2A    | SparkFun Qwiic (load cell, alternative) |
| STTS22H temperature    | 0x38    | IKS4A1           |
| SHT40 humidity/temp    | 0x44    | IKS4A1           |
| LPS22DF pressure       | 0x5D    | IKS4A1           |
| LSM6DSO16IS IMU        | 0x6A    | IKS4A1 (unused)  |
| LSM6DSV16X IMU         | 0x6B    | IKS4A1 (unused)  |
| MCP9808 temperature    | 0x18    | breakout, A2..A0 = 000 (0x19 would clash with the LIS2DUXS12) |

Run `pio run -e i2c_scan -t upload` to confirm. The IKS4A1 must be in its standard mode (Mode 1, the
factory jumper setting on J4/J5; see ST UM3239) so that the sensors sit on the main I2C bus rather than
behind a sensor hub.

## Per-part notes

- **TMP36**: needs 2.7–5.5 V, so power it from 3V3. Keep it off the MCU board and in the airflow for a
  representative air temperature. Output is 10 mV/°C, with 750 mV at 25 °C.
- **MCP9808**: the most accurate thermometer in the kit. Mount it in the free stream, away from the Nucleo.
  VDD from 3V3 (CN7 pin 16), SDA/SCL to PB9/PB8 (CN10 pins 5/3, the same nets as D14/D15), A0–A2 open. The
  firmware looks for it again every 2 s while it is missing, so it can be wired to a running board.
- **LM35**: needs 4–30 V, so power it from 5V (CN7 pin 18), not 3V3. Its output is 10 mV/°C from 0 V, well inside
  the 3.3 V ADC range. Fit a 100 kΩ from Vout to GND: an unplugged LM35 then reads 0 V and is reported missing
  instead of reading a floating pin. PC3 is not 5 V tolerant in analog mode: never wire it to the 5V rail.
  TO-92 with the flat face towards you: +Vs, Vout, GND from left to right.
- **Wiring diagrams**: the dashboard's Wiring tabs draw each part on a breadboard with the morpho pins, which
  stay reachable with the IKS4A1 fitted (jumpers need a female end). `pnpm check:wiring` checks them as circuits
  and against `board_config.h`. Morpho pin numbers are from ST UM1724; check them against the board silkscreen.
- **Load-cell amplifier: Weight I2C (default) or NAU7802.** The firmware uses whichever answers on I²C
  (`LoadCell::begin`, preference `kLoadCellPrefer` in `board_config.h` when both are plugged in), so swapping is a
  re-plug, then tare and scale again: the two give different raw counts. `GET` reports it as `lc_frontend`.
  The M5Stack Unit Weight I2C runs an HX711 behind its own MCU: 10 conversions/s (the 200 Hz stream holds each
  for 20 ticks), raw counts offset by 2^23 (about 8 390 000 with no bridge signal), read from register 0x00; its
  own tare, scale and filters (0x40–0x82) are not used. **Power it from 5V** (Nucleo 5V pin, CN6, or CN7 pin 18), not
  3V3: its HX711 makes the cell's excitation from that supply and gives no bridge signal on 3.3 V. Its SDA/SCL go
  to PB9/PB8, which are 5 V tolerant. Cell wires in its terminal: red E+, black E−, white A−,
  green A+, each as bare copper. A raw reading that stays within a few thousand counts of the amplifier's zero
  and does not move when the cell is pressed means the bridge is not connected (`bridge_probe` tests this on
  the NAU7802).
- **NAU7802 + 5 kg load cell**: 320 SPS at gain 128. With 3.3 V excitation, 5 kg full scale is only a
  few mV, and drag forces at 2 m/s are millinewtons. Mechanical lever ratio and tare discipline matter
  more than electronics here.
- **Qwiic Scale wiring photos**: [front, with terminal connections](images/qwiic-scale-front.jpg) and
  [back, with the printed pin labels](images/qwiic-scale-back.jpg). Check the board's silkscreen before wiring;
  the photos show the colour order but do not replace an electrical check.
- **Bourns EM14 encoder**: specified for a 5 V supply. Check that the outputs are within the F411's input
  limits before connecting them to PA0/PA1. Use a resistor divider or level shifter if the outputs swing
  to 5 V. Set `kEncoderCountsPerRev` to 4 × the cycles/rev of your part.
- **Allegro Hall switch**: digital switch/latch on D7, with magnets on the cup rotor. Set
  `kHallPulsesPerRev` to the number of magnets.
- **Magnets**: secure them with epoxy or heat-shrink, and guard rotating parts (brief safety requirement).
