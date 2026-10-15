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
// sqrt_k = 2 / (Cd * A)
#ifndef CAL_SQRT_K
#define CAL_SQRT_K 1.0f
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
