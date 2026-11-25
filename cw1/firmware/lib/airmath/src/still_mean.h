// Mean and spread of a window of integer readings, for taring: the zero is the window's mean, and the spread says
// whether the readings were still enough to be a zero at all (a plate in moving air wobbles far more than at rest).
#pragma once

#include <cmath>
#include <cstdint>

namespace airmath {

class StillMean {
 public:
  void reset() {
    n_ = 0;
    first_ = 0;
    sum_ = 0;
    sum_sq_ = 0;
  }

  // Sums are kept relative to the first reading, so 24-bit counts far from zero lose no precision.
  void add(int32_t x) {
    if (n_ == 0) first_ = x;
    int64_t d = static_cast<int64_t>(x) - first_;
    sum_ += d;
    sum_sq_ += d * d;
    ++n_;
  }

  uint32_t count() const { return n_; }

  // Rounded to the nearest count. 0 before any reading.
  int32_t mean() const {
    if (n_ == 0) return 0;
    double m = static_cast<double>(sum_) / n_;
    return static_cast<int32_t>(first_ + static_cast<int64_t>(std::llround(m)));
  }

  // Population standard deviation, in counts. 0 before two readings.
  float sd() const {
    if (n_ < 2) return 0.0f;
    double m = static_cast<double>(sum_) / n_;
    double var = static_cast<double>(sum_sq_) / n_ - m * m;
    return var > 0.0 ? static_cast<float>(std::sqrt(var)) : 0.0f;
  }

 private:
  uint32_t n_ = 0;
  int32_t first_ = 0;
  int64_t sum_ = 0;
  int64_t sum_sq_ = 0;
};

}  // namespace airmath
