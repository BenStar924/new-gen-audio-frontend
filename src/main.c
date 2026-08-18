/* Operator Console — application entry point and console state.
 *
 * Brings up the hardware in its safe state, restores operator settings, starts
 * the HTTP server that serves www/ and the /api endpoints, then waits. All the
 * interesting behaviour is in webserver.c (HTTP) and hardware/ (pins).
 *
 * The lane model, which the hardware dictates:
 *
 * There is ONE amplifier mute (GPIO25), ONE microphone enable (GPIO24) and one
 * master volume. So audio is console-wide, not per-lane. A lane is a routing
 * destination: selecting one points the mux at it and opens the mic and
 * speakers; releasing it closes them. With no lane held the board sits exactly
 * as it boots — mic off, amplifier muted.
 *
 * Ownership across consoles is arbitrated by the Master Controller, not here.
 * Until that link exists, lane status other than our own is whatever was last
 * pushed in, and a site with one console behaves correctly regardless.
 */

#include "console.h"
#include "hardware/hw.h"
#include "webserver.h"

#include <errno.h>
#include <pthread.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define DEFAULT_PORT   8000
#define DEFAULT_WWW    "www"
#define DEFAULT_CONFIG "/var/lib/operatorconsole/config"
#define DEFAULT_THEME  "/var/lib/operatorconsole/theme.json"

static console_state_t g_state;
static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;
static volatile sig_atomic_t g_running = 1;
static const char *g_config_path = DEFAULT_CONFIG;
static const char *g_theme_path  = DEFAULT_THEME;

/* Set while replaying the config file. The setters below save as they go and
 * log as they go, which is right for an operator changing something and wrong
 * here: a half-replayed file would be written back over the whole one, and the
 * replay would fill the log with events that are really just the old settings
 * being restored. */
static bool g_loading;

/* Placeholder until the installer sets them. Real deployments must not ship
 * these — they belong in the config file the installer writes.
 *
 * Sized to the 32 characters the on-screen keyboard accepts, so a password the
 * installer can type is always a password that fits. */
#define CONSOLE_PASSWORD_MAX 33

static char g_pw_config[CONSOLE_PASSWORD_MAX]    = "1234";
static char g_pw_developer[CONSOLE_PASSWORD_MAX] = "9999";

/* Truncating a password would leave a console nobody can unlock with the
 * password they were given, so an oversized one is refused outright. */
static void set_password(char *slot, const char *value, const char *which)
{
    if (strlen(value) >= CONSOLE_PASSWORD_MAX) {
        console_log("warn", "%s password longer than %d characters, ignored",
                    which, CONSOLE_PASSWORD_MAX - 1);
        return;
    }

    snprintf(slot, CONSOLE_PASSWORD_MAX, "%s", value);
}

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

const char *console_lane_status_name(lane_status_t status)
{
    switch (status) {
    case LANE_SELF:  return "self";
    case LANE_BUSY:  return "busy";
    case LANE_HOLD:  return "hold";
    case LANE_ERROR: return "error";
    case LANE_IDLE:
    default:         return "idle";
    }
}

static void set_message(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_state.message, sizeof(g_state.message), fmt, ap);
    va_end(ap);
}

void console_log(const char *level, const char *fmt, ...)
{
    /* Oldest out when full: the SD card is not a log server, and the
     * troubleshooting screen only ever shows the recent past. */
    if (g_state.log_count == CONSOLE_LOG_MAX) {
        memmove(&g_state.logs[0], &g_state.logs[1],
                sizeof(log_entry_t) * (CONSOLE_LOG_MAX - 1));
        g_state.log_count--;
    }

    log_entry_t *entry = &g_state.logs[g_state.log_count++];

    time_t now = time(NULL);
    struct tm tm_buf;
    localtime_r(&now, &tm_buf);
    strftime(entry->when, sizeof(entry->when), "%Y-%m-%d %H:%M:%S", &tm_buf);

    snprintf(entry->level, sizeof(entry->level), "%s", level);

    va_list ap;
    va_start(ap, fmt);
    vsnprintf(entry->text, sizeof(entry->text), fmt, ap);
    va_end(ap);
}

static lane_t *lane_at(int id)
{
    if (id < 1 || id > g_state.lane_count) return NULL;
    return &g_state.lanes[id - 1];
}

/* Pushes console state onto the pins in an order that cannot pop the speakers.
 *
 * Mute first, always. The amplifier must never be live while the mux is
 * mid-change, and must not come out of mute before routing settles — either
 * puts a click through the speakers. */
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

