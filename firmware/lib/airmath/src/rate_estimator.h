// Rate of a monotonic counter (encoder counts, Hall edges) over a sliding time window.
#pragma once

#include <cstddef>
#include <cstdint>

namespace airmath {

// Keeps the last N (time_us, count) samples. Both timestamps and counts may wrap:
// differences are taken in modular arithmetic, so a free-running 32-bit timer is fine.
template <size_t N>
class RateEstimator {
 public:
  void push(uint32_t t_us, int32_t count) {
    t_[head_] = t_us;
    c_[head_] = count;
    head_ = (head_ + 1) % N;
    if (size_ < N) ++size_;
  }

  // Counts per second over the newest samples spanning at most window_us.
  // Returns 0 until two samples are available.
  float rate(uint32_t window_us) const {
    if (size_ < 2) return 0.0f;
    size_t newest = (head_ + N - 1) % N;
    size_t oldest = newest;
    for (size_t k = 1; k < size_; ++k) {
      size_t i = (newest + N - k) % N;
      if (t_[newest] - t_[i] > window_us) break;
      oldest = i;
    }
    if (oldest == newest) oldest = (newest + N - 1) % N;  // window shorter than one tick
    uint32_t dt = t_[newest] - t_[oldest];
    if (dt == 0) return 0.0f;
    int32_t dc = static_cast<int32_t>(static_cast<uint32_t>(c_[newest]) -
                                      static_cast<uint32_t>(c_[oldest]));
    return static_cast<float>(dc) * 1e6f / static_cast<float>(dt);
  }

  void reset() { head_ = size_ = 0; }

 private:
  uint32_t t_[N] = {};
  int32_t c_[N] = {};
  size_t head_ = 0;
  size_t size_ = 0;
};

}  // namespace airmath
