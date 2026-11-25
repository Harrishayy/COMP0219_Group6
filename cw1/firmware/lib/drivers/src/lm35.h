// LM35 analog temperature sensor: 10 mV/C with no offset (0 V at 0 C), +/-0.5 C at 25 C (LM35A/CA),
// +/-1 C (LM35), +/-1.5 C (LM35D). Needs a 4-30 V supply: power it from the Nucleo's 5V pin. In the basic
// circuit it reads 2-150 C, so its output stays inside the 3.3 V ADC range.
#pragma once

#include <Arduino.h>

class Lm35 {
 public:
  explicit Lm35(uint32_t pin) : pin_(pin) {}

  // Returns NAN outside 2..150 C. With the 100 kOhm pull-down from Vout to GND (docs/hardware.md), an
  // unplugged or unpowered LM35 reads 0 V and so NAN; without it the floating pin reads noise.
  float read_celsius(uint8_t oversample = 16);
  float last_volts() const { return volts_; }

 private:
  uint32_t pin_;
  float volts_ = NAN;
};
