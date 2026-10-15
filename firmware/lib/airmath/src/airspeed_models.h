// Transducer models: map a raw feature (rotor speed, force, deflection...) to airspeed.
#pragma once

namespace airmath {

// Rotating transducers (cups, vanes, propellers): v = slope * f + offset.
// Below min_feature the rotor is treated as stalled and 0 is returned.
float speed_linear(float feature, float slope, float offset, float min_feature);

// Force/pressure transducers: v = sqrt(k * (x - x0) / rho), clamped at 0.
//   drag plate:  x = force [N],          k = 2 / (Cd * A)
//   cantilever:  x = deflection signal,  k fitted
//   pitot tap:   x = dynamic pressure,   k = 2
float speed_sqrt_density(float feature, float k, float x0, float rho);

}  // namespace airmath
