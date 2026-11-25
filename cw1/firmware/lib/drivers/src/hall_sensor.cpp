#include "hall_sensor.h"

namespace {
constexpr uint8_t kRing = 16;
volatile uint32_t g_edge_us[kRing];
volatile uint32_t g_edges = 0;

void on_edge() {
  g_edge_us[g_edges % kRing] = micros();
  g_edges = g_edges + 1;
}
}  // namespace

void HallTachometer::begin(uint32_t pin, uint8_t pulses_per_rev, uint32_t timeout_us) {
  pulses_per_rev_ = pulses_per_rev < 1 ? 1 : (pulses_per_rev >= kRing ? kRing - 1 : pulses_per_rev);
  timeout_us_ = timeout_us;
  // Allegro switches are open-drain and pull low when a south pole is near.
  pinMode(pin, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(pin), on_edge, FALLING);
}

float HallTachometer::rev_per_sec() const {
  noInterrupts();
  uint32_t n = g_edges;
  uint32_t newest = n ? g_edge_us[(n - 1) % kRing] : 0;
  uint32_t oldest = n > pulses_per_rev_ ? g_edge_us[(n - 1 - pulses_per_rev_) % kRing] : 0;
  interrupts();

  if (n <= pulses_per_rev_) return 0.0f;
  uint32_t now = micros();
  uint32_t span = newest - oldest;
  if (now - newest > timeout_us_ || span == 0) return 0.0f;
  // If the rotor is slowing, the time since the last edge bounds the rate from above.
  uint32_t since = now - newest;
  float period_per_rev = static_cast<float>(span);
  float rate = 1e6f / period_per_rev;
  float bound = 1e6f / (static_cast<float>(since) * pulses_per_rev_);
  return since * pulses_per_rev_ > span ? bound : rate;
}

uint32_t HallTachometer::edges() const { return g_edges; }
