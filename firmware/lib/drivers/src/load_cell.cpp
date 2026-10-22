#include "load_cell.h"

namespace {
// M5Stack Unit Weight I2C registers (M5Unit-WEIGHT, unit_WeightI2C.hpp). Little-endian.
constexpr uint8_t kRegRawAdc = 0x00;    // int32, unfiltered HX711 counts (the unit's filters only touch 0x10)
constexpr uint8_t kRegFirmware = 0xFE;  // non-zero once the unit has booted
constexpr uint32_t kWeightBootMs = 1500;
// NAU7802 registers (datasheet section 11). The SparkFun library is used only to set the chip up: its per-sample
// calls turn a failed I2C read into "data ready" (0xFF) and a reading of 0 or -1, which would pass as real force.
constexpr uint8_t kNauPuCtrl = 0x00;
constexpr uint8_t kNauCycleReady = 1 << 5;  // PU_CTRL.CR
constexpr uint8_t kNauAdcB2 = 0x12;         // ADCO_B2..B0, 24-bit two's complement, MSB first

bool ack(TwoWire& wire, uint8_t addr) {
  wire.beginTransmission(addr);
  return wire.endTransmission() == 0;
}

bool read_reg(TwoWire& wire, uint8_t addr, uint8_t reg, uint8_t* buf, uint8_t n) {
  wire.beginTransmission(addr);
  wire.write(reg);
  if (wire.endTransmission(false) != 0) return false;  // repeated start: the Weight unit needs it, the NAU7802 accepts it
  if (wire.requestFrom(addr, n) != n) return false;
  for (uint8_t i = 0; i < n; ++i) buf[i] = static_cast<uint8_t>(wire.read());
  return true;
}
}  // namespace

bool LoadCell::begin(TwoWire& wire, const LoadCellConfig& cfg, int32_t offset_counts, float newtons_per_count) {
  wire_ = &wire;
  cfg_ = cfg;
  offset_ = offset_counts;
  scale_ = newtons_per_count;
  // The Weight unit's MCU powers up with the Nucleo and needs a moment before it answers.
  uint32_t t0 = millis();
  while (!probe() && millis() - t0 < kWeightBootMs) delay(100);
  return present();
}

bool LoadCell::probe() {
  last_probe_ms_ = millis();
  bool weight = ack(*wire_, cfg_.addr_weight_i2c);
  bool nau = ack(*wire_, cfg_.addr_nau7802);
  bool first_weight = cfg_.prefer != LoadCellFrontend::kNau7802 || !nau;
  if (weight && first_weight && begin_weight_i2c()) return true;
  if (nau && begin_nau7802()) return true;
  if (weight && !first_weight && begin_weight_i2c()) return true;
  frontend_ = LoadCellFrontend::kNone;
  return false;
}

bool LoadCell::begin_weight_i2c() {
  uint8_t ver = 0;
  if (!read_reg(*wire_, cfg_.addr_weight_i2c, kRegFirmware, &ver, 1) || ver == 0) return false;
  int32_t v;
  if (!read_weight_raw(&v)) return false;
  frontend_ = LoadCellFrontend::kWeightI2c;
  last_weight_raw_ = v;
  fails_ = 0;
  accept(v);
  return true;
}

bool LoadCell::begin_nau7802() {
  bool ok = nau_.begin(*wire_) && nau_.setLDO(NAU7802_LDO_3V3) && nau_.setGain(NAU7802_GAIN_128) &&
            nau_.setSampleRate(NAU7802_SPS_320) && nau_.calibrateAFE();
  if (ok) {
    frontend_ = LoadCellFrontend::kNau7802;
    fails_ = 0;
  }
  return ok;
}

bool LoadCell::read_weight_raw(int32_t* out) {
  uint8_t b[4];
  if (!read_reg(*wire_, cfg_.addr_weight_i2c, kRegRawAdc, b, 4)) return false;
  *out = static_cast<int32_t>(static_cast<uint32_t>(b[0]) | static_cast<uint32_t>(b[1]) << 8 |
                              static_cast<uint32_t>(b[2]) << 16 | static_cast<uint32_t>(b[3]) << 24);
  return true;
}

bool LoadCell::note_fail(uint32_t now) {
  ++fail_total_;
  if (++fails_ >= kMaxFails) {
    frontend_ = LoadCellFrontend::kNone;
    last_probe_ms_ = now;
  }
  return false;  // the last good reading is kept; a failed read never becomes a sample
}

void LoadCell::accept(int32_t raw) {
  ++sample_total_;
  raw_ = raw;
  if (tare_left_ > 0) {
    tare_sum_ += raw_;
    if (--tare_left_ == 0) offset_ = static_cast<int32_t>(tare_sum_ / tare_total_);
  }
}

bool LoadCell::poll() {
  if (wire_ == nullptr) return false;
  uint32_t now = millis();
  if (frontend_ == LoadCellFrontend::kNone) {
    // Only the Weight unit is looked for while running: bringing up the NAU7802 blocks for its LDO ramp.
    if (now - last_probe_ms_ >= kReprobeMs) {
      last_probe_ms_ = now;
      if (ack(*wire_, cfg_.addr_weight_i2c)) return begin_weight_i2c();
    }
    return false;
  }
  if (frontend_ == LoadCellFrontend::kNau7802) {
    uint8_t st, b[3];
    if (!read_reg(*wire_, cfg_.addr_nau7802, kNauPuCtrl, &st, 1)) return note_fail(now);
    if (!(st & kNauCycleReady)) return false;
    if (!read_reg(*wire_, cfg_.addr_nau7802, kNauAdcB2, b, 3)) return note_fail(now);
    uint32_t u = static_cast<uint32_t>(b[0]) << 16 | static_cast<uint32_t>(b[1]) << 8 | b[2];
    if (u & 0x800000u) u |= 0xFF000000u;  // sign-extend 24 to 32 bits
    fails_ = 0;
    accept(static_cast<int32_t>(u));
    return true;
  }
  // Weight unit: no data-ready flag, so poll the raw register and take a changed value as a new conversion.
  if (now - last_read_ms_ < kWeightPollMs) return false;
  last_read_ms_ = now;
  int32_t v;
  if (!read_weight_raw(&v)) return note_fail(now);
  fails_ = 0;
  if (v == last_weight_raw_) return false;
  last_weight_raw_ = v;
  accept(v);
  return true;
}

void LoadCell::start_tare(uint16_t samples) {
  if (samples == 0) samples = static_cast<uint16_t>(max(1.0f, sample_hz() * 0.5f));
  tare_sum_ = 0;
  tare_total_ = samples;
  tare_left_ = samples;
}

const char* LoadCell::frontend_name() const {
  switch (frontend_) {
    case LoadCellFrontend::kWeightI2c: return "weight_i2c";
    case LoadCellFrontend::kNau7802: return "nau7802";
    default: return "none";
  }
}

float LoadCell::sample_hz() const {
  switch (frontend_) {
    case LoadCellFrontend::kWeightI2c: return 10.0f;
    case LoadCellFrontend::kNau7802: return 320.0f;
    default: return 0.0f;
  }
}
