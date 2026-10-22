/* Standalone STM32 HAL lab firmware. USART2: PA2/PA3, 115200 8N1.
 * Starts with raw ADC counts; t tares, k <grams> calibrates, h prints help.
 * Calibration is deliberately interactive: never invent a known mass.
 */
#include "main.h"
#include "nau7802.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

I2C_HandleTypeDef hi2c1;
UART_HandleTypeDef huart2;
static uint32_t i2c_pins = GPIO_PIN_8 | GPIO_PIN_9;
static bool calibrated;
static bool tared;
static uint8_t rx_byte;
static volatile uint8_t rx_head, rx_tail;
static uint8_t rx_ring[64];

void USART2_IRQHandler(void) { HAL_UART_IRQHandler(&huart2); }
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *h) {
  if (h->Instance != USART2) return;
  uint8_t next = (rx_head + 1) % sizeof rx_ring;
  if (next != rx_tail) { rx_ring[rx_head] = rx_byte; rx_head = next; }
  HAL_UART_Receive_IT(h, &rx_byte, 1);
}
void HAL_UART_ErrorCallback(UART_HandleTypeDef *h) {
  if (h->Instance == USART2) HAL_UART_Receive_IT(h, &rx_byte, 1);
}

void SysTick_Handler(void) { HAL_IncTick(); }

static void say(const char *s) {
  HAL_UART_Transmit(&huart2, (uint8_t *)s, (uint16_t)strlen(s), 200);
}

void Error_Handler(void) {
  for (;;) {
    HAL_GPIO_TogglePin(GPIOA, GPIO_PIN_5);
    for (volatile unsigned i = 0; i < 500000; ++i) {}
  }
}

void HAL_UART_MspInit(UART_HandleTypeDef *h) {
  if (h->Instance != USART2) return;
  __HAL_RCC_USART2_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  GPIO_InitTypeDef g = {0};
  g.Pin = GPIO_PIN_2 | GPIO_PIN_3;
  g.Mode = GPIO_MODE_AF_PP;
  g.Pull = GPIO_PULLUP;
  g.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  g.Alternate = GPIO_AF7_USART2;
  HAL_GPIO_Init(GPIOA, &g);
  HAL_NVIC_SetPriority(USART2_IRQn, 5, 0);
  HAL_NVIC_EnableIRQ(USART2_IRQn);
}

void HAL_I2C_MspInit(I2C_HandleTypeDef *h) {
  if (h->Instance != I2C1) return;
  __HAL_RCC_GPIOB_CLK_ENABLE();
  __HAL_RCC_I2C1_CLK_ENABLE();
  GPIO_InitTypeDef g = {0};
  g.Pin = i2c_pins;
  g.Mode = GPIO_MODE_AF_OD;
  g.Pull = GPIO_PULLUP;
  g.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  g.Alternate = GPIO_AF4_I2C1;
  HAL_GPIO_Init(GPIOB, &g);
}

void HAL_I2C_MspDeInit(I2C_HandleTypeDef *h) {
  if (h->Instance != I2C1) return;
  HAL_GPIO_DeInit(GPIOB, i2c_pins);
  __HAL_RCC_I2C1_FORCE_RESET();
  __HAL_RCC_I2C1_RELEASE_RESET();
  __HAL_RCC_I2C1_CLK_DISABLE();
}

static void i2c_init(void) {
  hi2c1.Instance = I2C1;
  hi2c1.Init.ClockSpeed = 100000;
  hi2c1.Init.DutyCycle = I2C_DUTYCYCLE_2;
  hi2c1.Init.AddressingMode = I2C_ADDRESSINGMODE_7BIT;
  if (HAL_I2C_Init(&hi2c1) != HAL_OK) Error_Handler();
}

static bool find_sensor(void) {
  const uint32_t pins[] = {GPIO_PIN_8 | GPIO_PIN_9, GPIO_PIN_6 | GPIO_PIN_7};
  for (unsigned p = 0; p < 2; ++p) {
    HAL_I2C_DeInit(&hi2c1);
    i2c_pins = pins[p];
    i2c_init();
    if (NAU7802_isConnected(&hi2c1)) {
      say(p == 0 ? "I2C1: NAU7802 at 0x2A on PB8(SCL)/PB9(SDA)\r\n"
                 : "I2C1: NAU7802 at 0x2A on PB6(SCL)/PB7(SDA)\r\n");
      return NAU7802_begin(&hi2c1);
    }
  }
  return false;
}

static bool average(int32_t *out) {
  int64_t total = 0;
  unsigned count = 0;
  uint32_t start = HAL_GetTick();
  while (count < 16 && HAL_GetTick() - start < 1500) {
    if (NAU7802_available(&hi2c1)) {
      uint8_t b[3];
      if (HAL_I2C_Mem_Read(&hi2c1, NAU7802_HAL_ADDR_8BIT, NAU7802_ADCO_B2,
                          I2C_MEMADD_SIZE_8BIT, b, 3, 100) != HAL_OK) return false;
      uint32_t v = ((uint32_t)b[0] << 16) | ((uint32_t)b[1] << 8) | b[2];
      if (v & 0x800000) v |= 0xff000000;
      total += (int32_t)v;
      ++count;
    } else HAL_Delay(1);
  }
  if (count != 16) return false;
  *out = (int32_t)(total / count);
  return true;
}

