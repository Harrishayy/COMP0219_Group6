// TMP36 analog temperature sensor: 10 mV/C with a 500 mV offset at 0 C, +/-2 C typical.
#pragma once

#include <Arduino.h>

class Tmp36 {
 public:
  explicit Tmp36(uint32_t pin) : pin_(pin) {}

  // Returns NAN when the output voltage is outside the -40..125 C range (open or shorted pin).
  float read_celsius(uint8_t oversample = 16);
  float last_volts() const { return volts_; }

 private:
  uint32_t pin_;
  float volts_ = NAN;
};
