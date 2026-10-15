// Allegro Hall-effect sensor, in either of two roles:
//   HallTachometer: digital switch/latch + rotor magnets -> rotation rate
//   (linear/ratiometric parts are read with analog_in::read_volts() instead).
#pragma once

#include <Arduino.h>

// Rotation rate from the timing of Hall edges. Single instance (one ISR).
class HallTachometer {
 public:
  // pulses_per_rev: magnets passing the sensor per revolution (1..15).
  // timeout_us: no edge for this long means the rotor is stopped.
  void begin(uint32_t pin, uint8_t pulses_per_rev, uint32_t timeout_us = 500000);

  // Revolutions per second, averaged over the last full revolution so uneven magnet
  // spacing cancels. 0 when stopped.
  float rev_per_sec() const;

  // Total edges seen (a monotonic counter, usable with airmath::RateEstimator).
  uint32_t edges() const;

 private:
  uint8_t pulses_per_rev_ = 1;
  uint32_t timeout_us_ = 500000;
};
