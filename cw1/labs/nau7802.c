#include "nau7802.h"

/* local static storage for calibration */
static int32_t _zeroOffset = 0;
static float _calFactor = 1.0f;

/* Small HAL wrappers */

static bool readRegister(I2C_HandleTypeDef *hi2c, uint8_t reg, uint8_t *value)
{
    return HAL_I2C_Mem_Read(hi2c, NAU7802_HAL_ADDR_8BIT, reg,
                            I2C_MEMADD_SIZE_8BIT, value, 1, 100) == HAL_OK;
}

static bool updateRegister(I2C_HandleTypeDef *hi2c, uint8_t reg,
                           uint8_t mask, uint8_t value)
{
    uint8_t current;
    if (!readRegister(hi2c, reg, &current)) return false;
    current = (uint8_t)((current & (uint8_t)~mask) | (value & mask));
    return NAU7802_setRegister(hi2c, reg, current);
}

bool NAU7802_isConnected(I2C_HandleTypeDef *hi2c)
{
    /* HAL uses the 7-bit address shifted left once. */
    if (HAL_I2C_IsDeviceReady(hi2c, NAU7802_HAL_ADDR_8BIT, 3, 50) == HAL_OK)
        return true;
    return false;
}

uint8_t NAU7802_getRegister(I2C_HandleTypeDef *hi2c, uint8_t reg)
{
    uint8_t val = 0;
    if (readRegister(hi2c, reg, &val))
        return val;
    return 0xFF;
}

bool NAU7802_setRegister(I2C_HandleTypeDef *hi2c, uint8_t reg, uint8_t value)
{
    if (HAL_I2C_Mem_Write(hi2c, NAU7802_HAL_ADDR_8BIT, reg, I2C_MEMADD_SIZE_8BIT, &value, 1, 100) == HAL_OK)
        return true;
    return false;
}

bool NAU7802_setBit(I2C_HandleTypeDef *hi2c, uint8_t bit, uint8_t reg)
{
    if (bit > 7) return false;
    return updateRegister(hi2c, reg, (uint8_t)(1U << bit), (uint8_t)(1U << bit));
}

bool NAU7802_clearBit(I2C_HandleTypeDef *hi2c, uint8_t bit, uint8_t reg)
{
    if (bit > 7) return false;
    return updateRegister(hi2c, reg, (uint8_t)(1U << bit), 0);
}

bool NAU7802_getBit(I2C_HandleTypeDef *hi2c, uint8_t bit, uint8_t reg)
{
    uint8_t v;
    if (bit > 7 || !readRegister(hi2c, reg, &v)) return false;
    return ((v & (1U << bit)) != 0);
}

static bool read24Bit(I2C_HandleTypeDef *hi2c, uint8_t regMSB, int32_t *value)
{
    uint8_t buf[3] = {0};
    if (HAL_I2C_Mem_Read(hi2c, NAU7802_HAL_ADDR_8BIT, regMSB, I2C_MEMADD_SIZE_8BIT, buf, 3, 200) != HAL_OK)
        return false;

    uint32_t unsigned32 = ((uint32_t)buf[0] << 16) | ((uint32_t)buf[1] << 8) | (uint32_t)buf[2];
    /* sign extend if needed */
    if (unsigned32 & 0x00800000)
        unsigned32 |= 0xFF000000;
    *value = (int32_t)unsigned32;
    return true;
}

int32_t NAU7802_get24Bit(I2C_HandleTypeDef *hi2c, uint8_t regMSB)
{
    int32_t value = 0;
    (void)read24Bit(hi2c, regMSB, &value);
    return value;
}

bool NAU7802_available(I2C_HandleTypeDef *hi2c)
{
    return NAU7802_getBit(hi2c, NAU7802_PU_CTRL_CR, NAU7802_PU_CTRL);
}

/* Basic control functions */

bool NAU7802_reset(I2C_HandleTypeDef *hi2c)
{
    if (!NAU7802_setBit(hi2c, NAU7802_PU_CTRL_RR, NAU7802_PU_CTRL)) return false;
    HAL_Delay(1);
    if (!NAU7802_clearBit(hi2c, NAU7802_PU_CTRL_RR, NAU7802_PU_CTRL)) return false;
    return true;
}

bool NAU7802_powerUp(I2C_HandleTypeDef *hi2c)
{
    if (!NAU7802_setBit(hi2c, NAU7802_PU_CTRL_PUD, NAU7802_PU_CTRL)) return false;
    if (!NAU7802_setBit(hi2c, NAU7802_PU_CTRL_PUA, NAU7802_PU_CTRL)) return false;

    /* A failed read must never be mistaken for PUR=1. */
    uint32_t start = HAL_GetTick();
    do
    {
        uint8_t status;
        if (!readRegister(hi2c, NAU7802_PU_CTRL, &status)) return false;
        if (status & (1U << NAU7802_PU_CTRL_PUR))
            return NAU7802_setBit(hi2c, NAU7802_PU_CTRL_CS, NAU7802_PU_CTRL);
        HAL_Delay(1);
    } while ((uint32_t)(HAL_GetTick() - start) < 200U);
    return false;
}

