// Quadrature encoder (Bourns EM14) counted in hardware by TIM2 in encoder mode.
//
// Fixed wiring: channel A -> PA0 (Arduino A0, TIM2_CH1), channel B -> PA1 (A1, TIM2_CH2).
// The timer counts every edge of both channels (x4 decoding) with zero CPU load, and
// TIM2 is 32 bits wide so the count does not wrap in any realistic run.
#pragma once

#include <Arduino.h>

class QuadEncoder {
 public:
  // input_filter: TIMx_CCMR1 ICxF value 0..15 (higher rejects more glitches);
  // invert flips the counting direction.
  void begin(uint8_t input_filter = 6, bool invert = false);
  int32_t count() const { return static_cast<int32_t>(TIM2->CNT); }
  void reset() { TIM2->CNT = 0; }
};
