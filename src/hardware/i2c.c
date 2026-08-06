/* I2C via the Linux character device.
 *
 * No external library — /dev/i2c-* is the interface. Note that
 * dtparam=i2c_arm=on alone does NOT create /dev/i2c-1: it enables the bus and
 * loads i2c_bcm2835, but the userspace character device comes from the
 * separate i2c-dev module that nothing auto-loads. SPI has no equivalent step,
 * which makes this failure look exactly like the dtparam not working at all.
 *
 *     sudo modprobe i2c-dev
 *     echo "i2c-dev" | sudo tee -a /etc/modules
 *
 * raspi-config's "Enable I2C" does both steps; hand-editing config.txt does not.
 */

#include "hw.h"

#include <errno.h>
#include <fcntl.h>
#include <linux/i2c-dev.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

int hw_i2c_open(uint8_t addr)
{
    int fd = open(HW_I2C_BUS, O_RDWR);
    if (fd < 0) {
        fprintf(stderr, "hw: opening %s failed: %s%s\n", HW_I2C_BUS,
                strerror(errno),
                errno == ENOENT ? " (is the i2c-dev module loaded?)" : "");
        return -1;
    }

    if (ioctl(fd, I2C_SLAVE, addr) < 0) {
        fprintf(stderr, "hw: selecting I2C address 0x%02x failed: %s\n",
                addr, strerror(errno));
        close(fd);
        return -1;
    }

    return fd;
}

int hw_i2c_write(int fd, const uint8_t *buf, size_t len)
{
    if (fd < 0) {
        return -1;
    }

    ssize_t n = write(fd, buf, len);
    if (n < 0 || (size_t)n != len) {
        fprintf(stderr, "hw: I2C write failed: %s\n", strerror(errno));
        return -1;
    }
    return 0;
}

int hw_i2c_read(int fd, uint8_t *buf, size_t len)
{
    if (fd < 0) {
        return -1;
    }

    ssize_t n = read(fd, buf, len);
    if (n < 0 || (size_t)n != len) {
        fprintf(stderr, "hw: I2C read failed: %s\n", strerror(errno));
        return -1;
    }
    return 0;
}

int hw_i2c_probe(uint8_t addr)
{
    int fd = hw_i2c_open(addr);
    if (fd < 0) {
        return 0;
    }

    /* A zero-length write is the same thing i2cdetect does: it addresses the
     * device and looks for an ACK without transferring a payload. */
    uint8_t dummy = 0;
    int present = (write(fd, &dummy, 0) == 0);
    close(fd);
    return present;
}

/* ---------------- Audio level and volume ----------------
 *
 * Both of these belong to the XVF3800, and the environment guide does not
 * document its register map — that is in the part's own datasheet, which this
 * project does not yet have on hand.
 *
 * They are left explicitly unimplemented rather than faked. A meter that
 * invents a level, or a volume control that returns success without changing
 * anything, is worse than one that reports it has no reading: the UI is built
 * to show what actually reaches the speakers, and a plausible-looking lie
 * would defeat exactly that.
 *
 * To finish these, fill in the register writes below and delete the guards.
 */

int hw_audio_level(void)
{
    if (!hw_present()) {
        return -1;
    }

    /* TODO(xvf3800): read the level/VU register over I2C and scale to 0-100.
     *   int fd = hw_i2c_open(<XVF3800 address>);
     *   ... write register pointer, read value ...
     * Until then the meter reads "no data" rather than a fabricated level. */
    return -1;
}

int hw_set_volume(int percent)
{
    if (percent < 0)   percent = 0;
    if (percent > 100) percent = 100;

    if (!hw_present()) {
        return 0;  /* dev box: the value is still tracked and reported */
    }

    /* TODO(xvf3800): write the gain register over I2C.
     * Returning -1 rather than 0 keeps the caller honest — it logs that the
     * value was accepted by the UI but not applied to hardware. */
    return -1;
}
