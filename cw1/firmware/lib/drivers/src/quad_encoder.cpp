#include "quad_encoder.h"

void QuadEncoder::begin(uint8_t input_filter, bool invert) {
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_TIM2_CLK_ENABLE();

  GPIO_InitTypeDef gpio = {};
  gpio.Pin = GPIO_PIN_0 | GPIO_PIN_1;
  gpio.Mode = GPIO_MODE_AF_PP;
  gpio.Pull = GPIO_PULLUP;
  gpio.Speed = GPIO_SPEED_FREQ_LOW;
  gpio.Alternate = GPIO_AF1_TIM2;
  HAL_GPIO_Init(GPIOA, &gpio);

  uint32_t f = input_filter & 0xFu;
  TIM2->CR1 = 0;
  TIM2->SMCR = TIM_SMCR_SMS_0 | TIM_SMCR_SMS_1;  // encoder mode 3: count on TI1 and TI2 edges
  TIM2->CCMR1 = TIM_CCMR1_CC1S_0 | TIM_CCMR1_CC2S_0 | (f << TIM_CCMR1_IC1F_Pos) |
                (f << TIM_CCMR1_IC2F_Pos);
  TIM2->CCER = invert ? TIM_CCER_CC1P : 0;
  TIM2->ARR = 0xFFFFFFFFu;
  TIM2->CNT = 0;
  TIM2->CR1 = TIM_CR1_CEN;
}
