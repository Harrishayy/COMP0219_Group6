// Hall switch bench test (D7/PA8). Streams "t_ms,edges,rev_per_s" at 20 Hz.
// Spin the cups by hand: edges should rise by board::kHallPulsesPerRev per revolution.
#include <Arduino.h>

#include "board_config.h"
#include "hall_sensor.h"

namespace {
HallTachometer g_tacho;
}

void setup() {
  Serial.begin(board::kSerialBaud);
  g_tacho.begin(board::kPinHallDigital, board::kHallPulsesPerRev);
  Serial.println("#cols,t_ms,edges,rev_per_s");
}

void loop() {
  static uint32_t last = 0;
  if (millis() - last < 50) return;
  last = millis();
  Serial.print(last);
  Serial.print(',');
  Serial.print(g_tacho.edges());
  Serial.print(',');
  Serial.println(g_tacho.rev_per_sec(), 4);
}
