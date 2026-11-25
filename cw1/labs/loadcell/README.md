# Repaired load-cell lab

The connected NUCLEO-F411RE is now running this STM32 HAL program. Source lives in
`Core/Src/main.c`; it uses the corrected `../nau7802.c` and `../nau7802.h`.

## What caused the blank terminal

On 21 October 2026, a direct 115200 baud capture received zero bytes. A background
calibration dashboard (`python -m calib serve --port auto --http-port 8765`) also held
`/dev/cu.usbmodem103` open. It was stopped to release the port.

The board's actual reset vector led through startup into an empty `main()` at
`0x08000bb8`. ST-LINK/GDB confirmed the CPU was at `0x08000bbe`, branching back to
`0x08000bbc` forever. There were no peripheral initialization or transmission calls.
USART2 and RCC peripheral enable registers were zero. A successful build/download
therefore did not mean the intended lab application was executing.

The original CubeIDE workspace is recorded at
`/Users/harrishayyanar/Documents/STM32CubeIDE/workspace_2.2.0`, but macOS denied
access to that directory even outside the command sandbox. That original project
was not edited. Flashing it again will replace the working firmware on the board.

## View readings in CoolTerm

### Use the existing website instead

Run `pnpm dev` from the repository root, then open
[Load cell](http://127.0.0.1:8765/#load-cell) (or the port printed by the launcher).
Close any Terminal serial monitor first. The website recognizes this lab firmware
and shows real raw counts, a tare button, and a known-mass calibration form.
Steps finish only after the board confirms them. Grams stay blank until calibration
has succeeded. There is no firmware change required to use the website.

### CoolTerm connection

1. Select port `usbmodem103` (`/dev/cu.usbmodem103`), baud 115200, 8 data bits,
   no parity, 1 stop bit. Disable RTS/CTS, DTR/DSR and XON/XOFF flow control.
2. Connect. Repeated `Raw: ... counts (uncalibrated)` lines should appear immediately.
   Reset the board only if you want to see startup diagnostics.
3. Keep the calibration dashboard and all other serial monitors disconnected while
   CoolTerm uses this port. The port name can change after reconnecting USB.

The firmware found the actual NAU7802 at address `0x2A` using PB8 for SCL and PB9
for SDA (Arduino D15/D14). It also checks PB6/PB7 if PB8/PB9 has no NAU7802.

## Convert raw counts to grams

Raw counts are real ADC measurements, not yet a weight measurement. Known-mass
calibration needs physical actions and was not performed automatically.

1. Remove all load. Send `t` followed by CR or LF.
2. Place a known mass on the scale. If it is 249 g, send `k 249` followed by CR or LF.
   Substitute your actual known mass in grams.
3. Output changes to `Weight: ... g  Raw: ...`.

Send `h` for help. Tare and scale factor remain in RAM and are lost on reset.
There are no timed prompts that silently assume you placed a mass.

## Build and use CubeIDE

Run from the repository root:

```sh
python3 labs/loadcell/build.py
cc -std=c11 -Wall -Wextra -Werror -Ilabs/tests -Ilabs labs/nau7802.c labs/tests/test_nau7802.c -o /tmp/test-nau7802
/tmp/test-nau7802
```

The build uses the STM32 HAL, CMSIS, startup assembly and ARM GCC already installed
under `~/.platformio/packages`; no Arduino runtime is linked. It uses the internal
16 MHz HSI clock. `STM32_FRAMEWORK` and `ARM_GCC_BIN` can override installed paths.
The linker includes `_printf_float`; a bounded `_sbrk` supports newlib formatting.

Outputs are `build/loadcell.elf` (debug symbols), `build/loadcell.bin`, and
`build/loadcell.map`. The ELF can be used in a CubeIDE STM32 debug configuration.
For source builds in CubeIDE, create an existing-code Makefile project here and
set its build command to `python3 build.py` with the default make command disabled.
Do not simply drop this complete main into another generated project: its UART/I2C
MSP callbacks and interrupt handlers would duplicate generated handlers. Transfer
them into the corresponding generated files if integrating with CubeMX.

The full 512 KiB original flash backup is `backup/original-flash.bin`. Preserve it
before deleting this directory. Build products and that backup are git-ignored.
The board was programmed through STM32CubeProgrammer and verified by readback.
Real serial capture and flash verification logs are in `build/`.

## Driver repairs

Corrected LDO bit position and 3.3 V encoding, calibration/channel/PGA capacitor
bit positions, internal ADC calibration, and I2C error/timeout handling. Register
definitions were checked against the
[Nuvoton NAU7802 datasheet](https://www.nuvoton.com/export/resource-files/en-us--DS_NAU7802_DataSheet_EN_Rev2.6.pdf).
The host regression checks cover register masks, initialization, timeouts, failed
I2C transactions, internal calibration and signed 24-bit ADC samples.
