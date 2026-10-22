#include "env_sensors.h"

#include <LPS22DFSensor.h>
#include <STTS22HSensor.h>

#include "air_density.h"
#include "lm35.h"
#include "mcp9808.h"
#include "sht40.h"
#include "tmp36.h"

namespace {
// Values older than this no longer count as the air temperature (sensor unplugged mid-run).
constexpr uint32_t kStaleMs = 5000;
// A missing MCP9808 is looked for again this often, so one wired to a running board shows up without a reset.
constexpr uint32_t kReprobeMs = 2000;
uint32_t g_mcp9808_probe_ms = 0;

Mcp9808 g_mcp9808;
Sht40 g_sht40;
LPS22DFSensor* g_lps22df = nullptr;
STTS22HSensor* g_stts22h = nullptr;
Tmp36* g_tmp36 = nullptr;
Lm35* g_lm35 = nullptr;

// Address-only probe ending in a STOP. The drivers below open with a register write
// that skips the STOP (repeated start); on an absent device that NACK leaves the STM32
// I2C peripheral wedged and every later transfer on the bus (the NAU7802) times out.
bool ack(TwoWire& wire, uint8_t address) {
  wire.beginTransmission(address);
  return wire.endTransmission() == 0;
}
}  // namespace

EnvSensors::EnvSensors(TwoWire& wire) : wire_(wire) {}

void EnvSensors::begin(const EnvConfig& config) {
  config_ = config;
  present_ = 0;

  if (ack(wire_, config.addr_mcp9808) && g_mcp9808.begin(wire_, config.addr_mcp9808)) present_ |= kHasMcp9808;
  if (ack(wire_, config.addr_sht40) && g_sht40.begin(wire_, config.addr_sht40)) present_ |= kHasSht40;

  static STTS22HSensor stts22h(&wire_);
  uint8_t id = 0;
  if (ack(wire_, STTS22H_I2C_ADD_H >> 1) && stts22h.begin() == STTS22H_OK && stts22h.ReadID(&id) == STTS22H_OK && id == STTS22H_ID &&
      stts22h.Enable() == STTS22H_OK) {
    stts22h.SetOutputDataRate(25.0f);
    g_stts22h = &stts22h;
    present_ |= kHasStts22h;
  }

  static LPS22DFSensor lps22df(&wire_);
  if (ack(wire_, LPS22DF_I2C_ADD_H >> 1) && lps22df.begin() == LPS22DF_OK && lps22df.ReadID(&id) == LPS22DF_OK && id == LPS22DF_ID &&
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

  // Always read: unlike the TMP36 an absent LM35 reads 0 V (pull-down), so it is safe to keep polling.
  static Lm35 lm35(config.pin_lm35);
  g_lm35 = &lm35;

  // Fill everything once so density() is meaningful straight after setup().
  uint32_t now = millis();
  for (uint8_t s = 0; s < kNumSteps; ++s) run_step(static_cast<Step>(s), now);
  delay(12);
  g_sht40.poll(millis(), config_.period_ms);
  update_air(millis());
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
      if (!(present_ & kHasMcp9808)) {
        if (now_ms - g_mcp9808_probe_ms >= kReprobeMs) {
          g_mcp9808_probe_ms = now_ms;
          if (ack(wire_, config_.addr_mcp9808) && g_mcp9808.begin(wire_, config_.addr_mcp9808)) present_ |= kHasMcp9808;
        }
      } else if (ack(wire_, config_.addr_mcp9808) && g_mcp9808.read(v)) {  // probe first: see ack()
        reading_.t_mcp9808_c = v;
        updated_ms_[step] = now_ms;
      } else if (now_ms - updated_ms_[step] > kStaleMs) {
        present_ &= ~kHasMcp9808;  // unplugged: report it missing and start probing again
        reading_.t_mcp9808_c = NAN;
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
    case kLm35:
      if (g_lm35) {
        v = g_lm35->read_celsius();
        reading_.v_lm35 = g_lm35->last_volts();
        if (!isnan(v)) {
          reading_.t_lm35_c = v;
          updated_ms_[step] = now_ms;
          present_ |= kHasLm35;
        } else {
          reading_.t_lm35_c = NAN;
          present_ &= ~kHasLm35;
        }
      }
      break;
    case kAir:
      update_air(now_ms);
      break;
    default:
      break;
  }
}

void EnvSensors::set_calibration(const EnvCalibration& cal) {
  cal_ = cal;
  if (cal_.source >= kNumThermometers) cal_.source = kThermoMcp9808;
  update_air(millis());
}

float EnvSensors::raw(Thermometer t) const {
  switch (t) {
    case kThermoMcp9808: return reading_.t_mcp9808_c;
    case kThermoSht40: return reading_.t_sht40_c;
    case kThermoStts22h: return reading_.t_stts22h_c;
    case kThermoLps22df: return reading_.t_lps22df_c;
    case kThermoTmp36: return reading_.t_tmp36_c;
    case kThermoLm35: return reading_.t_lm35_c;
    default: return NAN;
  }
}

void EnvSensors::update_air(uint32_t now_ms) {
  auto fresh = [&](Step s) { return updated_ms_[s] != 0 && now_ms - updated_ms_[s] < kStaleMs; };
  static_assert(static_cast<int>(kMcp9808) == kThermoMcp9808 && static_cast<int>(kLm35) == kThermoLm35,
                "Thermometer and Step share the same order for the six thermometers");
  const Thermometer t = static_cast<Thermometer>(cal_.source);
  reading_.temp_c = fresh(static_cast<Step>(t)) ? raw(t) + cal_.offset_c[t] : NAN;
  float p = fresh(kLps22df) ? reading_.pressure_pa : NAN;
  float rh = fresh(kSht40) ? reading_.rh_pct : NAN;
  reading_.density = airmath::air_density(reading_.temp_c, p, rh);
}

float EnvSensors::density() const {
  return isnan(reading_.density) ? airmath::dry_air_density(airmath::kStdTempC) : reading_.density;
}

void EnvSensors::print_status(Print& out) const {
  const EnvReading& r = reading_;
  out.print("#env t_air=");
  out.print(r.temp_c, 2);
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
  out.print(" lm35=");
  out.print(r.t_lm35_c, 2);
  out.print(" lm35_v=");
  out.print(r.v_lm35, 4);
  out.print(" rh=");
  out.print(r.rh_pct, 1);
  out.print(" p=");
  out.print(r.pressure_pa, 0);
  out.print(" rho=");
  out.println(density(), 4);
}
