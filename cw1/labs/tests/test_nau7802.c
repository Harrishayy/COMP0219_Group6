/* Host regression test. No target board or STM32 HAL dependency.
 * Run from repo root:
 * cc -std=c11 -Wall -Wextra -Werror -Ilabs/tests -Ilabs \
 *    labs/nau7802.c labs/tests/test_nau7802.c -o /tmp/test_nau7802 && /tmp/test_nau7802
 */
#include "nau7802.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static uint8_t registers[32];
static uint32_t ticks;
static unsigned writes, data_reads, calibration_polls;
static int fail_read_reg, fail_write_reg;
static bool ready, auto_power, auto_calibration, calibration_error;

static void reset_mock(void)
{
    memset(registers, 0, sizeof(registers));
    ticks = writes = data_reads = calibration_polls = 0;
    fail_read_reg = fail_write_reg = -1;
    ready = auto_power = auto_calibration = true;
    calibration_error = false;
}

HAL_StatusTypeDef HAL_I2C_IsDeviceReady(I2C_HandleTypeDef *h, uint16_t addr,
                                      uint32_t trials, uint32_t timeout)
{
    (void)h; (void)trials; (void)timeout;
    assert(addr == 0x54);
    return ready ? HAL_OK : HAL_ERROR;
}

HAL_StatusTypeDef HAL_I2C_Mem_Read(I2C_HandleTypeDef *h, uint16_t addr,
                                 uint16_t reg, uint16_t size, uint8_t *out,
                                 uint16_t count, uint32_t timeout)
{
    (void)h; (void)timeout;
    assert(addr == 0x54 && size == I2C_MEMADD_SIZE_8BIT);
    assert(reg + count <= sizeof(registers));
    if ((int)reg == fail_read_reg) return HAL_ERROR;
    if (reg == NAU7802_CTRL2 && (registers[reg] & 0x04) && auto_calibration)
    {
        if (++calibration_polls >= 3)
        {
            registers[reg] &= (uint8_t)~0x04;
            if (calibration_error) registers[reg] |= 0x08;
        }
    }
    memcpy(out, &registers[reg], count);
    if (reg == NAU7802_ADCO_B2) ++data_reads;
    return HAL_OK;
}

HAL_StatusTypeDef HAL_I2C_Mem_Write(I2C_HandleTypeDef *h, uint16_t addr,
                                  uint16_t reg, uint16_t size, uint8_t *value,
                                  uint16_t count, uint32_t timeout)
{
    (void)h; (void)timeout;
    assert(addr == 0x54 && size == I2C_MEMADD_SIZE_8BIT && count == 1);
    if ((int)reg == fail_write_reg) return HAL_ERROR;
    ++writes;
    if (reg == NAU7802_PU_CTRL && (*value & 1))
        memset(registers, 0, sizeof(registers));
    registers[reg] = *value;
    if (reg == NAU7802_PU_CTRL && auto_power && (*value & 0x06) == 0x06)
        registers[reg] |= 0x08;
    return HAL_OK;
}

void HAL_Delay(uint32_t delay) { ticks += delay; }
uint32_t HAL_GetTick(void) { return ticks; }

int main(void)
{
    I2C_HandleTypeDef bus = {0};
    reset_mock();
    assert(NAU7802_begin(&bus));
    /* Actual datasheet bit layout, independent of the driver constants. */
    assert(registers[0x01] == 0x27); /* VLDO=100 in5:3, gain128. */
    assert(registers[0x02] == 0x30); /* 80SPS, calibration complete, channel1. */
    assert(registers[0x15] == 0x30);
    assert(registers[0x1C] == 0x80); /* Not the PGA current bit0. */
    assert((registers[0] & 0x96) == 0x96);
    assert(calibration_polls >= 3);

    reset_mock();
    registers[1] = 0xFF;
    assert(NAU7802_setLDO(&bus, 4));
    assert(registers[1] == 0xE7); /* Preserve CRP, DRDY and gain. */
    assert(!NAU7802_setSampleRate(&bus, 4)); /* Reserved code. */

    reset_mock();
    fail_read_reg = 0;
    assert(!NAU7802_available(&bus));
    assert(!NAU7802_setBit(&bus, 4, 0));
    assert(!NAU7802_clearBit(&bus, 4, 0));
    assert(!NAU7802_powerUp(&bus));
    assert(!NAU7802_begin(&bus));
    assert(writes == 0); /* Never write a fabricated 0xFF register value. */

    reset_mock();
    auto_power = false;
    ticks = UINT32_MAX - 100U; /* Timeout still works across tick wrap. */
    uint32_t start = ticks;
    assert(!NAU7802_powerUp(&bus));
    assert((uint32_t)(ticks - start) == 200U);
    assert((registers[0] & 0x10) == 0); /* No cycle start on timeout. */

    reset_mock();
    fail_write_reg = 0x1C;
    assert(!NAU7802_begin(&bus)); /* PGA cap configuration must succeed. */
    reset_mock();
    auto_calibration = false;
    assert(!NAU7802_begin(&bus));
    assert(ticks >= 1200 && ticks < 1300);
    reset_mock();
    calibration_error = true;
    assert(!NAU7802_begin(&bus));

    reset_mock();
    registers[0] = 0x20;
    registers[0x12] = 0x80;
    assert(NAU7802_getReading(&bus) == -8388608);
    assert(NAU7802_getAverage(&bus, 3, 10) == -8388608);
    registers[0x12] = registers[0x13] = registers[0x14] = 0xFF;
    assert(NAU7802_getReading(&bus) == -1);
    registers[0x12] = 0x7F;
    assert(NAU7802_getReading(&bus) == 8388607);
    fail_read_reg = 0x12;
    assert(NAU7802_getAverage(&bus, 3, 10) == 0);

    reset_mock();
    assert(NAU7802_getAverage(&bus, 3, 10) == 0);
    assert(ticks == 10 && data_reads == 0);
    fail_read_reg = 0;
    assert(NAU7802_getAverage(&bus, 3, 0) == 0); /* Bus failure exits even without timeout. */
    puts("NAU7802 mocked HAL regression checks passed");
    return 0;
}
