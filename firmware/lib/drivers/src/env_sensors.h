// Environmental sensing for density compensation: every temperature source in the kit,
// barometric pressure and humidity, fused into one air-density estimate.
//
// Sources (all optional, missing ones are skipped at begin()):
//   MCP9808  (I2C, Qwiic/breakout)    +/-0.25 C
//   SHT40    (IKS4A1)                 +/-0.2 C, plus relative humidity
//   STTS22H  (IKS4A1)                 +/-0.5 C
//   LPS22DF  (IKS4A1)                 absolute pressure, die temperature
//   TMP36    (analog)                 +/-2 C
//   LM35     (analog, 5 V supply)     +/-1 C
#pragma once

#include <Arduino.h>
#include <Wire.h>

struct EnvConfig {
  uint32_t pin_tmp36;
  uint32_t pin_lm35;
  uint8_t addr_mcp9808;
  uint8_t addr_sht40;
  uint32_t period_ms;  // how often each source is refreshed
};

struct EnvReading {
  float t_mcp9808_c = NAN;
  float t_sht40_c = NAN;
  float t_stts22h_c = NAN;
  float t_lps22df_c = NAN;
  float t_tmp36_c = NAN;
  float t_lm35_c = NAN;
  float v_lm35 = NAN;        // LM35 output voltage, also when out of range (wiring diagnosis)
  float rh_pct = NAN;
  float pressure_pa = NAN;
  float temp_c = NAN;        // inverse-variance fused temperature
  float temp_sigma_c = NAN;  // its 1-sigma uncertainty
  float density = NAN;       // kg/m^3
};

class EnvSensors {
 public:
  explicit EnvSensors(TwoWire& wire);

  // Probes each sensor. Blocking (~300 ms); call from setup().
  void begin(const EnvConfig& config);

  // Non-blocking: performs at most one bus transaction per call, so it can run in the
  // idle time between 200 Hz ticks. Air density changes slowly; a few Hz is plenty.
  void update(uint32_t now_ms);

  const EnvReading& reading() const { return reading_; }

  // Current density, or ISA sea-level 1.225 kg/m^3 if nothing has been measured yet.
  float density() const;

  // One "#env ..." line describing every source.
  void print_status(Print& out) const;

  // Sensor presence bitmask, see the kHas* constants.
  uint8_t present_mask() const { return present_; }
  static constexpr uint8_t kHasMcp9808 = 1 << 0;
  static constexpr uint8_t kHasSht40 = 1 << 1;
  static constexpr uint8_t kHasStts22h = 1 << 2;
  static constexpr uint8_t kHasLps22df = 1 << 3;
  static constexpr uint8_t kHasTmp36 = 1 << 4;
  static constexpr uint8_t kHasLm35 = 1 << 5;  // last read in range; it may be plugged in after boot

 private:
  enum Step : uint8_t { kMcp9808, kSht40, kStts22h, kLps22df, kTmp36, kLm35, kFuse, kNumSteps };
  void run_step(Step step, uint32_t now_ms);
  void fuse(uint32_t now_ms);

  TwoWire& wire_;
  EnvConfig config_{};
  EnvReading reading_;
  uint8_t present_ = 0;
  uint8_t step_ = 0;
  uint32_t cycle_start_ms_ = 0;
  uint32_t updated_ms_[kNumSteps] = {};
};
