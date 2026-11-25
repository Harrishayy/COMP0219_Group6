// Encoder bench test (Bourns EM14 on TIM2: A -> A0/PA0, B -> A1/PA1).
// Streams "t_ms,count,rev_per_s" at 20 Hz. Spin by hand: one revolution should move the
// count by board::kEncoderCountsPerRev. If it runs backwards, swap A/B or pass invert=true.
#include <Arduino.h>

#include "board_config.h"
#include "quad_encoder.h"
#include "rate_estimator.h"

namespace {
QuadEncoder g_encoder;
airmath::RateEstimator<64> g_rate;
}  // namespace

void setup() {
  Serial.begin(board::kSerialBaud);
  g_encoder.begin(board::kEncoderFilter);
  Serial.println("#cols,t_ms,count,rev_per_s");
}

void loop() {
  static uint32_t last_sample = 0;
  static uint8_t n = 0;
  uint32_t now = micros();
  if (now - last_sample < 5000) return;  // sample at 200 Hz like the main firmware
  last_sample = now;
  g_rate.push(now, g_encoder.count());
  if (++n < 10) return;
  n = 0;
  Serial.print(millis());
  Serial.print(',');
  Serial.print(g_encoder.count());
  Serial.print(',');
  Serial.println(g_rate.rate(100000) / board::kEncoderCountsPerRev, 4);
}
