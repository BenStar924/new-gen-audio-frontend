/* Operator Console — application entry point and console state.
 *
 * Brings up the hardware in its safe state, restores operator settings, starts
 * the HTTP server that serves www/ and the /api endpoints, then waits. All the
 * interesting behaviour is in webserver.c (HTTP) and hardware/ (pins).
 *
 * The lane model, which the hardware dictates:
 *
 * There is ONE amplifier mute (GPIO25), ONE microphone enable (GPIO24) and one
 * master volume. So audio state is console-wide, not per-lane. A lane is a
 * routing destination: selecting one points the mux at it and opens the mic
 * and speakers; releasing it closes them again. With no lane selected the
 * board sits exactly as it boots — mic off, amplifier muted.
 *
 * Mic and speakers can still be overridden by hand while a lane is held, which
 * is what lets an operator cut the microphone without giving up the lane.
 */

#include "console.h"
#include "hardware/hw.h"
#include "webserver.h"

#include <errno.h>
#include <pthread.h>
#include <stdarg.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define DEFAULT_PORT   8000
#define DEFAULT_WWW    "www"
#define DEFAULT_CONFIG "/var/lib/operatorconsole/config"

static console_state_t g_state;
static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;
static volatile sig_atomic_t g_running = 1;
static const char *g_config_path = DEFAULT_CONFIG;

void console_lock(void)   { pthread_mutex_lock(&g_lock); }
void console_unlock(void) { pthread_mutex_unlock(&g_lock); }
console_state_t *console_state(void) { return &g_state; }

const char *console_layout_name(console_layout_t layout)
{
    switch (layout) {
    case LAYOUT_LEFT:   return "left";
    case LAYOUT_BOTTOM: return "bottom";
    case LAYOUT_RIGHT:
    default:            return "right";
    }
}

bool console_layout_parse(const char *name, console_layout_t *out)
{
    if (!name || !out) return false;
    if (strcmp(name, "left") == 0)   { *out = LAYOUT_LEFT;   return true; }
    if (strcmp(name, "right") == 0)  { *out = LAYOUT_RIGHT;  return true; }
    if (strcmp(name, "bottom") == 0) { *out = LAYOUT_BOTTOM; return true; }
    return false;
}

static void set_message(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_state.message, sizeof(g_state.message), fmt, ap);
    va_end(ap);
}

/* Pushes console state onto the pins in an order that cannot pop the speakers.
 *
 * Mute first, always. The amplifier must never be live while the mux is
 * mid-change, and it must never come out of mute before the routing is
 * settled — either would put a click through the speakers. */
static void apply_audio(void)
{
    hw_set_mute(true);

    if (g_state.active_lane == CONSOLE_NO_LANE) {
        hw_set_mic(false);
        hw_set_led(false);
        return;
    }

    hw_select_lane(g_state.active_lane);
    hw_set_mic(g_state.mic);
    hw_set_mute(g_state.mute);
    hw_set_led(g_state.mic);
}

int console_select_lane(int lane)
{
    if (lane < CONSOLE_NO_LANE || lane > g_state.lane_count) {
        return -1;
    }

    /* Tapping the lane already selected releases it. */
    if (lane == g_state.active_lane) {
        lane = CONSOLE_NO_LANE;
    }

    g_state.active_lane = lane;

    if (lane == CONSOLE_NO_LANE) {
        /* Releasing closes everything: mic off, speakers muted. */
        g_state.mic  = false;
        g_state.mute = true;
        apply_audio();
        set_message("No lane selected");
    } else {
        /* Selecting opens them. */
        g_state.mic  = true;
        g_state.mute = false;
        apply_audio();
        set_message("Lane %d live", lane);
    }

    return 0;
}

int console_set_mic(bool live)
{
    if (g_state.active_lane == CONSOLE_NO_LANE) {
        return -1;  /* nothing routed to open a microphone into */
    }

    g_state.mic = live;
    hw_set_mic(live);
    hw_set_led(live);
    set_message("Lane %d mic %s", g_state.active_lane, live ? "live" : "off");
    return 0;
}

int console_set_mute(bool muted)
{
    if (g_state.active_lane == CONSOLE_NO_LANE) {
        return -1;
    }

    g_state.mute = muted;
    hw_set_mute(muted);
    set_message("Lane %d speakers %s", g_state.active_lane,
                muted ? "muted" : "on");
    return 0;
}

int console_set_volume(int percent)
{
    if (percent < 0 || percent > 100) {
        return -1;
    }

    g_state.volume = percent;

    if (hw_set_volume(percent) < 0 && hw_present()) {
        /* Say so rather than reporting a success the speakers will not
         * reflect. The XVF3800 gain register is not implemented yet. */
        set_message("Volume %d — not applied (no gain driver)", percent);
    } else {
        set_message("Volume %d", percent);
    }

    console_config_save(g_config_path);
    return 0;
}

int console_set_brightness(int percent)
{
    if (percent < 0 || percent > 100) {
        return -1;
    }

    g_state.brightness = percent;
    hw_set_brightness(percent);
    console_config_save(g_config_path);
    return 0;
}

