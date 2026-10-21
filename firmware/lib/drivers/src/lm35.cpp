#include "lm35.h"

#include "analog_in.h"

float Lm35::read_celsius(uint8_t oversample) {
  volts_ = analog_in::read_volts(pin_, oversample);
  float t = volts_ / 0.01f;
  if (t < 2.0f || t > 150.0f) return NAN;
  return t;
}
