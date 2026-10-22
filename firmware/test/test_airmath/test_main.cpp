// Host-side tests for lib/airmath: pio test -e native
#include <unity.h>

#include <cmath>

#include "air_density.h"
#include "airspeed_models.h"
#include "filters.h"
#include "rate_estimator.h"

using namespace airmath;

void setUp() {}
void tearDown() {}

void test_isa_sea_level_density() {
  TEST_ASSERT_FLOAT_WITHIN(0.0005f, 1.2250f, dry_air_density(15.0f, 101325.0f));
}

void test_saturation_vapour_pressure() {
  // Reference values (WMO tables): 2339 Pa at 20 C, 611 Pa at 0 C.
  TEST_ASSERT_FLOAT_WITHIN(15.0f, 2339.0f, saturation_vapour_pressure_pa(20.0f));
  TEST_ASSERT_FLOAT_WITHIN(2.0f, 611.2f, saturation_vapour_pressure_pa(0.0f));
}

void test_humid_air_is_lighter() {
  float dry = humid_air_density(25.0f, 101325.0f, 0.0f);
  float wet = humid_air_density(25.0f, 101325.0f, 100.0f);
  TEST_ASSERT_FLOAT_WITHIN(1e-5f, dry_air_density(25.0f, 101325.0f), dry);
  TEST_ASSERT_TRUE(wet < dry);
  TEST_ASSERT_FLOAT_WITHIN(0.002f, 1.1684f, wet);  // engineering-toolbox value at 25 C, 100% RH
}

void test_air_density_fallbacks() {
  TEST_ASSERT_FLOAT_WITHIN(0.0005f, 1.225f, air_density(NAN, NAN, NAN));
  TEST_ASSERT_FLOAT_WITHIN(1e-5f, dry_air_density(20.0f, 100000.0f), air_density(20.0f, 100000.0f, NAN));
}

void test_speed_models() {
  TEST_ASSERT_FLOAT_WITHIN(1e-6f, 0.0f, speed_linear(0.01f, 2.0f, 0.5f, 0.05f));
  TEST_ASSERT_FLOAT_WITHIN(1e-6f, 6.5f, speed_linear(3.0f, 2.0f, 0.5f, 0.05f));
  // Pitot: q = 0.5 rho v^2 -> v = sqrt(2q/rho)
  float rho = 1.2f, v = 7.0f, q = 0.5f * rho * v * v;
  TEST_ASSERT_FLOAT_WITHIN(1e-4f, v, speed_sqrt_density(q, 2.0f, 0.0f, rho));
  TEST_ASSERT_FLOAT_WITHIN(1e-6f, 0.0f, speed_sqrt_density(-1.0f, 2.0f, 0.0f, rho));
  TEST_ASSERT_FLOAT_WITHIN(1e-6f, 0.0f, speed_sqrt_density(1.0f, 2.0f, 0.0f, 0.0f));
}

void test_butterworth_dc_and_attenuation() {
  ButterworthLowPass lpf(10.0f, 200.0f);
  float y = 0;
  for (int i = 0; i < 400; ++i) y = lpf.update(3.0f);
  TEST_ASSERT_FLOAT_WITHIN(1e-4f, 3.0f, y);  // unity DC gain, primed: no transient

  ButterworthLowPass lpf2(10.0f, 200.0f);
  float peak = 0;
  for (int i = 0; i < 400; ++i) {
    float out = lpf2.update(std::sin(2.0f * 3.14159265f * 50.0f * i / 200.0f));
    if (i > 200 && std::fabs(out) > peak) peak = std::fabs(out);
  }
  TEST_ASSERT_TRUE(peak < 0.06f);  // 50 Hz is ~2.3 octaves above 10 Hz: about -28 dB
}

void test_lead_compensator() {
  LeadCompensator lead;
  lead.configure(0.1f, 200.0f);  // gain 20
  TEST_ASSERT_FLOAT_WITHIN(1e-6f, 1.0f, lead.update(1.0f));
  TEST_ASSERT_FLOAT_WITHIN(1e-5f, 1.1f + 20.0f * 0.1f, lead.update(1.1f));
  LeadCompensator off;
  off.configure(0.0f, 200.0f);
  off.update(1.0f);
  TEST_ASSERT_FLOAT_WITHIN(1e-6f, 5.0f, off.update(5.0f));
}

void test_median3_removes_spike() {
  Median3 m;
  m.update(1.0f);
  m.update(1.0f);
  m.update(1.0f);
  TEST_ASSERT_FLOAT_WITHIN(1e-6f, 1.0f, m.update(50.0f));
  TEST_ASSERT_FLOAT_WITHIN(1e-6f, 1.0f, m.update(1.0f));
}

void test_rate_estimator_with_wrap() {
  RateEstimator<16> r;
  uint32_t t = 0xFFFFFFFFu - 12000;  // micros() about to wrap
  int32_t c = 0x7FFFFFF0;            // count about to wrap
  for (int i = 0; i < 10; ++i) {
    r.push(t, c);
    t += 5000;
    c += 50;  // 50 counts / 5 ms = 10000 counts/s
  }
  TEST_ASSERT_FLOAT_WITHIN(1.0f, 10000.0f, r.rate(20000));
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_isa_sea_level_density);
  RUN_TEST(test_saturation_vapour_pressure);
  RUN_TEST(test_humid_air_is_lighter);
  RUN_TEST(test_air_density_fallbacks);
  RUN_TEST(test_speed_models);
  RUN_TEST(test_butterworth_dc_and_attenuation);
  RUN_TEST(test_lead_compensator);
  RUN_TEST(test_median3_removes_spike);
  RUN_TEST(test_rate_estimator_with_wrap);
  return UNITY_END();
}