int console_set_lane_count(int count)
{
    if (count < CONSOLE_MIN_LANES || count > CONSOLE_MAX_LANES) {
        return -1;
    }

    g_state.lane_count = count;

    /* The selected lane can be the one that just disappeared. Release rather
     * than silently re-point: the operator chose a lane that no longer exists,
     * and moving a live microphone to a different one without being asked is
     * the wrong recovery. */
    if (g_state.active_lane > count) {
        g_state.active_lane = CONSOLE_NO_LANE;
        g_state.mic  = false;
        g_state.mute = true;
        apply_audio();
    }

    set_message("%d lane%s configured", count, count == 1 ? "" : "s");
    console_config_save(g_config_path);
    return 0;
}

int console_set_layout(console_layout_t layout)
{
    g_state.layout = layout;
    set_message("Controls moved to %s", console_layout_name(layout));
    console_config_save(g_config_path);
    return 0;
}

/* ---------------- config persistence ---------------- */

int console_config_load(const char *path)
{
    FILE *f = fopen(path, "r");
    if (!f) {
        return -1;  /* first boot: defaults stand */
    }

    char line[128];
    while (fgets(line, sizeof(line), f)) {
        char key[64], value[64];
        if (sscanf(line, "%63[^=]=%63s", key, value) != 2) {
            continue;
        }

        if (strcmp(key, "lanes") == 0) {
            int n = atoi(value);
            if (n >= CONSOLE_MIN_LANES && n <= CONSOLE_MAX_LANES) {
                g_state.lane_count = n;
            }
        } else if (strcmp(key, "layout") == 0) {
            console_layout_parse(value, &g_state.layout);
        } else if (strcmp(key, "volume") == 0) {
            int v = atoi(value);
            if (v >= 0 && v <= 100) g_state.volume = v;
        } else if (strcmp(key, "brightness") == 0) {
            int v = atoi(value);
            if (v >= 0 && v <= 100) g_state.brightness = v;
        }
    }

    fclose(f);
    return 0;
}

int console_config_save(const char *path)
{
    /* Write to a temporary file and rename, so a power cut mid-write leaves
     * the previous config intact rather than a truncated one. */
    char tmp[256];
    snprintf(tmp, sizeof(tmp), "%s.tmp", path);

    FILE *f = fopen(tmp, "w");
    if (!f) {
        return -1;
    }

    fprintf(f, "lanes=%d\n",      g_state.lane_count);
    fprintf(f, "layout=%s\n",     console_layout_name(g_state.layout));
    fprintf(f, "volume=%d\n",     g_state.volume);
    fprintf(f, "brightness=%d\n", g_state.brightness);

    fflush(f);
    fsync(fileno(f));
    fclose(f);

    return rename(tmp, path);
}

/* ---------------- startup ---------------- */

static void on_signal(int sig)
{
    (void)sig;
    g_running = 0;
}

static void defaults(void)
{
    /* No lane selected, mic off, amplifier muted — matching the config.txt
     * boot state (gpio=25=op,dh), so the console agrees with the hardware from
     * the first byte it serves. */
    g_state.lane_count  = 4;
    g_state.active_lane = CONSOLE_NO_LANE;
    g_state.mic         = false;
    g_state.mute        = true;
    g_state.level       = -1;
    g_state.volume      = 60;
    g_state.brightness  = 80;
    g_state.layout      = LAYOUT_RIGHT;
    snprintf(g_state.message, sizeof(g_state.message), "No lane selected");
}

static void usage(const char *argv0)
{
    fprintf(stderr,
            "usage: %s [--port N] [--www DIR] [--config PATH]\n"
            "  --port    HTTP port (default %d)\n"
            "  --www     directory of static assets (default %s)\n"
            "  --config  operator settings file (default %s)\n",
            argv0, DEFAULT_PORT, DEFAULT_WWW, DEFAULT_CONFIG);
}

int main(int argc, char **argv)
{
    unsigned short port = DEFAULT_PORT;
    const char *www = DEFAULT_WWW;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--port") == 0 && i + 1 < argc) {
            port = (unsigned short)atoi(argv[++i]);
        } else if (strcmp(argv[i], "--www") == 0 && i + 1 < argc) {
            www = argv[++i];
        } else if (strcmp(argv[i], "--config") == 0 && i + 1 < argc) {
            g_config_path = argv[++i];
        } else {
            usage(argv[0]);
            return 2;
        }
    }

    /* SIGPIPE would otherwise kill the process when a browser disconnects
     * mid-response. */
    signal(SIGPIPE, SIG_IGN);
    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);

    defaults();

    if (hw_init() < 0) {
        fprintf(stderr, "operatorconsole: hardware init failed\n");
        return 1;
    }

    console_config_load(g_config_path);

    /* Bring the board to match the state we just restored. */
    hw_set_brightness(g_state.brightness);
    hw_set_volume(g_state.volume);
    apply_audio();

    if (webserver_start(port, www) < 0) {
        fprintf(stderr, "operatorconsole: could not start HTTP server on port %u\n",
                port);
        hw_shutdown();
        return 1;
    }

    printf("operatorconsole: serving %s on http://127.0.0.1:%u (hardware: %s)\n",
           www, port, hw_present() ? "present" : "absent");
    fflush(stdout);

    /* The HTTP server runs on its own threads; this one only waits for a
     * signal and then tears down in the right order. */
    while (g_running) {
        pause();
    }

    printf("\noperatorconsole: shutting down\n");
    webserver_stop();
    hw_shutdown();
    return 0;
}
