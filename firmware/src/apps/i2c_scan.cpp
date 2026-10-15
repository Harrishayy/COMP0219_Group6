// I2C bus scanner: lists every responding address with the kit part it most likely is.
// Run it first whenever the stack changes.
#include <Arduino.h>
#include <Wire.h>

#include "board_config.h"

namespace {
const char* guess(uint8_t addr) {
  switch (addr) {
    case 0x18: return "MCP9808 (default) / LIS2DUXS12 (SA0=0)";
    case 0x19: return "LIS2DUXS12 (IKS4A1, unused)";
    case 0x1E: return "LIS2MDL magnetometer (IKS4A1, unused)";
    case 0x2A: return "NAU7802 Qwiic Scale";
    case 0x38: return "STTS22H temperature (IKS4A1)";
    case 0x44: return "SHT40 humidity/temperature (IKS4A1)";
    case 0x5D: return "LPS22DF pressure (IKS4A1)";
    case 0x6A: return "LSM6DSO16IS IMU (IKS4A1, unused)";
    case 0x6B: return "LSM6DSV16X IMU (IKS4A1, unused)";
    default:
      if (addr >= 0x18 && addr <= 0x1F) return "MCP9808 (A2..A0 strapped)?";
      return "?";
  }
}
}  // namespace

void setup() {
  Serial.begin(board::kSerialBaud);
  Wire.begin();
  Wire.setClock(100000);
}

void loop() {
  Serial.println("# scanning I2C1 (SDA=D14/PB9, SCL=D15/PB8)...");
  uint8_t found = 0;
  for (uint8_t addr = 1; addr < 127; ++addr) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      Serial.print("  0x");
      if (addr < 16) Serial.print('0');
      Serial.print(addr, HEX);
      Serial.print("  ");
      Serial.println(guess(addr));
      ++found;
    }
  }
  Serial.print("# ");
  Serial.print(found);
  Serial.println(" device(s)\n");
  delay(3000);
}