bool NAU7802_setLDO(I2C_HandleTypeDef *hi2c, uint8_t ldoValue)
{
    if (ldoValue > 7) return false;
    if (!updateRegister(hi2c, NAU7802_CTRL1, 0x38,
                        (uint8_t)(ldoValue << NAU7802_CTRL1_VLDO))) return false;
    return NAU7802_setBit(hi2c, NAU7802_PU_CTRL_AVDDS, NAU7802_PU_CTRL);
}

bool NAU7802_setGain(I2C_HandleTypeDef *hi2c, uint8_t gainValue)
{
    if (gainValue > 7) return false;
    return updateRegister(hi2c, NAU7802_CTRL1, 0x07, gainValue);
}

bool NAU7802_setSampleRate(I2C_HandleTypeDef *hi2c, uint8_t rateValue)
{
    /* Encodings 4, 5 and 6 are reserved in the datasheet. */
    if (rateValue > 3 && rateValue != 7) return false;
    return updateRegister(hi2c, NAU7802_CTRL2, 0x70,
                           (uint8_t)(rateValue << NAU7802_CTRL2_CRS));
}

static bool calibrateAFE(I2C_HandleTypeDef *hi2c)
{
    /* CALMOD=00: internal offset calibration, with the configured gain/rate. */
    if (!updateRegister(hi2c, NAU7802_CTRL2, 0x07,
                         (uint8_t)(1U << NAU7802_CTRL2_CALS))) return false;
    uint32_t start = HAL_GetTick();
    do
    {
        uint8_t status;
        if (!readRegister(hi2c, NAU7802_CTRL2, &status)) return false;
        if (!(status & (1U << NAU7802_CTRL2_CALS)))
            return !(status & (1U << NAU7802_CTRL2_CAL_ERROR));
        HAL_Delay(1);
    } while ((uint32_t)(HAL_GetTick() - start) < 1000U);
    return false;
}

/* begin: a basic init similar to Arduino begin() */
bool NAU7802_begin(I2C_HandleTypeDef *hi2c)
{
    if (!NAU7802_isConnected(hi2c)) {
        /* try once more like Arduino library does */
        HAL_Delay(10);
        if (!NAU7802_isConnected(hi2c)) return false;
    }

    if (!NAU7802_reset(hi2c) || !NAU7802_powerUp(hi2c)) return false;
    if (!NAU7802_setLDO(hi2c, NAU7802_LDO_3V3)) return false;
    if (!NAU7802_setGain(hi2c, 7)) return false; /* Gain 128. */
    if (!NAU7802_setSampleRate(hi2c, 3)) return false; /* 80 SPS. */

    /* CLK_CHP=11: power-on sequence in datasheet section 9.1. */
    if (!updateRegister(hi2c, NAU7802_ADC, 0x30, 0x30)) return false;
    if (!NAU7802_setBit(hi2c, NAU7802_PGA_PWR_PGA_CAP_EN,
                        NAU7802_PGA_PWR)) return false;

    HAL_Delay(200); /* Allow LDO and analog signal path to settle. */
    return calibrateAFE(hi2c);
}

/* Read one 24-bit reading (ADCO_B2..B0) */
int32_t NAU7802_getReading(I2C_HandleTypeDef *hi2c)
{
    return NAU7802_get24Bit(hi2c, NAU7802_ADCO_B2);
}

/* Average multiple readings (waits for available) */
int32_t NAU7802_getAverage(I2C_HandleTypeDef *hi2c, uint8_t samples, uint32_t timeout_ms)
{
    int64_t total = 0;
    uint8_t got = 0;
    uint32_t start = HAL_GetTick();

    while (got < samples)
    {
        uint8_t status;
        if (!readRegister(hi2c, NAU7802_PU_CTRL, &status)) return 0;
        if (status & (1U << NAU7802_PU_CTRL_CR))
        {
            int32_t v;
            if (!read24Bit(hi2c, NAU7802_ADCO_B2, &v)) return 0;
            total += v;
            got++;
        } else {
            HAL_Delay(1);
        }
        if (timeout_ms && (uint32_t)(HAL_GetTick() - start) >= timeout_ms) break;
    }

    if (got == 0) return 0;
    return (int32_t)(total / got);
}

/* Simple calibration helpers */
void NAU7802_setZeroOffset(int32_t val) { _zeroOffset = val; }
int32_t NAU7802_getZeroOffset(void) { return _zeroOffset; }
void NAU7802_setCalibrationFactor(float f) { _calFactor = f; }
float NAU7802_getCalibrationFactor(void) { return _calFactor; }

float NAU7802_getWeight(I2C_HandleTypeDef *hi2c, bool allowNegative, uint8_t samples, uint32_t timeout_ms)
{
    int32_t onScale = NAU7802_getAverage(hi2c, samples, timeout_ms);

    if (!allowNegative && onScale < _zeroOffset) onScale = _zeroOffset;
    float weight = ((float)(onScale - _zeroOffset)) / _calFactor;
    return weight;
}
