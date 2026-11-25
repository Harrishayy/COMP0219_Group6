// ADC helpers: 12-bit reads scaled by the measured supply (VDDA), not an assumed 3.3 V.
#pragma once

#include <Arduino.h>

namespace analog_in {

// Sets 12-bit resolution and takes a first VDDA measurement.
void begin();

// Re-measures VDDA from the internal reference (VREFINT, factory-calibrated).
// Falls back to 3.3 V on parts without the calibration value.
float measure_vdda();

// Last measured VDDA [V].
float vdda();

// Mean of `oversample` conversions, in volts.
float read_volts(uint32_t pin, uint8_t oversample = 8);

}  // namespace analog_in
