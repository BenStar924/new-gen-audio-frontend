/* SPI via the Linux spidev character device.
 *
 * No external library — /dev/spidev0.* is the interface.
 *
 * Do NOT drive the chip selects. With dtparam=spi=on the spidev driver owns
 * CE0 (pin 24) and CE1 (pin 26), idles them high and asserts them only for the
 * duration of a transfer. Requesting them via libgpiod conflicts with the
 * driver. Device selection is by which node you open: spidev0.0 or spidev0.1.
 */

#include "hw.h"

#include <errno.h>
#include <fcntl.h>
#include <linux/spi/spidev.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

static uint32_t g_speed_hz = 1000000;

int hw_spi_open(const char *dev, uint32_t speed_hz, uint8_t mode)
{
    int fd = open(dev, O_RDWR);
    if (fd < 0) {
        fprintf(stderr, "hw: opening %s failed: %s\n", dev, strerror(errno));
        return -1;
    }

    uint8_t bits = 8;

    if (ioctl(fd, SPI_IOC_WR_MODE, &mode) < 0 ||
        ioctl(fd, SPI_IOC_WR_BITS_PER_WORD, &bits) < 0 ||
        ioctl(fd, SPI_IOC_WR_MAX_SPEED_HZ, &speed_hz) < 0) {
        fprintf(stderr, "hw: configuring %s failed: %s\n", dev, strerror(errno));
        close(fd);
        return -1;
    }

    g_speed_hz = speed_hz;
    return fd;
}

int hw_spi_transfer(int fd, const uint8_t *tx, uint8_t *rx, size_t len)
{
    if (fd < 0 || len == 0) {
        return -1;
    }

    /* Full duplex: SPI always clocks the same number of bytes in both
     * directions, so a read is a write of don't-care bytes. */
    struct spi_ioc_transfer xfer;
    memset(&xfer, 0, sizeof(xfer));
    xfer.tx_buf        = (unsigned long)tx;
    xfer.rx_buf        = (unsigned long)rx;
    xfer.len           = (uint32_t)len;
    xfer.speed_hz      = g_speed_hz;
    xfer.bits_per_word = 8;

    if (ioctl(fd, SPI_IOC_MESSAGE(1), &xfer) < 0) {
        fprintf(stderr, "hw: SPI transfer failed: %s\n", strerror(errno));
        return -1;
    }

    return 0;
}
