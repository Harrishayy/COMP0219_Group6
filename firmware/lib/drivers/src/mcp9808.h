// MCP9808 I2C temperature sensor (+/-0.25 C typical), continuous conversion at 0.0625 C.
#pragma once

#include <Arduino.h>
#include <Wire.h>

class Mcp9808 {
 public:
  // Checks the manufacturer ID and sets the finest resolution (250 ms per conversion).
  bool begin(TwoWire& wire, uint8_t address = 0x18);
  // One register read, no waiting. Returns false if the sensor is absent or the read failed.
  bool read(float& temp_c);
  bool present() const { return present_; }

 private:
  bool read_reg16(uint8_t reg, uint16_t& value);
  bool write_reg8(uint8_t reg, uint8_t value);

  TwoWire* wire_ = nullptr;
  uint8_t address_ = 0x18;
  bool present_ = false;
};
