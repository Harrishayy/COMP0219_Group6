// Transducer selection and calibration constants.
//
// Defaults live here. Fitted values go in calibration_generated.h (same macro names),
// which overrides these when present. Every CAL_<NAME> is also a live parameter:
// "SET <name> <value>" over serial changes it in RAM, "GET" prints them all.
#pragma once

#if __has_include("calibration_generated.h")
#include "calibration_generated.h"
#endif

// ---- Which transducer feeds the estimate ---------------------------------------------
#define METHOD_CUP_ENCODER 1      // cups/vanes on the EM14 encoder, feature = rev/s
#define METHOD_CUP_HALL 2         // cups/vanes with magnets + Hall switch, feature = rev/s
#define METHOD_DRAG_PLATE 3       // plate on the load cell, feature = force [N]

#ifndef AIRSPEED_METHOD
#define AIRSPEED_METHOD METHOD_CUP_ENCODER
#endif

// ---- Linear model for rotors: v = rotor_slope * f + rotor_offset (f in rev/s) -----------
#ifndef CAL_ROTOR_SLOPE
#define CAL_ROTOR_SLOPE 1.0f  // m/s per rev/s; placeholder until fitted
#endif
#ifndef CAL_ROTOR_OFFSET
#define CAL_ROTOR_OFFSET 0.0f  // m/s, roughly the stall speed
#endif
#ifndef CAL_ROTOR_MIN_HZ
#define CAL_ROTOR_MIN_HZ 0.05f  // below this the rotor is considered stopped
#endif
#ifndef CAL_ROTOR_WINDOW_MS
#define CAL_ROTOR_WINDOW_MS 50.0f  // encoder rate window: longer = smoother but laggier
#endif

// ---- Square-root model for the drag plate: v = sqrt(sqrt_k * (F - sqrt_x0) / rho) --------
// sqrt_k = 2 / (Cd * A), A in m². The default is our body, the hollow hemisphere open to the wind (catapult.stl:
// 86 mm outside, 80 mm inside; Cd 1.42 on the outer rim's 58.1 cm², Cd*A = 82.5 cm²), so about 2.0 g of drag at
// 2 m/s and 50 g at 10 m/s; the arm is not included. It must stay equal to the toolkit's default
// (calibration/calib/models.py, test_chain.py checks): 1.0 would mean a 2 m² plate, so a few hundred grams
// would read as 2 m/s.
#ifndef CAL_SQRT_K
#define CAL_SQRT_K 242.5f
#endif
#ifndef CAL_SQRT_X0
#define CAL_SQRT_X0 0.0f
#endif

// ---- Load cell: force = (counts - lc_offset) * lc_n_per_count ----------------------------
#ifndef CAL_LC_OFFSET
#define CAL_LC_OFFSET 0.0f  // counts; "TARE" re-measures it at runtime
#endif
#ifndef CAL_LC_N_PER_COUNT
#define CAL_LC_N_PER_COUNT 1.0e-5f  // placeholder; measure with loadcell_test and a known mass
#endif

// ---- Output conditioning -----------------------------------------------------------------
#ifndef CAL_LEAD_TAU
#define CAL_LEAD_TAU 0.0f  // s; first-order lead to cancel rotor lag, 0 = off
#endif
#ifndef CAL_FILTER_KIND
#define CAL_FILTER_KIND 0.0f  // output smoother: 0 = Butterworth on lpf_hz, 1 = EMA on ema_alpha
#endif
#ifndef CAL_LPF_HZ
#define CAL_LPF_HZ 10.0f  // 2nd-order Butterworth cutoff on the output; >= 100 disables it
#endif
#ifndef CAL_EMA_ALPHA
#define CAL_EMA_ALPHA 1.0f  // EMA: y = alpha*x + (1-alpha)*y_prev, 0 < alpha <= 1; 1 = pass-through
#endif

// ---- Air temperature: which thermometer feeds the density, and each one's offset --------
// The thermometers are calibrated one by one and never combined. temp_source numbers them:
// 0 MCP9808, 1 SHT40, 2 STTS22H, 3 LPS22DF (die), 4 TMP36, 5 LM35. Offsets are in C, added
// to the raw reading (reference minus raw, from the dashboard's calibrate step).
#ifndef CAL_TEMP_SOURCE
#define CAL_TEMP_SOURCE 0.0f
#endif
#ifndef CAL_MCP9808_OFFSET
#define CAL_MCP9808_OFFSET 0.0f
#endif
#ifndef CAL_SHT40_OFFSET
#define CAL_SHT40_OFFSET 0.0f
#endif
#ifndef CAL_STTS22H_OFFSET
#define CAL_STTS22H_OFFSET 0.0f
#endif
#ifndef CAL_LPS22DF_OFFSET
#define CAL_LPS22DF_OFFSET 0.0f
#endif
#ifndef CAL_TMP36_OFFSET
#define CAL_TMP36_OFFSET 0.0f
#endif
#ifndef CAL_LM35_OFFSET
#define CAL_LM35_OFFSET 0.0f
#endif
// Pa; the density uses this constant whenever the LPS22DF barometer is not reading. Set it from the lab
// barometer or a nearby weather station's station pressure (not the sea-level figure).
#ifndef CAL_PRESSURE_REF_PA
#define CAL_PRESSURE_REF_PA 101325.0f
#endif

#include "env_sensors.h"
// The air-temperature settings above as EnvSensors takes them.
inline EnvCalibration default_env_calibration() {
  EnvCalibration c;
  c.source = static_cast<uint8_t>(CAL_TEMP_SOURCE);
  const float offsets[kNumThermometers] = {CAL_MCP9808_OFFSET, CAL_SHT40_OFFSET, CAL_STTS22H_OFFSET,
                                           CAL_LPS22DF_OFFSET, CAL_TMP36_OFFSET,  CAL_LM35_OFFSET};
  for (uint8_t i = 0; i < kNumThermometers; ++i) c.offset_c[i] = offsets[i];
  c.pressure_pa = CAL_PRESSURE_REF_PA;
  return c;
}
