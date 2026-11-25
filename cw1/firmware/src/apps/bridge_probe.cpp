// Load cell bridge probe: finds a wiring fault without a multimeter.
//
// 1. The Nucleo's ADC on A0 is a voltmeter. Touch a jumper from A0 to each Qwiic Scale terminal in turn
//    (GND jumper to the Nucleo is shared through the Qwiic cable): a powered, wired bridge reads
//      E+ ~3.3 V, E- ~0 V, A+ and A- both ~1.65 V (half of E+).
//    A+ or A- at 0 V or 3.3 V means a broken bridge arm or a wire on the wrong terminal.
// 2. The NAU7802 reads channel 1 (the load cell) at gain 128 and 1, and channel 2 (nothing connected,
//    so it floats to full scale, about +-8388607). A real bridge sits thousands to hundreds of thousands
//    of counts off zero at gain 128. Channel 1 within about +-2000 of zero at both gains means A+ and A-
//    are at the same voltage: shorted together, or the bridge has no excitation on E+.
// 3. "live" streams channel 1 at gain 128 with the change since start: press the cell and it must move
//    by thousands of counts.
// 4. Direct cell test, no amplifier: cell E+ to 3V3, E- to GND, A+ to A0, A- to A1. A live, intact bridge reads
//    A0 and A1 both ~1.65 V, and "diff" (A0 - A1, averaged over 1024 reads, ~0.05 mV resolution) moves by a
//    fraction of a mV when the free end is pressed. Both at 0 V or 3.3 V, or wandering: a broken cell or wire.
//
//   s   re-run the survey (steps 1-2)
// Lines start with "probe" so the dashboard's log shows them as they are.
#include <Arduino.h>
#include <Wire.h>
#include <SparkFun_Qwiic_Scale_NAU7802_Arduino_Library.h>

#include "board_config.h"

namespace {
NAU7802 g_adc;
bool g_ok = false;
int32_t g_start = 0;

float volts(uint32_t pin, int n) {
  uint32_t sum = 0;
  for (int i = 0; i < n; ++i) sum += analogRead(pin);
  return sum / static_cast<float>(n) / 4095.0f * 3.3f;  // VDDA is 3.3 V on the Nucleo; good to a few percent
}

float probe_volts() { return volts(board::kPinProbe, 32); }

/** A0 - A1 in mV, interleaved so slow drift in VDDA cancels. */
float diff_mv() {
  int64_t sum = 0;
  for (int i = 0; i < 1024; ++i) sum += static_cast<int32_t>(analogRead(board::kPinProbe)) - analogRead(board::kPinProbe2);
  return sum / 1024.0f / 4095.0f * 3300.0f;
}

/** Mean of the next n conversions with the current channel and gain. */
bool read_n(uint8_t n, int32_t* out) {
  int64_t sum = 0;
  for (uint8_t i = 0; i < n; ++i) {
    uint32_t t0 = millis();
    while (!g_adc.available()) {
      if (millis() - t0 > 200) return false;
    }
    sum += g_adc.getReading();
  }
  *out = static_cast<int32_t>(sum / n);
  return true;
}

/** Mean of n conversions on the given channel and gain, after the AFE recalibrates for them. */
bool measure(uint8_t channel, uint8_t gain, uint8_t n, int32_t* out) {
  if (!g_adc.setChannel(channel) || !g_adc.setGain(gain) || !g_adc.calibrateAFE()) return false;
  int32_t skip;
  read_n(3, &skip);  // drop the first conversions after a switch
  return read_n(n, out);
}

void line(const char* label, uint8_t ch, uint8_t gain) {
  int32_t v;
  Serial.print("probe   ");
  Serial.print(label);
  if (measure(ch, gain, 16, &v)) Serial.println(v);
  else Serial.println("no conversions");
}

void regs(const char* when) {
  const uint8_t r[] = {0x00, 0x01, 0x02, 0x15, 0x1B, 0x1C, 0x1F};
  Serial.print("probe   regs ");
  Serial.print(when);
  for (uint8_t a : r) {
    Serial.print(' ');
    Serial.print(a, HEX);
    Serial.print('=');
    Serial.print(g_adc.getRegister(a), HEX);
  }
  Serial.println();
}

void survey() {
  Serial.println("probe ---- survey (s to repeat) ----");
  regs("before");
  Serial.print("probe   calibrateAFE ");
  Serial.println(g_adc.calibrateAFE() ? "ok" : "FAILED");
  regs("after cal");
  Serial.print("probe   A0 jumper            ");
  Serial.print(probe_volts(), 3);
  Serial.println(" V   (E+ ~3.3, E- ~0, A+ and A- ~1.65 each)");
  if (!g_ok) {
    Serial.println("probe   NAU7802 not found at 0x2A: check the Qwiic cable");
    return;
  }
  line("ch1 load cell, gain 128    ", NAU7802_CHANNEL_1, NAU7802_GAIN_128);
  line("ch2 nothing,   gain 128    ", NAU7802_CHANNEL_2, NAU7802_GAIN_128);
  line("ch1 load cell, gain 1      ", NAU7802_CHANNEL_1, NAU7802_GAIN_1);
  Serial.println("probe   ch2 floats near +-8388607. ch1 within ~+-2000 of 0 at both gains: A+ = A- (shorted, or no E+)");
  measure(NAU7802_CHANNEL_1, NAU7802_GAIN_128, 16, &g_start);
}
}  // namespace

void setup() {
  Serial.begin(board::kSerialBaud);
  analogReadResolution(12);
  Wire.begin();
  Wire.setClock(board::kI2cClockHz);
  g_ok = g_adc.begin(Wire);
  Serial.print("probe   begin ");
  Serial.println(g_ok ? "ok" : "FAILED");
  if (!g_ok) g_ok = g_adc.isConnected();  // begin() also fails when only the AFE calibration fails
  if (g_ok) {
    g_adc.setLDO(NAU7802_LDO_3V3);
    g_adc.setSampleRate(NAU7802_SPS_80);
  }
  delay(300);
  survey();
}

void loop() {
  static uint32_t last = 0;
  static char buf[16];
  static uint8_t used = 0;
  while (Serial.available()) {
    char c = static_cast<char>(Serial.read());
    if (c == '\n' || c == '\r') {
      buf[used] = 0;
      if (used == 1 && buf[0] == 's') survey();
      used = 0;
    } else if (used < sizeof buf - 1) {
      buf[used++] = c;
    }
  }
  if (millis() - last < 500) return;
  last = millis();
  Serial.print("probe live  A0 ");
  Serial.print(probe_volts(), 3);
  Serial.print(" V  A1 ");
  Serial.print(volts(board::kPinProbe2, 32), 3);
  Serial.print(" V  diff ");
  Serial.print(diff_mv(), 3);
  Serial.print(" mV");
  int32_t v;
  if (g_ok && read_n(8, &v)) {  // the survey left channel 1 at gain 128
    Serial.print("  ch1 ");
    Serial.print(v);
    Serial.print("  change ");
    Serial.println(v - g_start);
  } else {
    Serial.println("  ch1 no conversions");
  }
}