static void release_lane(void)
{
    lane_t *lane = lane_at(g_state.active_lane);
    if (lane && lane->status == LANE_SELF) {
        lane->status = LANE_IDLE;
        lane->owner[0] = '\0';
    }

    g_state.active_lane = CONSOLE_NO_LANE;
    g_state.held = false;
    g_state.mic  = false;
    g_state.mute = true;
    apply_audio();
    set_message("No lane selected");
}

int console_select_lane(int id, const char **reason)
{
    if (id == CONSOLE_NO_LANE || id == g_state.active_lane) {
        release_lane();
        return 0;
    }

    lane_t *lane = lane_at(id);
    if (!lane) {
        if (reason) *reason = "no such lane";
        return -1;
    }

    /* A lane another console is talking to cannot be taken. One another
     * console has parked CAN be. */
    if (lane->status == LANE_BUSY) {
        if (reason) *reason = "lane in use by another console";
        return -1;
    }
    if (lane->status == LANE_ERROR) {
        if (reason) *reason = "lane out of service";
        return -1;
    }

    release_lane();

    lane->status = LANE_SELF;
    snprintf(lane->owner, sizeof(lane->owner), "this console");

    g_state.active_lane = id;
    g_state.held = false;
    g_state.mic  = true;
    g_state.mute = false;
    apply_audio();

    set_message("%s live", lane->label);
    console_log("info", "Connected to %s", lane->label);
    return 0;
}

int console_set_hold(bool on)
{
    if (g_state.active_lane == CONSOLE_NO_LANE) return -1;

    g_state.held = on;
    /* Parking drops audio but keeps the lane ours until someone takes it. */
    g_state.mic  = !on;
    g_state.mute = on;
    apply_audio();

    lane_t *lane = lane_at(g_state.active_lane);
    set_message("%s %s", lane ? lane->label : "Lane", on ? "on hold" : "live");
    return 0;
}

int console_set_mic(bool live)
{
    if (g_state.active_lane == CONSOLE_NO_LANE) return -1;

    g_state.mic = live;
    hw_set_mic(live);
    hw_set_led(live);
    set_message("Mic %s", live ? "live" : "off");
    return 0;
}

int console_set_mute(bool muted)
{
    if (g_state.active_lane == CONSOLE_NO_LANE) return -1;

    g_state.mute = muted;
    hw_set_mute(muted);
    set_message("Speakers %s", muted ? "muted" : "on");
    return 0;
}

int console_set_volume(int percent)
{
    if (percent < 0 || percent > 100) return -1;

    g_state.volume = percent;

    if (hw_set_volume(percent) < 0 && hw_present()) {
        set_message("Volume %d — not applied (no gain driver)", percent);
    } else {
        set_message("Volume %d", percent);
    }

    console_config_save(g_config_path);
    return 0;
}

int console_set_brightness(int percent)
{
    if (percent < 0 || percent > 100) return -1;

    g_state.brightness = percent;
    hw_set_brightness(percent);
    console_config_save(g_config_path);
    return 0;
}

int console_set_audio_out(const char *id)
{
    if (!id || !*id) return -1;

    /* Only the built-in speaker and modules this console actually has. Routing
     * audio to a module that is not fitted is silence the operator cannot
     * explain, so it is refused rather than accepted and ignored. */
    if (strcmp(id, "speaker") != 0) {
        bool found = false;
        for (int i = 0; i < g_state.module_count; i++) {
            if (strcmp(g_state.modules[i].id, id) == 0) { found = true; break; }
        }
        if (!found) return -1;
    }

    snprintf(g_state.audio_out, sizeof(g_state.audio_out), "%s", id);
    set_message("Audio via %s", id);
    console_config_save(g_config_path);
    return 0;
}

int console_set_layout(console_layout_t layout)
{
    g_state.layout = layout;
    console_config_save(g_config_path);
    return 0;
}

int console_set_lane_count(int count)
{
    if (count < CONSOLE_MIN_LANES || count > CONSOLE_MAX_LANES) return -1;

    for (int i = g_state.lane_count; i < count; i++) {
        lane_t *lane = &g_state.lanes[i];
        lane->id = i + 1;
        snprintf(lane->label, sizeof(lane->label), "Lane %d", i + 1);
        snprintf(lane->tag, sizeof(lane->tag), "none");
        lane->status = LANE_IDLE;
        lane->owner[0] = '\0';
        lane->out_volume = 60;
    }

    g_state.lane_count = count;

    /* The held lane can be the one that just disappeared. Release rather than
     * re-point: moving a live microphone somewhere the operator did not ask
     * for is the wrong recovery. */
    if (g_state.active_lane > count) {
        release_lane();
    }

    set_message("%d lane%s configured", count, count == 1 ? "" : "s");

    /* Replaying the saved lane count at boot is not an event worth a line in a
     * 50-entry log; an installer changing it is. */
    if (!g_loading) {
        console_log("info", "Lane count set to %d", count);
    }
    console_config_save(g_config_path);
    return 0;
}

