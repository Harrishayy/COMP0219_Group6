#include "filters.h"

#include <cmath>

namespace airmath {

namespace {
constexpr float kPi = 3.14159265358979f;
}

float Ema::alpha_for(float cutoff_hz, float sample_hz) {
  if (!(cutoff_hz > 0.0f) || !(sample_hz > 0.0f)) return 1.0f;
  float dt = 1.0f / sample_hz;
  float rc = 1.0f / (2.0f * kPi * cutoff_hz);
  return dt / (rc + dt);
}

float Ema::update(float x) {
  if (!primed_) {
    y_ = x;
    primed_ = true;
  } else {
    y_ += alpha_ * (x - y_);
  }
  return y_;
}

void ButterworthLowPass::configure(float cutoff_hz, float sample_hz) {
  primed_ = false;
  if (!(cutoff_hz > 0.0f) || !(sample_hz > 0.0f) || cutoff_hz >= 0.5f * sample_hz) {
    b0_ = 1.0f;
    b1_ = b2_ = a1_ = a2_ = 0.0f;
    return;
  }
  const float q = 0.70710678f;
  float w0 = 2.0f * kPi * cutoff_hz / sample_hz;
  float cw = std::cos(w0);
  float alpha = std::sin(w0) / (2.0f * q);
  float a0 = 1.0f + alpha;
  b0_ = (1.0f - cw) / 2.0f / a0;
  b1_ = (1.0f - cw) / a0;
  b2_ = b0_;
  a1_ = -2.0f * cw / a0;
  a2_ = (1.0f - alpha) / a0;
}

float ButterworthLowPass::update(float x) {
  if (!primed_) {
    // Steady state for a constant input x (unity DC gain).
    s2_ = x * (b2_ - a2_);
    s1_ = x * (1.0f - b0_);
    primed_ = true;
  }
  float y = b0_ * x + s1_;
  s1_ = b1_ * x - a1_ * y + s2_;
  s2_ = b2_ * x - a2_ * y;
  return y;
}

float Median3::update(float x) {
  buf_[idx_] = x;
  idx_ = (idx_ + 1) % 3;
  if (count_ < 3) {
    ++count_;
    return x;
  }
  float a = buf_[0], b = buf_[1], c = buf_[2];
  if ((a <= b && b <= c) || (c <= b && b <= a)) return b;
  if ((b <= a && a <= c) || (c <= a && a <= b)) return a;
  return c;
}

}  // namespace airmath
