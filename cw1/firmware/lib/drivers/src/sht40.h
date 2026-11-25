// SHT40 humidity + temperature sensor (on the X-NUCLEO-IKS4A1), non-blocking.
//
// A high-precision measurement takes ~8.3 ms, longer than one 200 Hz tick, so the
// measurement is triggered and collected later instead of waiting with delay().
#pragma once

#include <Arduino.h>
#include <Wire.h>

class Sht40 {
 public:
  bool begin(TwoWire& wire, uint8_t address = 0x44);

  // Call often. Starts a measurement every period_ms and returns true when a fresh,
  // CRC-checked result has been read. Each call does at most one bus transaction.
  bool poll(uint32_t now_ms, uint32_t period_ms = 1000);

  float temperature_c() const { return temp_c_; }
  float humidity_pct() const { return rh_pct_; }
  bool present() const { return present_; }

 private:
  bool fetch();

  TwoWire* wire_ = nullptr;
  uint8_t address_ = 0x44;
  bool present_ = false;
  bool busy_ = false;
  uint32_t started_ms_ = 0;
  uint32_t last_start_ms_ = 0;
  float temp_c_ = NAN;
  float rh_pct_ = NAN;
};
