#include "airspeed_models.h"

#include <cmath>

namespace airmath {

float speed_linear(float feature, float slope, float offset, float min_feature) {
  if (!(feature >= min_feature)) return 0.0f;
  float v = slope * feature + offset;
  return v > 0.0f ? v : 0.0f;
}

float speed_sqrt_density(float feature, float k, float x0, float rho) {
  if (!(rho > 0.0f)) return 0.0f;
  float arg = k * (feature - x0) / rho;
  return arg > 0.0f ? std::sqrt(arg) : 0.0f;
}

}  // namespace airmath
