// Hall sensor bench test. Streams "t_ms,edges,rev_per_s,analog_v" at 20 Hz:
//   digital switch on D7 (PA8): edges and rotation rate
//   linear sensor on PC3:       output voltage (for a cantilever/deflection design)
// Wire whichever part you have; the other column just reads idle.
#include <Arduino.h>

#include "analog_in.h"
#include "board_config.h"
#include "hall_sensor.h"

namespace {
HallTachometer g_tacho;
}

void setup() {
  Serial.begin(board::kSerialBaud);
  analog_in::begin();
  g_tacho.begin(board::kPinHallDigital, board::kHallPulsesPerRev);
  Serial.println("#cols,t_ms,edges,rev_per_s,analog_v");
}

void loop() {
  static uint32_t last = 0;
  if (millis() - last < 50) return;
  last = millis();
  Serial.print(last);
  Serial.print(',');
  Serial.print(g_tacho.edges());
  Serial.print(',');
  Serial.print(g_tacho.rev_per_sec(), 4);
  Serial.print(',');
  Serial.println(analog_in::read_volts(board::kPinHallAnalog, 16), 4);
}
