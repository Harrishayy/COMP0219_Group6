// IMU + magnetometer stream from the IKS4A1 (LSM6DSO16IS, LIS2MDL) at 100 Hz.
// Useful for vibration-based designs (e.g. plate flutter, rotor imbalance frequency)
// or sensing a rotor magnet with the magnetometer instead of the Hall switch.
// Columns: t_ms, ax, ay, az [mg], gx, gy, gz [mdps], mx, my, mz [mGauss]
#include <Arduino.h>
#include <LIS2MDLSensor.h>
#include <LSM6DSO16ISSensor.h>
#include <Wire.h>

#include "board_config.h"

namespace {
LSM6DSO16ISSensor g_imu(&Wire);
LIS2MDLSensor g_mag(&Wire);
bool g_imu_ok = false;
bool g_mag_ok = false;

void print3(const int32_t v[3]) {
  for (int i = 0; i < 3; ++i) {
    Serial.print(',');
    Serial.print(v[i]);
  }
}
}  // namespace

void setup() {
  Serial.begin(board::kSerialBaud);
  Wire.begin();
  Wire.setClock(board::kI2cClockHz);

  g_imu_ok = g_imu.begin() == LSM6DSO16IS_STATUS_OK && g_imu.Enable_X() == LSM6DSO16IS_STATUS_OK &&
             g_imu.Enable_G() == LSM6DSO16IS_STATUS_OK;
  if (g_imu_ok) {
    g_imu.Set_X_ODR(208.0f);
    g_imu.Set_G_ODR(208.0f);
  }
  g_mag_ok = g_mag.begin() == LIS2MDL_OK && g_mag.Enable() == LIS2MDL_OK;
  if (g_mag_ok) g_mag.SetOutputDataRate(100.0f);

  Serial.println(g_imu_ok ? "# LSM6DSO16IS ok" : "# LSM6DSO16IS NOT FOUND");
  Serial.println(g_mag_ok ? "# LIS2MDL ok" : "# LIS2MDL NOT FOUND");
  Serial.println("#cols,t_ms,ax,ay,az,gx,gy,gz,mx,my,mz");
}

void loop() {
  static uint32_t last = 0;
  if (millis() - last < 10) return;
  last = millis();
  int32_t acc[3] = {0, 0, 0}, gyr[3] = {0, 0, 0}, mag[3] = {0, 0, 0};
  if (g_imu_ok) {
    g_imu.Get_X_Axes(acc);
    g_imu.Get_G_Axes(gyr);
  }
  if (g_mag_ok) g_mag.GetAxes(mag);
  Serial.print(last);
  print3(acc);
  print3(gyr);
  print3(mag);
  Serial.println();
}
