// Strain-gauge load cell on the SparkFun Qwiic Scale (NAU7802, 24-bit, up to 320 SPS).
#pragma once

#include <Arduino.h>
#include <SparkFun_Qwiic_Scale_NAU7802_Arduino_Library.h>
#include <Wire.h>

class LoadCell {
 public:
  // Configures 320 SPS, gain 128, 3.3 V excitation, and runs the internal AFE calibration.
  // Blocking (~300 ms, the LDO ramp); call from setup().
  bool begin(TwoWire& wire, int32_t offset_counts, float newtons_per_count);

  // Reads a conversion if one is ready. Returns true when a new sample arrived.
  bool poll();

  int32_t raw() const { return raw_; }
  float force_n() const { return static_cast<float>(raw_ - offset_) * scale_; }

  // Non-blocking tare: the next `samples` readings are averaged into the zero offset.
  void start_tare(uint16_t samples = 160);
  bool taring() const { return tare_left_ > 0; }

  int32_t offset() const { return offset_; }
  void set_offset(int32_t offset) { offset_ = offset; }
  float scale() const { return scale_; }
  void set_scale(float newtons_per_count) { scale_ = newtons_per_count; }
  bool present() const { return present_; }

 private:
  NAU7802 adc_;
  bool present_ = false;
  int32_t raw_ = 0;
  int32_t offset_ = 0;
  float scale_ = 1.0f;
  uint16_t tare_left_ = 0;
  uint16_t tare_total_ = 0;
  int64_t tare_sum_ = 0;
};