int console_set_lane_label(int id, const char *label)
{
    lane_t *lane = lane_at(id);
    if (!lane || !label) return -1;

    /* A name longer than the tile can show is truncated rather than refused —
     * unlike a password, a shortened lane name still identifies the lane. */
    snprintf(lane->label, sizeof(lane->label), "%.*s",
             (int)sizeof(lane->label) - 1, label);
    console_config_save(g_config_path);
    return 0;
}

int console_set_lane_tag(int id, const char *tag)
{
    static const char *known[] = {"none", "red", "blue", "green",
                                  "yellow", "purple", NULL};
    lane_t *lane = lane_at(id);
    if (!lane || !tag) return -1;

    for (int i = 0; known[i]; i++) {
        if (strcmp(tag, known[i]) == 0) {
            snprintf(lane->tag, sizeof(lane->tag), "%s", tag);
            console_config_save(g_config_path);
            return 0;
        }
    }
    return -1;
}

int console_set_lane_out_volume(int id, int percent)
{
    lane_t *lane = lane_at(id);
    if (!lane || percent < 0 || percent > 100) return -1;

    lane->out_volume = percent;
    console_config_save(g_config_path);
    return 0;
}

int console_set_title(const char *text)
{
    if (!text) return -1;

    snprintf(g_state.title, sizeof(g_state.title), "%s", text);
    console_config_save(g_config_path);
    return 0;
}

bool console_check_password(const char *scope, const char *password)
{
    if (!scope || !password) return false;

    const char *expected = NULL;
    if (strcmp(scope, "config") == 0)         expected = g_pw_config;
    else if (strcmp(scope, "developer") == 0) expected = g_pw_developer;
    if (!expected) return false;

    bool ok = (strcmp(password, expected) == 0);
    console_log(ok ? "info" : "warn", "%s unlock %s",
                scope, ok ? "granted" : "refused");
    return ok;
}

/* Records an add-on module as fitted. Set by the installer for now; when the
 * hardware engineer's detection lands this is where a probe result goes, and
 * nothing above it has to change. Format in the config file:
 *
 *     module1=headset:Headset
 */
static int add_module(const char *spec)
{
    if (g_state.module_count >= CONSOLE_MODULE_MAX) return -1;

    const char *colon = strchr(spec, ':');
    if (!colon || colon == spec) return -1;

    module_t *m = &g_state.modules[g_state.module_count];

    size_t id_len = (size_t)(colon - spec);
    if (id_len >= sizeof(m->id)) return -1;
    memcpy(m->id, spec, id_len);
    m->id[id_len] = '\0';

    snprintf(m->name, sizeof(m->name), "%s", colon + 1);
    g_state.module_count++;
    return 0;
}

/* ---------------- config persistence ---------------- */

/* Validates a candidate theme and copies it into g_state.theme. It is pasted
 * verbatim into /api/status, so it must be a JSON object and nothing else — a
 * stray control character or a truncated value would break every poll, not just
 * the colours. Trims surrounding whitespace and neutralises control bytes. */
static int store_theme(const char *src, size_t n)
{
    size_t start = 0;
    while (start < n && (unsigned char)src[start] <= ' ') start++;
    while (n > start && (unsigned char)src[n - 1] <= ' ') n--;

    size_t len = n - start;
    if (len < 2 || src[start] != '{' || src[n - 1] != '}') return -1;
    if (len >= sizeof(g_state.theme)) return -1;

    for (size_t i = 0; i < len; i++) {
        char c = src[start + i];
        g_state.theme[i] = ((unsigned char)c < 0x20) ? ' ' : c;
    }
    g_state.theme[len] = '\0';
    return 0;
}

/* Persists the current theme, temp-file-then-rename so a power cut leaves the
 * previous one intact. An empty theme writes "{}", the "no override" marker. */
