#include "mcp9808.h"

namespace {
constexpr uint8_t kRegAmbient = 0x05;
constexpr uint8_t kRegManufacturerId = 0x06;
constexpr uint8_t kRegResolution = 0x08;
constexpr uint16_t kManufacturerId = 0x0054;
constexpr uint8_t kResolution0p0625 = 0x03;
}  // namespace

bool Mcp9808::begin(TwoWire& wire, uint8_t address) {
  wire_ = &wire;
  address_ = address;
  uint16_t id = 0;
  present_ = read_reg16(kRegManufacturerId, id) && id == kManufacturerId;
  if (present_) write_reg8(kRegResolution, kResolution0p0625);
  return present_;
}

bool Mcp9808::read(float& temp_c) {
  uint16_t raw = 0;
  if (!present_ || !read_reg16(kRegAmbient, raw)) return false;
  // Bits 12..0: 13-bit two's complement in 1/16 C. Bits 15..13 are alert flags.
  int32_t t = raw & 0x1FFF;
  if (t & 0x1000) t -= 0x2000;
  temp_c = static_cast<float>(t) / 16.0f;
  return true;
}

bool Mcp9808::read_reg16(uint8_t reg, uint16_t& value) {
  wire_->beginTransmission(address_);
  wire_->write(reg);
  if (wire_->endTransmission(false) != 0) return false;
  if (wire_->requestFrom(address_, static_cast<uint8_t>(2)) != 2) return false;
  uint8_t hi = wire_->read();
  uint8_t lo = wire_->read();
  value = static_cast<uint16_t>(hi << 8 | lo);
  return true;
}

bool Mcp9808::write_reg8(uint8_t reg, uint8_t value) {
  wire_->beginTransmission(address_);
  wire_->write(reg);
  wire_->write(value);
  return wire_->endTransmission() == 0;
}
