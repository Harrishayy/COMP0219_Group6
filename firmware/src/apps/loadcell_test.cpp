// Load cell bench test and scale calibration (NAU7802 + 5 kg cell).
//
//   t        tare: average the unloaded reading into the zero offset
//   k <g>    put a known mass of <g> grams on the cell, then send e.g. "k 200"
//            to compute newtons-per-count; copy the printed values into calibration.h
//
// Streams "t_ms,raw,force_n" at ~20 Hz.
#include <Arduino.h>
#include <Wire.h>

#include "board_config.h"
#include "calibration.h"
#include "load_cell.h"

namespace {
constexpr float kGravity = 9.80665f;
LoadCell g_cell;
int64_t g_avg_sum = 0;
uint16_t g_avg_left = 0;
uint16_t g_avg_total = 0;
float g_known_mass_g = 0.0f;

void print_cal() {
  Serial.print("# CAL_LC_OFFSET ");
  Serial.print(g_cell.offset());
  Serial.print("  CAL_LC_N_PER_COUNT ");
  Serial.println(g_cell.scale(), 10);
}
}  // namespace

void setup() {
  Serial.begin(board::kSerialBaud);
  Wire.begin();
  Wire.setClock(board::kI2cClockHz);
  if (!g_cell.begin(Wire, static_cast<int32_t>(CAL_LC_OFFSET), CAL_LC_N_PER_COUNT)) {
    Serial.println("# NAU7802 not found at 0x2A - check the Qwiic cable");
  }
  Serial.println("# commands: t (tare), k <grams> (scale with known mass)");
  Serial.println("#cols,t_ms,raw,force_n");
}

void loop() {
  static uint32_t last_print = 0;

  if (g_cell.poll() && g_avg_left > 0) {
    g_avg_sum += g_cell.raw();
    if (--g_avg_left == 0) {
      float mean = static_cast<float>(g_avg_sum) / g_avg_total;
      float counts = mean - g_cell.offset();
      if (counts != 0.0f) g_cell.set_scale(g_known_mass_g / 1000.0f * kGravity / counts);
      print_cal();
    }
  }
  if (!g_cell.taring() && g_avg_left == 0 && g_cell.present() && millis() - last_print >= 50) {
    last_print = millis();
    Serial.print(last_print);
    Serial.print(',');
    Serial.print(g_cell.raw());
    Serial.print(',');
    Serial.println(g_cell.force_n(), 5);
  }

  if (Serial.available()) {
    char c = static_cast<char>(Serial.read());
    if (c == 't') {
      g_cell.start_tare(320);
      Serial.println("# taring for 1 s...");
    } else if (c == 'k') {
      g_known_mass_g = Serial.parseFloat();
      if (g_known_mass_g > 0.0f) {
        g_avg_sum = 0;
        g_avg_total = g_avg_left = 320;
        Serial.println("# averaging known mass for 1 s...");
      }
    } else if (c == 'p') {
      print_cal();
    }
  }
  static bool was_taring = false;
  if (was_taring && !g_cell.taring()) print_cal();
  was_taring = g_cell.taring();
}