static int write_theme_file(const char *path)
{
    char tmp[300];
    snprintf(tmp, sizeof(tmp), "%s.tmp", path);

    FILE *f = fopen(tmp, "w");
    if (!f) return -1;

    fputs(g_state.theme[0] ? g_state.theme : "{}", f);
    fflush(f);
    fsync(fileno(f));
    fclose(f);

    return rename(tmp, path);
}

int console_theme_load(const char *path)
{
    g_state.theme[0] = '\0';

    FILE *f = fopen(path, "r");
    if (!f) return -1;      /* no theme file: the compiled defaults stand */

    char buf[CONSOLE_THEME_MAX];
    size_t n = fread(buf, 1, sizeof(buf) - 1, f);
    int too_big = !feof(f);
    fclose(f);
    buf[n] = '\0';

    if (too_big) {
        console_log("warn", "Theme file larger than %d bytes, ignored",
                    (int)sizeof(buf) - 1);
        return -1;
    }

    if (store_theme(buf, n) < 0) {
        g_state.theme[0] = '\0';
        console_log("warn", "Theme file is not a JSON object, ignored");
        return -1;
    }

    console_log("info", "Theme loaded (%d bytes)", (int)strlen(g_state.theme));
    return 0;
}

int console_set_theme(const char *json)
{
    if (!json || store_theme(json, strlen(json)) < 0) return -1;
    write_theme_file(g_theme_path);
    return 0;
}

int console_set_brand(const char *base, const char *shade, const char *theme_json)
{
    if (!shade) return -1;
    if (strcmp(shade, "light") && strcmp(shade, "base") && strcmp(shade, "dark")) {
        return -1;
    }

    /* The derived palette must be valid before the choice is recorded — a
     * refused theme must not leave the brand pointing at nothing. */
    if (console_set_theme(theme_json) < 0) return -1;

    snprintf(g_state.brand_base, sizeof(g_state.brand_base), "%.*s",
             (int)sizeof(g_state.brand_base) - 1, base ? base : "");
    snprintf(g_state.brand_shade, sizeof(g_state.brand_shade), "%s", shade);

    console_config_save(g_config_path);
    console_log("info", "Brand colour set to %s %s",
                g_state.brand_base[0] ? g_state.brand_base : "default", shade);
    return 0;
}

int console_config_load(const char *path)
{
    FILE *f = fopen(path, "r");
    if (!f) return -1;      /* first boot: defaults stand */

    g_loading = true;
    g_state.module_count = 0;

    char line[192];
    while (fgets(line, sizeof(line), f)) {
        char key[64], value[128];
        if (sscanf(line, "%63[^=]=%127[^\n]", key, value) != 2) continue;

        if (strcmp(key, "lanes") == 0) {
            int n = atoi(value);
            if (n >= CONSOLE_MIN_LANES && n <= CONSOLE_MAX_LANES) {
                console_set_lane_count(n);
            }
        } else if (strcmp(key, "layout") == 0) {
            console_layout_parse(value, &g_state.layout);
        } else if (strcmp(key, "volume") == 0) {
            int v = atoi(value);
            if (v >= 0 && v <= 100) g_state.volume = v;
        } else if (strcmp(key, "brightness") == 0) {
            int v = atoi(value);
            if (v >= 0 && v <= 100) g_state.brightness = v;
        } else if (strcmp(key, "title") == 0) {
            /* Explicit precision: a hand-edited config file may carry a longer
             * value than the field, and truncating it is the right answer. */
            snprintf(g_state.title, sizeof(g_state.title), "%.*s",
                     (int)sizeof(g_state.title) - 1, value);
        } else if (strcmp(key, "audio_out") == 0) {
            snprintf(g_state.audio_out, sizeof(g_state.audio_out), "%.*s",
                     (int)sizeof(g_state.audio_out) - 1, value);
        } else if (strcmp(key, "pw_config") == 0) {
            set_password(g_pw_config, value, "Configuration");
        } else if (strcmp(key, "pw_developer") == 0) {
            set_password(g_pw_developer, value, "Developer");
        } else if (strcmp(key, "brand_base") == 0) {
            snprintf(g_state.brand_base, sizeof(g_state.brand_base), "%.*s",
                     (int)sizeof(g_state.brand_base) - 1, value);
        } else if (strcmp(key, "brand_shade") == 0) {
            snprintf(g_state.brand_shade, sizeof(g_state.brand_shade), "%.*s",
                     (int)sizeof(g_state.brand_shade) - 1, value);
        } else if (strncmp(key, "module", 6) == 0) {
            add_module(value);
        } else if (strncmp(key, "lane", 4) == 0) {
            /* laneN_label / laneN_tag / laneN_out */
            int id = atoi(key + 4);
            const char *field = strchr(key, '_');
            if (field && lane_at(id)) {
                field++;
                if (strcmp(field, "label") == 0)      console_set_lane_label(id, value);
                else if (strcmp(field, "tag") == 0)   console_set_lane_tag(id, value);
                else if (strcmp(field, "out") == 0)   console_set_lane_out_volume(id, atoi(value));
            }
        }
    }

    fclose(f);
    g_loading = false;

    /* audio_out is replayed before the module list is known, so it is checked
     * once the whole file has been read. A console whose headset was removed
     * comes back on its own speaker rather than pointing at nothing.
     *
     * Via a copy: console_set_audio_out writes into g_state.audio_out, and
     * handing it its own buffer as the source is overlapping copy. */
    char want[sizeof(g_state.audio_out)];
    snprintf(want, sizeof(want), "%s", g_state.audio_out);

    if (console_set_audio_out(want) < 0) {
        console_log("warn", "Audio output %s not fitted, using speaker", want);
        snprintf(g_state.audio_out, sizeof(g_state.audio_out), "speaker");
    }

    return 0;
}

