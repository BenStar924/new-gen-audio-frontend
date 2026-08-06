/* GPIO via libgpiod v2, plus the PWM backlight.
 *
 * All output lines are requested in one call so they share a single line
 * request, which is what the v2 API is built around — one request object owns
 * a set of offsets and you address them by offset thereafter.
 */

#include "hw.h"

#include <errno.h>
#include <fcntl.h>
#include <gpiod.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* On the Zero 2 W the header pins are on gpiochip0. (Pi 5 moves them to
 * gpiochip4 — if this ever runs on one, that is the line to change.) */
#define GPIO_CHIP "/dev/gpiochip0"
#define CONSUMER  "operatorconsole"

static struct gpiod_chip         *g_chip;
static struct gpiod_line_request *g_request;
static bool                       g_present;

/* Offsets in the order they are requested. */
static const unsigned int g_offsets[] = {
    HW_GPIO_AMP_MUTE,
    HW_GPIO_MIC_ENABLE,
    HW_GPIO_XVF_RESET,
    HW_GPIO_MUX_A,
    HW_GPIO_MUX_B,
    HW_GPIO_LED,
};

/* Safe states, matching config.txt exactly. Requesting a line drives it, so
 * these must agree with what firmware already set or the request itself
 * becomes a glitch: mute low for even a few milliseconds is an audible pop
 * through the speakers. */
static const enum gpiod_line_value g_safe[] = {
    GPIOD_LINE_VALUE_ACTIVE,    /* 25 amp mute   -> MUTED */
    GPIOD_LINE_VALUE_INACTIVE,  /* 24 mic enable -> off */
    GPIOD_LINE_VALUE_INACTIVE,  /* 19 XVF reset  -> de-asserted */
    GPIOD_LINE_VALUE_INACTIVE,  /* 12 mux A */
    GPIOD_LINE_VALUE_INACTIVE,  /* 16 mux B */
    GPIOD_LINE_VALUE_INACTIVE,  /* 26 LED */
};

#define N_LINES (sizeof(g_offsets) / sizeof(g_offsets[0]))

bool hw_present(void)
{
    return g_present;
}

static int set_line(unsigned int offset, bool active)
{
    if (!g_present) {
        return 0;  /* dev box: state is tracked above this layer */
    }

    if (gpiod_line_request_set_value(g_request, offset,
                                     active ? GPIOD_LINE_VALUE_ACTIVE
                                            : GPIOD_LINE_VALUE_INACTIVE) < 0) {
        fprintf(stderr, "hw: setting GPIO%u failed: %s\n", offset, strerror(errno));
        return -1;
    }
    return 0;
}

int hw_init(void)
{
    struct gpiod_line_settings *settings = NULL;
    struct gpiod_line_config   *lcfg     = NULL;
    struct gpiod_request_config *rcfg    = NULL;
    int rc = -1;

    g_chip = gpiod_chip_open(GPIO_CHIP);
    if (!g_chip) {
        /* Not fatal. Without a chip this is a development machine, and the
         * console still runs so the UI can be worked on — it just reports
         * hardware: false rather than pretending to drive an amplifier. */
        fprintf(stderr, "hw: %s unavailable (%s) — running without hardware\n",
                GPIO_CHIP, strerror(errno));
        g_present = false;
        return 0;
    }

    settings = gpiod_line_settings_new();
    lcfg     = gpiod_line_config_new();
    rcfg     = gpiod_request_config_new();
    if (!settings || !lcfg || !rcfg) {
        fprintf(stderr, "hw: out of memory building line config\n");
        goto out;
    }

    if (gpiod_line_settings_set_direction(settings,
                                          GPIOD_LINE_DIRECTION_OUTPUT) < 0) {
        fprintf(stderr, "hw: set_direction failed: %s\n", strerror(errno));
        goto out;
    }

    /* Each line needs its own safe value, so add them one at a time rather
     * than as a block with a shared setting. */
    for (size_t i = 0; i < N_LINES; i++) {
        if (gpiod_line_settings_set_output_value(settings, g_safe[i]) < 0 ||
            gpiod_line_config_add_line_settings(lcfg, &g_offsets[i], 1,
                                                settings) < 0) {
            fprintf(stderr, "hw: configuring GPIO%u failed: %s\n",
                    g_offsets[i], strerror(errno));
            goto out;
        }
    }

    gpiod_request_config_set_consumer(rcfg, CONSUMER);

    g_request = gpiod_chip_request_lines(g_chip, rcfg, lcfg);
    if (!g_request) {
        fprintf(stderr, "hw: requesting lines failed: %s\n", strerror(errno));
        goto out;
    }

    g_present = true;
    rc = 0;

out:
    if (rcfg)     gpiod_request_config_free(rcfg);
    if (lcfg)     gpiod_line_config_free(lcfg);
    if (settings) gpiod_line_settings_free(settings);

    if (rc != 0 && g_chip) {
        gpiod_chip_close(g_chip);
        g_chip = NULL;
    }
    return rc;
}

