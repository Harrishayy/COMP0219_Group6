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
| I2C1 SDA / SCL                 | D14 (PB9) / D15 (PB8)  | IKS4A1 + Qwiic NAU7802 + MCP9808, 400 kHz                    |
| Encoder A / B (EM14)           | A0 (PA0) / A1 (PA1)    | TIM2 CH1/CH2, AF1, hardware x4 decoding                      |
| TMP36 Vout                     | PC2 (CN7 pin 35)       | ADC1_IN12, VDDA measured via VREFINT                         |
| Hall switch output             | D7 (PA8)               | EXTI, internal pull-up (Allegro switches are open-drain)    |
| Linear Hall output             | PC3 (CN7 pin 37)       | ADC1_IN13                                                    |
| Serial to PC                   | PA2 / PA3 (USART2)     | ST-LINK virtual COM port, no wiring needed                   |
| LED                            | LD2 (PA5)              | Heartbeat blink while streaming                              |

**Pins the IKS4A1 already uses** (sensor interrupt lines, from ST's Zephyr shield definition):
A2, A3, A4, A5, D4, D6. Do not wire anything else to them. Most IKS4A1 interrupt outputs are push-pull
and would fight an external signal.

## I2C addresses (7-bit)

| Device                 | Address | Board            |
| ---------------------- | ------- | ---------------- |
| LIS2DUXS12 accel       | 0x19    | IKS4A1           |
| LIS2MDL magnetometer   | 0x1E    | IKS4A1           |
| NAU7802 (Qwiic Scale)  | 0x2A    | SparkFun Qwiic   |
| STTS22H temperature    | 0x38    | IKS4A1           |
| SHT40 humidity/temp    | 0x44    | IKS4A1           |
| LPS22DF pressure       | 0x5D    | IKS4A1           |
| LSM6DSO16IS IMU        | 0x6A    | IKS4A1           |
| LSM6DSV16X IMU         | 0x6B    | IKS4A1           |
| MCP9808 temperature    | 0x18    | breakout, A2..A0 = 000 (0x19 would clash with the LIS2DUXS12) |

Run `pio run -e i2c_scan -t upload` to confirm. The IKS4A1 must be in its standard mode (Mode 1, the
factory jumper setting on J4/J5; see ST UM3239) so that the sensors sit on the main I2C bus rather than
behind a sensor hub.

## Per-part notes

- **TMP36**: needs 2.7–5.5 V, so power it from 3V3. Keep it off the MCU board and in the airflow for a
  representative air temperature. Output is 10 mV/°C, with 750 mV at 25 °C.
- **MCP9808**: the most accurate thermometer in the kit. Mount it in the free stream, away from the Nucleo.
- **NAU7802 + 5 kg load cell**: 320 SPS at gain 128. With 3.3 V excitation, 5 kg full scale is only a
  few mV, and drag forces at 2 m/s are millinewtons. Mechanical lever ratio and tare discipline matter
  more than electronics here.
- **Bourns EM14 encoder**: specified for a 5 V supply. Check that the outputs are within the F411's input
  limits before connecting them to PA0/PA1. Use a resistor divider or level shifter if the outputs swing
  to 5 V. Set `kEncoderCountsPerRev` to 4 × the cycles/rev of your part.
- **Allegro Hall sensor**: a digital switch/latch goes on D7. A linear (ratiometric) part goes on PC3 and
  needs the same supply as VDDA, so its output stays within 0–3.3 V.
- **Magnets**: secure them with epoxy or heat-shrink, and guard rotating parts (brief safety requirement).
