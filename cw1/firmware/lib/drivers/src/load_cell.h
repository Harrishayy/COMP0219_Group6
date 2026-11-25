// Strain-gauge load cell behind one of two I2C bridge amplifiers, found at runtime:
//   - M5Stack Unit Weight I2C (U177): an HX711 read by the unit's own MCU, 24-bit, 10 SPS.
//   - SparkFun Qwiic Scale (NAU7802): 24-bit, gain 128, 320 SPS.
// Both give raw counts; force = (counts - offset) * scale for either, so tare and the known-mass scale work the
// same way. A front end that is unplugged is looked for again every 2 s, so either can be swapped in live.
#pragma once

#include <Arduino.h>
#include <SparkFun_Qwiic_Scale_NAU7802_Arduino_Library.h>
#include <Wire.h>

#include "still_mean.h"

enum class LoadCellFrontend : uint8_t { kNone, kWeightI2c, kNau7802 };
enum class TareResult : uint8_t { kNone, kAccepted, kRefused };

struct LoadCellConfig {
  uint8_t addr_weight_i2c;   // M5Stack Unit Weight I2C, 0x26 by default
  uint8_t addr_nau7802;      // fixed 0x2A
  LoadCellFrontend prefer;   // used when both answer
};

class LoadCell {
 public:
  // Finds a front end and configures it. Blocking (up to ~1.5 s while the Weight unit boots, ~300 ms for the
  // NAU7802 LDO ramp); call from setup(). False when neither answers (poll() keeps looking).
  bool begin(TwoWire& wire, const LoadCellConfig& cfg, int32_t offset_counts, float newtons_per_count);

  // Reads a conversion if one is ready. Returns true when a new sample arrived.
  bool poll();

  int32_t raw() const { return raw_; }
  float force_n() const { return static_cast<float>(raw_ - offset_) * scale_; }

  // Non-blocking tare: the next `samples` readings are averaged into the zero offset. 0 = half a second's worth.
  // With max_sd_counts > 0 the zero is kept only if the readings were that still (standard deviation in counts);
  // otherwise the old offset stays and the tare reports kRefused.
  void start_tare(uint16_t samples = 0, float max_sd_counts = 0.0f);
  bool taring() const { return tare_left_ > 0; }
  void cancel_tare() { tare_left_ = 0; }  // the offset is left as it was; no result is reported
  // The outcome of the last finished tare, once: kNone until one finishes, then kAccepted or kRefused.
  TareResult take_tare_result() {
    TareResult r = tare_result_;
    tare_result_ = TareResult::kNone;
    return r;
  }
  float last_tare_sd() const { return tare_sd_; }       // spread of the last tare's readings, counts
  int32_t last_tare_mean() const { return tare_mean_; }  // their mean (the offset, if it was accepted)

  int32_t offset() const { return offset_; }
  void set_offset(int32_t offset) { offset_ = offset; }
  float scale() const { return scale_; }
  void set_scale(float newtons_per_count) { scale_ = newtons_per_count; }
  bool present() const { return frontend_ != LoadCellFrontend::kNone; }

  LoadCellFrontend frontend() const { return frontend_; }
  const char* frontend_name() const;  // "weight_i2c", "nau7802" or "none"
  float sample_hz() const;            // conversions per second of the current front end
  uint32_t samples() const { return sample_total_; }  // conversions accepted since boot
  uint32_t failures() const { return fail_total_; }   // I2C reads that failed (and were dropped)

 private:
  bool probe();
  bool begin_weight_i2c();
  bool begin_nau7802();
  bool read_weight_raw(int32_t* out);
  void accept(int32_t raw);
  bool note_fail(uint32_t now);

  static constexpr uint32_t kReprobeMs = 2000;
  static constexpr uint32_t kWeightPollMs = 20;   // 50 Hz: 5x the unit's 10 SPS, new values seen within 20 ms
  static constexpr uint8_t kMaxFails = 10;        // consecutive failed reads before the front end counts as gone

  TwoWire* wire_ = nullptr;
  LoadCellConfig cfg_{0x26, 0x2A, LoadCellFrontend::kWeightI2c};
  LoadCellFrontend frontend_ = LoadCellFrontend::kNone;
  NAU7802 nau_;
  uint32_t last_probe_ms_ = 0;
  uint32_t last_read_ms_ = 0;
  int32_t last_weight_raw_ = INT32_MIN;
  uint8_t fails_ = 0;
  uint32_t sample_total_ = 0;
  uint32_t fail_total_ = 0;
  int32_t raw_ = 0;
  int32_t offset_ = 0;
  float scale_ = 1.0f;
  uint16_t tare_left_ = 0;
  airmath::StillMean tare_;
  float tare_max_sd_ = 0.0f;
  float tare_sd_ = 0.0f;
  int32_t tare_mean_ = 0;
  TareResult tare_result_ = TareResult::kNone;
};
