#include "load_cell.h"

bool LoadCell::begin(TwoWire& wire, int32_t offset_counts, float newtons_per_count) {
  offset_ = offset_counts;
  scale_ = newtons_per_count;
  present_ = adc_.begin(wire) && adc_.setLDO(NAU7802_LDO_3V3) && adc_.setGain(NAU7802_GAIN_128) &&
             adc_.setSampleRate(NAU7802_SPS_320) && adc_.calibrateAFE();
  return present_;
}

bool LoadCell::poll() {
  if (!present_ || !adc_.available()) return false;
  raw_ = adc_.getReading();
  if (tare_left_ > 0) {
    tare_sum_ += raw_;
    if (--tare_left_ == 0) offset_ = static_cast<int32_t>(tare_sum_ / tare_total_);
  }
  return true;
}

void LoadCell::start_tare(uint16_t samples) {
  if (samples == 0) samples = 1;
  tare_sum_ = 0;
  tare_total_ = samples;
  tare_left_ = samples;
}
