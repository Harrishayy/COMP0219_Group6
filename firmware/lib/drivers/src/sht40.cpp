#include "sht40.h"

namespace {
constexpr uint8_t kCmdMeasureHighPrecision = 0xFD;
constexpr uint8_t kCmdSoftReset = 0x94;
constexpr uint8_t kCmdReadSerial = 0x89;
constexpr uint32_t kMeasureTimeMs = 10;  // datasheet max 8.3 ms

uint8_t crc8(const uint8_t* data, size_t len) {
  uint8_t crc = 0xFF;
  for (size_t i = 0; i < len; ++i) {
    crc ^= data[i];
    for (int b = 0; b < 8; ++b) crc = (crc & 0x80) ? static_cast<uint8_t>(crc << 1 ^ 0x31) : crc << 1;
  }
  return crc;
}

bool send_command(TwoWire& wire, uint8_t address, uint8_t cmd) {
  wire.beginTransmission(address);
  wire.write(cmd);
  return wire.endTransmission() == 0;
}

bool read_words(TwoWire& wire, uint8_t address, uint8_t out[6]) {
  if (wire.requestFrom(address, static_cast<uint8_t>(6)) != 6) return false;
  for (int i = 0; i < 6; ++i) out[i] = wire.read();
  return crc8(out, 2) == out[2] && crc8(out + 3, 2) == out[5];
}
}  // namespace

bool Sht40::begin(TwoWire& wire, uint8_t address) {
  wire_ = &wire;
  address_ = address;
  present_ = false;
  if (!send_command(wire, address, kCmdSoftReset)) return false;
  delay(2);
  uint8_t buf[6];
  if (!send_command(wire, address, kCmdReadSerial)) return false;
  delay(2);
  present_ = read_words(wire, address, buf);
  return present_;
}

bool Sht40::poll(uint32_t now_ms, uint32_t period_ms) {
  if (!present_) return false;
  if (busy_) {
    if (now_ms - started_ms_ < kMeasureTimeMs) return false;
    busy_ = false;
    return fetch();
  }
  if (now_ms - last_start_ms_ >= period_ms || last_start_ms_ == 0) {
    if (send_command(*wire_, address_, kCmdMeasureHighPrecision)) {
      busy_ = true;
      started_ms_ = now_ms;
      last_start_ms_ = now_ms == 0 ? 1 : now_ms;
    }
  }
  return false;
}

bool Sht40::fetch() {
  uint8_t buf[6];
  if (!read_words(*wire_, address_, buf)) return false;
  uint16_t t_ticks = static_cast<uint16_t>(buf[0] << 8 | buf[1]);
  uint16_t rh_ticks = static_cast<uint16_t>(buf[3] << 8 | buf[4]);
  temp_c_ = -45.0f + 175.0f * t_ticks / 65535.0f;
  float rh = -6.0f + 125.0f * rh_ticks / 65535.0f;
  rh_pct_ = rh < 0.0f ? 0.0f : (rh > 100.0f ? 100.0f : rh);
  return true;
}
