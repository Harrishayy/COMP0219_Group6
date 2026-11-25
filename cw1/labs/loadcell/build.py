#!/usr/bin/env python3
"""Build the HAL lab using STM32 packages already installed by PlatformIO.

No download, CubeIDE workspace access, or change to the competition firmware.
Override STM32_FRAMEWORK and ARM_GCC_BIN for another local installation.
"""
import os
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parent
packages = Path.home() / '.platformio/packages'
framework = Path(os.environ.get('STM32_FRAMEWORK', packages / 'framework-arduinoststm32'))
tools = Path(os.environ.get('ARM_GCC_BIN', packages / 'toolchain-gccarmnoneeabi/bin'))
hal = framework / 'system/Drivers/STM32F4xx_HAL_Driver'
cmsis = framework / 'system/Drivers/CMSIS'
device = cmsis / 'Device/ST/STM32F4xx'
out = root / 'build'
out.mkdir(exist_ok=True)
includes = [root / 'Core/Inc', root.parent, hal / 'Inc', device / 'Include',
            packages / 'framework-cmsis/CMSIS/Core/Include']
flags = ['-mcpu=cortex-m4', '-mthumb', '-mfpu=fpv4-sp-d16', '-mfloat-abi=hard',
         '-DSTM32F411xE', '-DUSE_HAL_DRIVER', '-Os', '-g3', '-ffunction-sections',
         '-fdata-sections', '-Wall', '-Wextra'] + [f'-I{p}' for p in includes]
sources = [root / 'Core/Src/main.c', root / 'Core/Src/syscalls.c', root.parent / 'nau7802.c',
           device / 'Source/Templates/system_stm32f4xx.c',
           device / 'Source/Templates/gcc/startup_stm32f411xe.s']
sources += [hal / f'Src/stm32f4xx_{name}.c' for name in
            ['hal', 'hal_cortex', 'hal_rcc', 'hal_rcc_ex', 'hal_gpio', 'hal_flash',
             'hal_flash_ex', 'hal_pwr', 'hal_pwr_ex', 'hal_i2c', 'hal_uart', 'hal_dma']]
objects = []
for src in sources:
    obj = out / (src.stem + '.o')
    subprocess.run([str(tools / 'arm-none-eabi-gcc'), *flags, '-c', str(src), '-o', str(obj)], check=True)
    objects.append(str(obj))
elf = out / 'loadcell.elf'
subprocess.run([str(tools / 'arm-none-eabi-gcc'), *flags, *objects,
                f'-T{root / "STM32F411RE.ld"}', '--specs=nano.specs', '--specs=nosys.specs',
                '-Wl,--gc-sections', f'-Wl,-Map={out / "loadcell.map"}', '-u', '_printf_float',
                '-Wl,--start-group', '-lc', '-lm', '-Wl,--end-group', '-o', str(elf)], check=True)
subprocess.run([str(tools / 'arm-none-eabi-objcopy'), '-O', 'binary', str(elf), str(out / 'loadcell.bin')], check=True)
subprocess.run([str(tools / 'arm-none-eabi-size'), str(elf)], check=True)
print(f'Built {elf}')
