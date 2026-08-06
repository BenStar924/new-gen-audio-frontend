/* Operator Console — application entry point and console state.
 *
 * Brings up the hardware in its safe state, restores operator settings, starts
 * the HTTP server that serves www/ and the /api endpoints, then waits. All the
 * interesting behaviour is in webserver.c (HTTP) and hardware/ (pins).
 *
 * A note on the lane model, because it is the one place where the UI and the
 * hardware do not obviously agree:
 *
 * The UI keeps mic and mute state for every lane. The board has ONE amplifier
 * mute (GPIO25) and ONE microphone enable (GPIO24). So only the ACTIVE lane's
 * state can be physically applied; the other lanes' settings are remembered
 * and applied when that lane is selected. Selecting a lane therefore does
 * three things: it moves the mux, then it pushes that lane's remembered mic
 * and mute state onto the single pair of control lines.
 *
 * That is the only coherent reading of a per-lane UI on single-channel
 * hardware, and it is worth confirming against the actual wiring: if mic and
 * speakers are meant to be console-wide — as master volume turned out to be —
 * then the per-lane state here should collapse to a single pair and the UI
 * should stop drawing per-lane indicators.
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

/* Pushes the given lane's remembered state onto the single mic/mute pair. */
static void apply_active_lane(void)
{
    const lane_t *lane = &g_state.lanes[g_state.active_lane - 1];

    /* Mute first when muting, un-mute last when un-muting: the amplifier
     * should never be live while the mux is mid-change. */
    hw_set_mute(true);
    hw_select_lane(g_state.active_lane);
    hw_set_mic(lane->mic);
    hw_set_mute(lane->mute);
    hw_set_led(lane->mic);
}

int console_select_lane(int lane)
{
    if (lane < 1 || lane > g_state.lane_count) {
        return -1;
    }

    g_state.active_lane = lane;
    apply_active_lane();
    set_message("Lane %d active", lane);
    return 0;
}

int console_set_mic(int lane, bool live)
{
    if (lane < 1 || lane > g_state.lane_count) {
        return -1;
    }

    g_state.lanes[lane - 1].mic = live;

    /* Only the active lane reaches the hardware — there is one mic enable. */
    if (lane == g_state.active_lane) {
        hw_set_mic(live);
        hw_set_led(live);
    }

    set_message("Lane %d mic %s", lane, live ? "live" : "off");
    return 0;
}

int console_set_mute(int lane, bool muted)
{
    if (lane < 1 || lane > g_state.lane_count) {
        return -1;
    }

    g_state.lanes[lane - 1].mute = muted;

    if (lane == g_state.active_lane) {
        hw_set_mute(muted);
    }

    set_message("Lane %d speakers %s", lane, muted ? "muted" : "on");
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

    /* Lanes appearing start in the safe state: mic off, speakers muted. */
    for (int i = g_state.lane_count; i < count; i++) {
        g_state.lanes[i].mic   = false;
        g_state.lanes[i].mute  = true;
        g_state.lanes[i].level = -1;
    }

    g_state.lane_count = count;

    /* The active lane can be the one that just disappeared. */
    if (g_state.active_lane > count) {
        console_select_lane(count);
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
    /* Muted matches the config.txt boot state (gpio=25=op,dh), so the console
     * agrees with the hardware from the first byte it serves. */
    for (int i = 0; i < CONSOLE_MAX_LANES; i++) {
        g_state.lanes[i].mic   = false;
        g_state.lanes[i].mute  = true;
        g_state.lanes[i].level = -1;
    }

    g_state.lane_count  = 4;
    g_state.active_lane = 1;
    g_state.volume      = 60;
    g_state.brightness  = 80;
    g_state.layout      = LAYOUT_RIGHT;
    snprintf(g_state.message, sizeof(g_state.message), "Starting…");
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
    apply_active_lane();

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
