#include "analog_in.h"

#include "stm32yyxx_ll_adc.h"

namespace analog_in {

namespace {
constexpr uint32_t kFullScale = 4095;
float g_vdda = 3.3f;
}  // namespace

void begin() {
  analogReadResolution(12);
  measure_vdda();
}

float measure_vdda() {
#if defined(AVREF) && defined(__LL_ADC_CALC_VREFANALOG_VOLTAGE)
  uint32_t sum = 0;
  for (int i = 0; i < 8; ++i) sum += analogRead(AVREF);
  uint32_t raw = sum / 8;
  if (raw > 0) {
    g_vdda = __LL_ADC_CALC_VREFANALOG_VOLTAGE(raw, LL_ADC_RESOLUTION_12B) / 1000.0f;
  }
#endif
  return g_vdda;
}

float vdda() { return g_vdda; }

float read_volts(uint32_t pin, uint8_t oversample) {
  if (oversample == 0) oversample = 1;
  uint32_t sum = 0;
  for (uint8_t i = 0; i < oversample; ++i) sum += analogRead(pin);
  return static_cast<float>(sum) / oversample * g_vdda / kFullScale;
}

}  // namespace analog_in
