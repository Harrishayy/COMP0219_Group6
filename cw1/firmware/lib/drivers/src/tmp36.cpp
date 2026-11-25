#include "tmp36.h"

#include "analog_in.h"

float Tmp36::read_celsius(uint8_t oversample) {
  volts_ = analog_in::read_volts(pin_, oversample);
  float t = (volts_ - 0.5f) / 0.01f;
  if (t < -40.0f || t > 125.0f) return NAN;
  return t;
}
