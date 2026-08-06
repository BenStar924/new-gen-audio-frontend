/* Hardware layer for the Operator Console.
 *
 * Owns GPIO direction, bias and the named control functions, and opens the I2C
 * and SPI devices. Everything above this header deals in console concepts
 * (lanes, mute, volume); everything below deals in pins and buses.
 *
 * Requires libgpiod v2 (Raspberry Pi OS Trixie ships 2.2.x as libgpiod-dev).
 * The v1 API is neither available on Trixie nor compatible — v1 code will not
 * compile against this. Note libgpiod3 (runtime) is installed by default but
 * libgpiod-dev (headers, linker symlink, pkg-config metadata) is not.
 *
 * Pin assignments come from /boot/firmware/config.txt, which has already put
 * every line into its safe state before Linux starts. See hw_init().
 */

#ifndef OPERATORCONSOLE_HW_H
#define OPERATORCONSOLE_HW_H

#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

/* BCM GPIO numbers, matching the config.txt block in the environment guide.
 *
 *   gpio=25=op,dh   pin 22  amp mute ACTIVE HIGH -> start MUTED
 *   gpio=24=op,dl   pin 18  mic enable           -> start OFF
 *   gpio=19=op,dl   pin 35  XVF3800 reset        -> de-asserted
 *   gpio=12,16,...  mux A/B                      -> low
 *   gpio=26         LED
 */
#define HW_GPIO_AMP_MUTE    25  /* active HIGH: 1 = muted */
#define HW_GPIO_MIC_ENABLE  24  /* active HIGH: 1 = mic live */
#define HW_GPIO_XVF_RESET   19  /* active HIGH: 1 = held in reset */
#define HW_GPIO_MUX_A       12
#define HW_GPIO_MUX_B       16
#define HW_GPIO_LED         26

/* The lane mux is two select lines, so it addresses four lanes. A site
 * configured for more than this needs additional select lines in hardware —
 * hw_select_lane() reports the shortfall rather than silently wrapping. */
#define HW_MUX_LANES        4

/* I2C. The AD2437 answers at two addresses: BASE_ADDR for its own registers
 * and BUS_ADDR for remote subordinate access, selected by the LSB of the 7-bit
 * address. With both ADR pins strapped low the pair is 0x68/0x69 — one chip,
 * not two. A transceiver configured as a subordinate does not answer on
 * BUS_ADDR, so 0x69 responding confirms this one is the main node. */
#define HW_I2C_BUS          "/dev/i2c-1"
#define HW_I2C_AD2437_BASE  0x68
#define HW_I2C_AD2437_BUS   0x69

/* SPI. Device selection is by which node you open — do NOT drive the chip
 * selects. With dtparam=spi=on the spidev driver owns CE0 (pin 24) and CE1
 * (pin 26), idles them high and asserts them only during a transfer;
 * requesting them via libgpiod conflicts with the driver. */
#define HW_SPI_DEV0         "/dev/spidev0.0"
#define HW_SPI_DEV1         "/dev/spidev0.1"

/* Backlight PWM, from dtoverlay=pwm,pin=18,func=2. */
#define HW_PWM_CHIP         "/sys/class/pwm/pwmchip0"
#define HW_PWM_CHANNEL      0

/* Every call returns 0 on success and -1 on failure, having logged the reason.
 *
 * hw_init() requests the GPIO lines with output values that MATCH the safe
 * states config.txt already set. That ordering matters: requesting a line
 * drives it to the requested value, so requesting the mute line low would
 * un-mute the amplifier for as long as it took the app to decide otherwise —
 * an audible pop on every start. */
int  hw_init(void);
void hw_shutdown(void);

/* Named controls. */
int  hw_set_mute(bool muted);       /* true = amplifier muted */
int  hw_set_mic(bool live);         /* true = microphone enabled */
int  hw_set_led(bool on);
int  hw_reset_xvf3800(void);        /* pulse reset, then release */

/* Drives the mux select lines. Lanes are 1-based. Returns -1 if the lane is
 * outside what two select lines can address. */
int  hw_select_lane(int lane);

/* Master volume, 0-100. One per console — there is one amplifier. */
int  hw_set_volume(int percent);

/* Backlight, 0-100, via the PWM sysfs interface. */
int  hw_set_brightness(int percent);

/* Current audio level, 0-100, for the meter. Returns -1 if unavailable. */
int  hw_audio_level(void);

/* Bus primitives, exposed for device drivers built on top of this layer. */
int  hw_i2c_open(uint8_t addr);                       /* returns fd, or -1 */
int  hw_i2c_write(int fd, const uint8_t *buf, size_t len);
int  hw_i2c_read(int fd, uint8_t *buf, size_t len);
int  hw_i2c_probe(uint8_t addr);                      /* 1 present, 0 absent */

int  hw_spi_open(const char *dev, uint32_t speed_hz, uint8_t mode);
int  hw_spi_transfer(int fd, const uint8_t *tx, uint8_t *rx, size_t len);

/* True when hw_init() found real hardware. False means the process is running
 * on a machine without these buses — a dev box — and the control functions are
 * no-ops that still track state. Reported at /api/status so the UI can say so
 * rather than pretending it is driving an amplifier. */
bool hw_present(void);

#endif /* OPERATORCONSOLE_HW_H */