static void command(char *line) {
  int32_t raw;
  char buf[128];
  if (strcmp(line, "t") == 0) {
    if (!average(&raw)) { say("ERROR: no ADC samples; tare unchanged\r\n"); return; }
    NAU7802_setZeroOffset(raw);
    tared = true;
    snprintf(buf, sizeof buf, "Tare: %ld counts\r\n", (long)raw);
    say(buf);
  } else if (line[0] == 'k' && line[1] == ' ') {
    if (!tared) { say("First remove all load and send t to tare\r\n"); return; }
    char *end;
    float grams = strtof(line + 2, &end);
    while (*end == ' ') ++end;
    if (*end || !isfinite(grams) || grams <= 0) { say("Use k <positive known mass in grams>\r\n"); return; }
    if (!average(&raw)) { say("ERROR: no ADC samples; calibration unchanged\r\n"); return; }
    int32_t delta = raw - NAU7802_getZeroOffset();
    if (labs(delta) < 100) { say("ERROR: load change too small; tare unloaded, then add known mass\r\n"); return; }
    NAU7802_setCalibrationFactor((float)delta / grams);
    calibrated = true;
    snprintf(buf, sizeof buf, "Calibration: %.6f counts/g\r\n", (double)NAU7802_getCalibrationFactor());
    say(buf);
  } else {
    say("Commands: t = tare with no load; k 249 = calibrate WITH 249 g on scale; h = help. End with CR or LF.\r\n");
    say("Calibration lives in RAM and is lost on reset. Until calibrated, output is raw counts.\r\n");
    snprintf(buf, sizeof buf, "Scale factor: %.6f counts/g; tared=%u; calibrated=%u\r\n", (double)NAU7802_getCalibrationFactor(), tared, calibrated);
    say(buf);
  }
}

int main(void) {
  HAL_Init();
  /* Use the internal 16 MHz HSI: no dependency on an external clock jumper. */
  SystemCoreClockUpdate();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  GPIO_InitTypeDef led = {0};
  led.Pin = GPIO_PIN_5; led.Mode = GPIO_MODE_OUTPUT_PP;
  HAL_GPIO_Init(GPIOA, &led);
  huart2.Instance = USART2;
  huart2.Init.BaudRate = 115200;
  huart2.Init.WordLength = UART_WORDLENGTH_8B;
  huart2.Init.StopBits = UART_STOPBITS_1;
  huart2.Init.Parity = UART_PARITY_NONE;
  huart2.Init.Mode = UART_MODE_TX_RX;
  huart2.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart2.Init.OverSampling = UART_OVERSAMPLING_16;
  if (HAL_UART_Init(&huart2) != HAL_OK) Error_Handler();
  HAL_UART_Receive_IT(&huart2, &rx_byte, 1);
  say("\r\nLAB2 load cell: firmware running; USART2 115200 8N1, flow control OFF\r\n");
  i2c_init();
  bool ready = false;
  uint32_t last_retry = HAL_GetTick() - 3000, last_print = HAL_GetTick();
  char line[48]; unsigned used = 0;
  for (;;) {
    uint8_t ch;
    if (rx_tail != rx_head) {
      ch = rx_ring[rx_tail];
      rx_tail = (rx_tail + 1) % sizeof rx_ring;
      if (ch == '\r' || ch == '\n') {
        if (used) { line[used] = 0; if (ready) command(line); else say("ERROR: sensor not ready\r\n"); }
        used = 0;
      } else if (used < sizeof line - 1) line[used++] = (char)ch;
    }
    if (!ready && HAL_GetTick() - last_retry >= 3000) {
      last_retry = HAL_GetTick();
      ready = find_sensor();
      if (ready) { say("NAU7802 initialized. RAW counts (uncalibrated).\r\n"); command("h"); }
      else say("ERROR: NAU7802 not ready. Check 3.3V/GND/SCL/SDA; retrying.\r\n");
    }
    if (ready && HAL_GetTick() - last_print >= 500) {
      last_print = HAL_GetTick();
      int32_t raw;
      char buf[128];
      if (!average(&raw)) {
        say("ERROR: ADC read timed out; reconnecting sensor\r\n"); ready = false;
        tared = calibrated = false;
      } else {
        if (calibrated) snprintf(buf, sizeof buf, "Weight: %.2f g  Raw: %ld\r\n", (double)((raw - NAU7802_getZeroOffset()) / NAU7802_getCalibrationFactor()), (long)raw);
        else snprintf(buf, sizeof buf, "Raw: %ld counts (uncalibrated)\r\n", (long)raw);
        say(buf);
        HAL_GPIO_TogglePin(GPIOA, GPIO_PIN_5);
      }
    }
  }
}
