// Pin map, bus addresses and loop rates for the Nucleo-F411RE + X-NUCLEO-IKS4A1 stack.
// Run the i2c_scan env to confirm the addresses on your hardware.
#pragma once

#include <Arduino.h>

#include "env_sensors.h"

namespace board {

constexpr uint32_t kSerialBaud = 115200;  // ST-LINK virtual COM port (USART2, PA2/PA3)
constexpr float kSampleHz = 200.0f;       // output rate required by the brief

// I2C1 on the Arduino header: SDA = D14 (PB9), SCL = D15 (PB8).
// Shared by the IKS4A1 and the Qwiic parts (NAU7802, MCP9808).
constexpr uint32_t kI2cClockHz = 400000;

// IKS4A1 interrupt lines occupy A2, A3, A4, A5, D4, D6 (and DIL24 lines); keep them free.
// The pins below avoid them.

// Quadrature encoder: fixed to TIM2, A = PA0 (A0), B = PA1 (A1). See quad_encoder.h.
constexpr uint16_t kEncoderCountsPerRev = 4 * 24;  // x4 decoding * EM14 cycles/rev: set to your part
constexpr uint8_t kEncoderFilter = 6;

// TMP36 output -> PC2 (morpho CN7 pin 35, ADC1_IN12).
constexpr uint32_t kPinTmp36 = PC2;

// LM35 output -> PC3 (morpho CN7 pin 37, ADC1_IN13). Powered from 5V (CN7 pin 18): it needs 4-30 V.
constexpr uint32_t kPinLm35 = PC3;

// Allegro Hall switch output -> D7 (PA8).
constexpr uint32_t kPinHallDigital = PA8;
constexpr uint8_t kHallPulsesPerRev = 1;  // magnets on the rotor

constexpr uint32_t kPinLed = LED_BUILTIN;  // LD2, PA5

// 7-bit I2C addresses. The ST libraries take their own (8-bit) defaults internally:
// LPS22DF 0x5D, STTS22H 0x38. Other IKS4A1 parts (unused) sit at 0x19, 0x1E, 0x6A, 0x6B.
constexpr uint8_t kAddrMcp9808 = 0x18;  // A2..A0 = 000; 0x19 would clash with the LIS2DUXS12
constexpr uint8_t kAddrSht40 = 0x44;
constexpr uint8_t kAddrNau7802 = 0x2A;  // fixed

// Environmental sensors refresh period. Density drifts over minutes, not milliseconds.
constexpr uint32_t kEnvPeriodMs = 250;

inline EnvConfig env_config() {
  return EnvConfig{kPinTmp36, kPinLm35, kAddrMcp9808, kAddrSht40, kEnvPeriodMs};
}

}  // namespace board
