#include "env_sensors.h"

#include <LPS22DFSensor.h>
#include <STTS22HSensor.h>

#include "air_density.h"
#include "mcp9808.h"
#include "sht40.h"
#include "tmp36.h"

namespace {
// Values older than this are dropped from the fusion (sensor unplugged mid-run).
constexpr uint32_t kStaleMs = 5000;

// 1-sigma used to weight each source. Datasheet typical accuracy; tighten after
// comparing against a reference thermometer.
constexpr float kSigmaMcp9808 = 0.25f;
constexpr float kSigmaSht40 = 0.2f;
constexpr float kSigmaStts22h = 0.5f;
constexpr float kSigmaLps22df = 1.5f;
constexpr float kSigmaTmp36 = 2.0f;

Mcp9808 g_mcp9808;
Sht40 g_sht40;
LPS22DFSensor* g_lps22df = nullptr;
STTS22HSensor* g_stts22h = nullptr;
Tmp36* g_tmp36 = nullptr;
}  // namespace

EnvSensors::EnvSensors(TwoWire& wire) : wire_(wire) {}

void EnvSensors::begin(const EnvConfig& config) {
  config_ = config;
  present_ = 0;

  if (g_mcp9808.begin(wire_, config.addr_mcp9808)) present_ |= kHasMcp9808;
  if (g_sht40.begin(wire_, config.addr_sht40)) present_ |= kHasSht40;

  static STTS22HSensor stts22h(&wire_);
  uint8_t id = 0;
  if (stts22h.begin() == STTS22H_OK && stts22h.ReadID(&id) == STTS22H_OK && id == STTS22H_ID &&
      stts22h.Enable() == STTS22H_OK) {
    stts22h.SetOutputDataRate(25.0f);
    g_stts22h = &stts22h;
    present_ |= kHasStts22h;
  }

  static LPS22DFSensor lps22df(&wire_);
  if (lps22df.begin() == LPS22DF_OK && lps22df.ReadID(&id) == LPS22DF_OK && id == LPS22DF_ID &&
      lps22df.Enable() == LPS22DF_OK) {
    lps22df.SetOutputDataRate(25.0f);
    g_lps22df = &lps22df;
    present_ |= kHasLps22df;
  }

  static Tmp36 tmp36(config.pin_tmp36);
  if (!isnan(tmp36.read_celsius())) {
    g_tmp36 = &tmp36;
    present_ |= kHasTmp36;
  }

  // Fill everything once so density() is meaningful straight after setup().
  uint32_t now = millis();
  for (uint8_t s = 0; s < kNumSteps; ++s) run_step(static_cast<Step>(s), now);
  delay(12);
  g_sht40.poll(millis(), config_.period_ms);
  fuse(millis());
  step_ = 0;
  cycle_start_ms_ = millis();
}

void EnvSensors::update(uint32_t now_ms) {
  // The SHT40 has to be collected ~10 ms after its trigger regardless of the cycle.
  if (present_ & kHasSht40) {
    if (g_sht40.poll(now_ms, config_.period_ms)) {
      reading_.t_sht40_c = g_sht40.temperature_c();
      reading_.rh_pct = g_sht40.humidity_pct();
      updated_ms_[kSht40] = now_ms;
    }
  }

  // One step per call; a full cycle starts every period_ms.
  if (step_ == 0 && now_ms - cycle_start_ms_ < config_.period_ms) return;
  if (step_ == 0) cycle_start_ms_ = now_ms;
  run_step(static_cast<Step>(step_), now_ms);
  step_ = (step_ + 1) % kNumSteps;
}

void EnvSensors::run_step(Step step, uint32_t now_ms) {
  float v = NAN;
  switch (step) {
    case kMcp9808:
      if ((present_ & kHasMcp9808) && g_mcp9808.read(v)) {
        reading_.t_mcp9808_c = v;
        updated_ms_[step] = now_ms;
      }
      break;
    case kSht40:
      break;  // handled in update(): needs its own trigger/collect timing
    case kStts22h:
      if (g_stts22h && g_stts22h->GetTemperature(&v) == STTS22H_OK) {
        reading_.t_stts22h_c = v;
        updated_ms_[step] = now_ms;
      }
      break;
    case kLps22df:
      if (g_lps22df && g_lps22df->GetPressure(&v) == LPS22DF_OK) {
        reading_.pressure_pa = v * 100.0f;  // hPa -> Pa
        if (g_lps22df->GetTemperature(&v) == LPS22DF_OK) reading_.t_lps22df_c = v;
        updated_ms_[step] = now_ms;
      }
      break;
    case kTmp36:
      if (g_tmp36) {
        v = g_tmp36->read_celsius();
        if (!isnan(v)) {
          reading_.t_tmp36_c = v;
          updated_ms_[step] = now_ms;
        }
      }
      break;
    case kFuse:
      fuse(now_ms);
      break;
    default:
      break;
  }
}

void EnvSensors::fuse(uint32_t now_ms) {
  auto fresh = [&](Step s) { return updated_ms_[s] != 0 && now_ms - updated_ms_[s] < kStaleMs; };
  const airmath::TemperatureSource sources[] = {
      {reading_.t_mcp9808_c, kSigmaMcp9808, fresh(kMcp9808)},
      {reading_.t_sht40_c, kSigmaSht40, fresh(kSht40)},
      {reading_.t_stts22h_c, kSigmaStts22h, fresh(kStts22h)},
      {reading_.t_lps22df_c, kSigmaLps22df, fresh(kLps22df)},
      {reading_.t_tmp36_c, kSigmaTmp36, fresh(kTmp36)},
  };
  float sigma = NAN;
  reading_.temp_c = airmath::fuse_temperatures(sources, sizeof(sources) / sizeof(sources[0]), &sigma);
  reading_.temp_sigma_c = sigma;
  float p = fresh(kLps22df) ? reading_.pressure_pa : NAN;
  float rh = fresh(kSht40) ? reading_.rh_pct : NAN;
  reading_.density = airmath::air_density(reading_.temp_c, p, rh);
}

float EnvSensors::density() const {
  return isnan(reading_.density) ? airmath::dry_air_density(airmath::kStdTempC) : reading_.density;
}

void EnvSensors::print_status(Print& out) const {
  const EnvReading& r = reading_;
  out.print("#env t_fused=");
  out.print(r.temp_c, 2);
  out.print(" sigma=");
  out.print(r.temp_sigma_c, 2);
  out.print(" mcp9808=");
  out.print(r.t_mcp9808_c, 2);
  out.print(" sht40=");
  out.print(r.t_sht40_c, 2);
  out.print(" stts22h=");
  out.print(r.t_stts22h_c, 2);
  out.print(" lps22df=");
  out.print(r.t_lps22df_c, 2);
  out.print(" tmp36=");
  out.print(r.t_tmp36_c, 2);
  out.print(" rh=");
  out.print(r.rh_pct, 1);
  out.print(" p=");
  out.print(r.pressure_pa, 0);
  out.print(" rho=");
  out.println(density(), 4);
}