void hw_shutdown(void)
{
    /* Leave the board as config.txt would have it: amplifier muted, mic off.
     * A console that exits with the amp live is a console that pops or, worse,
     * keeps a microphone open with nothing watching it. */
    if (g_present) {
        set_line(HW_GPIO_AMP_MUTE, true);
        set_line(HW_GPIO_MIC_ENABLE, false);
        set_line(HW_GPIO_LED, false);
    }

    if (g_request) {
        gpiod_line_request_release(g_request);
        g_request = NULL;
    }
    if (g_chip) {
        gpiod_chip_close(g_chip);
        g_chip = NULL;
    }
    g_present = false;
}

int hw_set_mute(bool muted)
{
    /* Active high: driving the line high mutes. */
    return set_line(HW_GPIO_AMP_MUTE, muted);
}

int hw_set_mic(bool live)
{
    return set_line(HW_GPIO_MIC_ENABLE, live);
}

int hw_set_led(bool on)
{
    return set_line(HW_GPIO_LED, on);
}

int hw_reset_xvf3800(void)
{
    if (set_line(HW_GPIO_XVF_RESET, true) < 0) {
        return -1;
    }
    usleep(10000);  /* 10 ms asserted is comfortably above the part's minimum */
    return set_line(HW_GPIO_XVF_RESET, false);
}

int hw_select_lane(int lane)
{
    if (lane < 1 || lane > HW_MUX_LANES) {
        /* Deliberately an error rather than a wrap. Two select lines address
         * four lanes; a site configured for more needs more select lines, and
         * silently routing lane 5 to lane 1 would be worse than refusing. */
        fprintf(stderr,
                "hw: lane %d is outside the %d the mux can address — "
                "more select lines are needed in hardware\n",
                lane, HW_MUX_LANES);
        return -1;
    }

    unsigned int code = (unsigned int)(lane - 1);
    if (set_line(HW_GPIO_MUX_A, code & 0x1) < 0) return -1;
    if (set_line(HW_GPIO_MUX_B, code & 0x2) < 0) return -1;
    return 0;
}

/* ---------------- PWM backlight ---------------- */

static int pwm_write(const char *leaf, const char *value)
{
    char path[256];
    snprintf(path, sizeof(path), "%s/%s", HW_PWM_CHIP, leaf);

    int fd = open(path, O_WRONLY);
    if (fd < 0) {
        return -1;
    }

    ssize_t n = write(fd, value, strlen(value));
    close(fd);
    return n < 0 ? -1 : 0;
}

int hw_set_brightness(int percent)
{
    if (percent < 0)   percent = 0;
    if (percent > 100) percent = 100;

    /* 1 kHz. Fast enough not to be visible, slow enough that the duty step is
     * not fighting the PWM clock's resolution. */
    const long period_ns = 1000000;
    char buf[64], leaf[64];

    snprintf(leaf, sizeof(leaf), "pwm%d/period", HW_PWM_CHANNEL);
    snprintf(buf, sizeof(buf), "%ld", period_ns);
    if (pwm_write(leaf, buf) < 0) {
        /* Not exported yet — export once, then retry. */
        snprintf(buf, sizeof(buf), "%d", HW_PWM_CHANNEL);
        if (pwm_write("export", buf) < 0) {
            return -1;  /* no PWM on this machine; caller keeps the value */
        }
        usleep(50000);  /* udev needs a moment to create the attributes */
        snprintf(buf, sizeof(buf), "%ld", period_ns);
        if (pwm_write(leaf, buf) < 0) {
            return -1;
        }
    }

    snprintf(leaf, sizeof(leaf), "pwm%d/duty_cycle", HW_PWM_CHANNEL);
    snprintf(buf, sizeof(buf), "%ld", period_ns * percent / 100);
    if (pwm_write(leaf, buf) < 0) {
        return -1;
    }

    snprintf(leaf, sizeof(leaf), "pwm%d/enable", HW_PWM_CHANNEL);
    return pwm_write(leaf, "1");
}
