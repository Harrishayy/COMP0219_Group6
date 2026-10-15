// Environment monitor: every temperature source, pressure, humidity and the resulting
// air density, as CSV at 4 Hz. Use it to check wiring, compare the thermometers against
// each other, and log the conditions during a calibration run.
#include <Arduino.h>
#include <Wire.h>

#include "analog_in.h"
#include "board_config.h"
#include "env_sensors.h"

namespace {
EnvSensors g_env(Wire);

void print_present(uint8_t mask, uint8_t bit, const char* name) {
  Serial.print("# ");
  Serial.print(name);
  Serial.println((mask & bit) ? ": found" : ": NOT FOUND");
}
}  // namespace

void setup() {
  Serial.begin(board::kSerialBaud);
  Wire.begin();
  Wire.setClock(board::kI2cClockHz);
  analog_in::begin();
  g_env.begin(board::env_config());

  uint8_t mask = g_env.present_mask();
  Serial.print("# env_monitor, VDDA = ");
  Serial.print(analog_in::vdda(), 3);
  Serial.println(" V");
  print_present(mask, EnvSensors::kHasMcp9808, "MCP9808");
  print_present(mask, EnvSensors::kHasSht40, "SHT40");
  print_present(mask, EnvSensors::kHasStts22h, "STTS22H");
  print_present(mask, EnvSensors::kHasLps22df, "LPS22DF");
  print_present(mask, EnvSensors::kHasTmp36, "TMP36");
  Serial.println("#cols,t_ms,t_mcp9808,t_sht40,t_stts22h,t_lps22df,t_tmp36,t_fused,t_sigma,rh_pct,pressure_pa,rho");
}

void loop() {
  static uint32_t last_print = 0;
  uint32_t now = millis();
  g_env.update(now);
  if (now - last_print < 250) return;
  last_print = now;

  const EnvReading& r = g_env.reading();
  const float cols[] = {r.t_mcp9808_c, r.t_sht40_c,   r.t_stts22h_c, r.t_lps22df_c,
                        r.t_tmp36_c,   r.temp_c,      r.temp_sigma_c, r.rh_pct};
  Serial.print(now);
  for (float c : cols) {
    Serial.print(',');
    Serial.print(c, 2);
  }
  Serial.print(',');
  Serial.print(r.pressure_pa, 1);
  Serial.print(',');
  Serial.println(g_env.density(), 5);
}