int console_config_save(const char *path)
{
    if (g_loading) return 0;

    /* Temp file then rename, so a power cut mid-write leaves the previous
     * config intact rather than a truncated one. */
    char tmp[256];
    snprintf(tmp, sizeof(tmp), "%s.tmp", path);

    FILE *f = fopen(tmp, "w");
    if (!f) return -1;

    fprintf(f, "lanes=%d\n",      g_state.lane_count);
    fprintf(f, "layout=%s\n",     console_layout_name(g_state.layout));
    fprintf(f, "volume=%d\n",     g_state.volume);
    fprintf(f, "brightness=%d\n", g_state.brightness);
    fprintf(f, "title=%s\n",      g_state.title);
    fprintf(f, "audio_out=%s\n",  g_state.audio_out);

    if (g_state.brand_base[0])  fprintf(f, "brand_base=%s\n",  g_state.brand_base);
    if (g_state.brand_shade[0]) fprintf(f, "brand_shade=%s\n", g_state.brand_shade);

    for (int i = 0; i < g_state.module_count; i++) {
        fprintf(f, "module%d=%s:%s\n", i + 1,
                g_state.modules[i].id, g_state.modules[i].name);
    }

    for (int i = 0; i < g_state.lane_count; i++) {
        const lane_t *lane = &g_state.lanes[i];
        fprintf(f, "lane%d_label=%s\n", lane->id, lane->label);
        fprintf(f, "lane%d_tag=%s\n",   lane->id, lane->tag);
        fprintf(f, "lane%d_out=%d\n",   lane->id, lane->out_volume);
    }

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
    /* Nothing established here is a change worth writing back — and writing it
     * back would land on top of the config file that has not been read yet. */
    g_loading = true;

    memset(&g_state, 0, sizeof(g_state));

    /* Muted with no lane held matches the config.txt boot state
     * (gpio=25=op,dh), so the console agrees with the hardware from the first
     * byte it serves. */
    g_state.online      = false;
    g_state.active_lane = CONSOLE_NO_LANE;
    g_state.mic         = false;
    g_state.mute        = true;
    g_state.volume      = 60;
    g_state.brightness  = 80;
    g_state.layout      = LAYOUT_RIGHT;
    snprintf(g_state.title, sizeof(g_state.title), "Drive-Thru");
    snprintf(g_state.audio_out, sizeof(g_state.audio_out), "speaker");
    snprintf(g_state.message, sizeof(g_state.message), "No lane selected");

    console_set_lane_count(4);

    g_loading = false;
}

static void usage(const char *argv0)
{
    fprintf(stderr,
            "usage: %s [--port N] [--www DIR] [--config PATH] [--theme PATH]\n",
            argv0);
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
        } else if (strcmp(argv[i], "--theme") == 0 && i + 1 < argc) {
            g_theme_path = argv[++i];
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

    /* defaults() and the first config save are not events. Clearing here means
     * the troubleshooting screen opens on a boot record that starts at boot. */
    g_state.log_count = 0;
    console_log("info", "Console started");

    console_config_load(g_config_path);
    console_theme_load(g_theme_path);

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

    while (g_running) {
        pause();
    }

    printf("\noperatorconsole: shutting down\n");
    webserver_stop();
    hw_shutdown();
    return 0;
}
