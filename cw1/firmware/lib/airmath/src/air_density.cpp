#include "air_density.h"

#include <cmath>

namespace airmath {

float saturation_vapour_pressure_pa(float temp_c) {
  return 610.94f * std::exp(17.625f * temp_c / (temp_c + 243.04f));
}

float dry_air_density(float temp_c, float pressure_pa) {
  return pressure_pa / (kRDry * (temp_c + kKelvinOffset));
}

float humid_air_density(float temp_c, float pressure_pa, float rh_percent) {
  float rh = rh_percent < 0.0f ? 0.0f : (rh_percent > 100.0f ? 100.0f : rh_percent);
  float t_k = temp_c + kKelvinOffset;
  float p_vapour = rh / 100.0f * saturation_vapour_pressure_pa(temp_c);
  float p_dry = pressure_pa - p_vapour;
  return p_dry / (kRDry * t_k) + p_vapour / (kRVapour * t_k);
}

float air_density(float temp_c, float pressure_pa, float rh_percent) {
  float t = std::isfinite(temp_c) ? temp_c : kStdTempC;
  float p = std::isfinite(pressure_pa) ? pressure_pa : kStdPressurePa;
  if (std::isfinite(rh_percent)) return humid_air_density(t, p, rh_percent);
  return dry_air_density(t, p);
}

}  // namespace airmath
