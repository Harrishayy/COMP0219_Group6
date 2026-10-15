// Small fixed-cost filters for the 200 Hz loop.
#pragma once

#include <cstddef>
#include <cstdint>

namespace airmath {

// First-order IIR low-pass (exponential moving average).
class Ema {
 public:
  explicit Ema(float alpha = 1.0f) : alpha_(alpha) {}
  // alpha for a given -3 dB cutoff at a given sample rate.
  static float alpha_for(float cutoff_hz, float sample_hz);
  void set_alpha(float alpha) { alpha_ = alpha; }
  float update(float x);
  void reset() { primed_ = false; }
  float value() const { return y_; }

 private:
  float alpha_;
  float y_ = 0.0f;
  bool primed_ = false;
};

// Second-order Butterworth low-pass (RBJ biquad, transposed direct form II).
// Primes itself to the first sample, so there is no start-up transient.
class ButterworthLowPass {
 public:
  ButterworthLowPass() = default;
  ButterworthLowPass(float cutoff_hz, float sample_hz) { configure(cutoff_hz, sample_hz); }
  // A cutoff at or above Nyquist turns the filter into a pass-through.
  void configure(float cutoff_hz, float sample_hz);
  float update(float x);
  void reset() { primed_ = false; }

 private:
  float b0_ = 1.0f, b1_ = 0.0f, b2_ = 0.0f, a1_ = 0.0f, a2_ = 0.0f;
  float s1_ = 0.0f, s2_ = 0.0f;
  bool primed_ = false;
};

// Median of the last three samples: removes single-sample spikes with one sample of delay.
class Median3 {
 public:
  float update(float x);

 private:
  float buf_[3] = {0.0f, 0.0f, 0.0f};
  uint8_t count_ = 0;
  uint8_t idx_ = 0;
};

// First-order lead: y_k = x_k + tau * (x_k - x_{k-1}) * fs.
// Cancels the first-order lag of a rotor spinning up/down (time constant ~tau).
// Amplifies noise by (1 + tau*fs), so apply it before the low-pass. tau = 0 is a pass-through.
class LeadCompensator {
 public:
  void configure(float tau_s, float sample_hz) {
    gain_ = (tau_s > 0.0f && sample_hz > 0.0f) ? tau_s * sample_hz : 0.0f;
  }
  float update(float x) {
    float y = primed_ ? x + gain_ * (x - prev_) : x;
    prev_ = x;
    primed_ = true;
    return y;
  }
  void reset() { primed_ = false; }

 private:
  float gain_ = 0.0f;
  float prev_ = 0.0f;
  bool primed_ = false;
};

// Boxcar average over the last N samples.
template <size_t N>
class MovingAverage {
 public:
  float update(float x) {
    sum_ += x - buf_[idx_];
    buf_[idx_] = x;
    idx_ = (idx_ + 1) % N;
    if (count_ < N) ++count_;
    return sum_ / static_cast<float>(count_);
  }

 private:
  float buf_[N] = {};
  float sum_ = 0.0f;
  size_t idx_ = 0;
  size_t count_ = 0;
};

}  // namespace airmath
