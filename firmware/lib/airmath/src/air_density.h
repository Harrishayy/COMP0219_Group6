// Air density from temperature, pressure and humidity.
//
// Framework-free so it runs both on the Nucleo and in the native unit tests.
#pragma once

#include <cstddef>

namespace airmath {

constexpr float kRDry = 287.058f;          // J/(kg K), specific gas constant of dry air
constexpr float kRVapour = 461.495f;       // J/(kg K), specific gas constant of water vapour
constexpr float kKelvinOffset = 273.15f;
constexpr float kStdPressurePa = 101325.0f;
constexpr float kStdTempC = 15.0f;         // ISA sea level, gives rho = 1.225 kg/m^3

// Saturation vapour pressure over liquid water [Pa].
// Magnus form with the Alduchov & Eskridge (1996) coefficients, <0.4% error from -40 to 50 C.
float saturation_vapour_pressure_pa(float temp_c);

// Ideal-gas density of dry air [kg/m^3].
float dry_air_density(float temp_c, float pressure_pa = kStdPressurePa);

// Density of humid air [kg/m^3]: dry-air and water-vapour partial pressures summed.
// Humid air is lighter than dry air at the same T and p (about 1% at 25 C, 100% RH).
float humid_air_density(float temp_c, float pressure_pa, float rh_percent);

// Best available density estimate. Pass NAN for any input you don't have:
// a missing pressure falls back to 101325 Pa, missing RH to dry air, missing T to 15 C.
float air_density(float temp_c, float pressure_pa, float rh_percent);

struct TemperatureSource {
  float value_c;
  float sigma_c;  // 1-sigma uncertainty, used as the fusion weight
  bool valid;
};

// Inverse-variance weighted mean of the valid sources. Returns NAN if none are valid.
// If sigma_out is given it receives the combined 1-sigma uncertainty.
float fuse_temperatures(const TemperatureSource* sources, size_t n, float* sigma_out = nullptr);

}  // namespace airmath
