/* HTTP layer, on libmicrohttpd.
 *
 * Two jobs: serve the static front end out of www/, and implement the /api
 * contract documented in README.md. No JSON library — the payloads are a
 * handful of flat objects, and the guide lists libmicrohttpd as the only
 * application library, so both directions are done by hand here.
 */

#include "webserver.h"
#include "console.h"
#include "hardware/hw.h"

#include <fcntl.h>
#include <microhttpd.h>
#include <netinet/in.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

/* MHD changed the handler return type to an enum in 0.9.71. */
#if defined(MHD_VERSION) && MHD_VERSION >= 0x00097002
typedef enum MHD_Result mhd_result_t;
#else
typedef int mhd_result_t;
#endif

#define MAX_BODY 4096

static struct MHD_Daemon *g_daemon;
static char g_www_root[512];

/* Accumulates a POST body across the callbacks MHD delivers it in. */
typedef struct {
    char   data[MAX_BODY];
    size_t len;
} body_buf_t;

/* ---------------- tiny JSON reader ----------------
 *
 * Enough for flat objects of numbers, booleans and short strings, which is all
 * the API accepts. Anything it cannot parse is reported as absent, and every
 * caller treats absent as "reject the request" rather than "use a default" —
 * a malformed command must not be guessed at. */

static const char *json_find(const char *body, const char *key)
{
    char pattern[64];
    snprintf(pattern, sizeof(pattern), "\"%s\"", key);

    const char *p = strstr(body, pattern);
    if (!p) {
        return NULL;
    }

    p = strchr(p + strlen(pattern), ':');
    if (!p) {
        return NULL;
    }

    p++;
    while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r') {
        p++;
    }
    return p;
}

static bool json_int(const char *body, const char *key, int *out)
{
    const char *p = json_find(body, key);
    if (!p) {
        return false;
    }

    char *end = NULL;
    long v = strtol(p, &end, 10);
    if (end == p) {
        return false;
    }

    *out = (int)v;
    return true;
}

static bool json_bool(const char *body, const char *key, bool *out)
{
    const char *p = json_find(body, key);
    if (!p) {
        return false;
    }

    if (strncmp(p, "true", 4) == 0)  { *out = true;  return true; }
    if (strncmp(p, "false", 5) == 0) { *out = false; return true; }
    return false;
}

static bool json_str(const char *body, const char *key, char *out, size_t cap)
{
    const char *p = json_find(body, key);
    if (!p || *p != '"') {
        return false;
    }

    p++;
    const char *end = strchr(p, '"');
    if (!end) {
        return false;
    }

    size_t len = (size_t)(end - p);
    if (len >= cap) {
        return false;
    }

    memcpy(out, p, len);
    out[len] = '\0';
    return true;
}

/* ---------------- tiny JSON writer ----------------
 *
 * Lane labels, the screen title and log lines all reach us from the on-screen
 * keyboard, so they are operator-supplied text going straight into a JSON
 * document. A quote or a backslash in a lane name would otherwise break every
 * poll the UI makes. */

static void json_escape(char *out, size_t cap, const char *in)
{
    size_t o = 0;

    for (const unsigned char *p = (const unsigned char *)in; *p; p++) {
        /* Room for the longest expansion (a six-character escape) plus
         * the terminator. */
        if (o + 7 >= cap) {
            break;
        }

        if (*p == '"' || *p == '\\') {
            out[o++] = '\\';
            out[o++] = (char)*p;
        } else if (*p < 0x20) {
            o += (size_t)snprintf(out + o, cap - o, "\\u%04x", *p);
        } else {
            out[o++] = (char)*p;
        }
    }

    out[o] = '\0';
}

/* Appending builder, so the status document can be assembled a lane at a time
 * instead of as one unreadable format string. Overflow stops the append rather
 * than truncating mid-token — a short document is caught by the UI's JSON
 * parse, a mangled one might not be. */
typedef struct {
    char  *buf;
    size_t cap;
    size_t len;
    bool   full;
} sbuf_t;

static void sb_init(sbuf_t *sb, char *buf, size_t cap)
{
    sb->buf = buf;
    sb->cap = cap;
    sb->len = 0;
    sb->full = false;
    buf[0] = '\0';
}

static void sb_add(sbuf_t *sb, const char *fmt, ...)
{
    if (sb->full) {
        return;
    }

    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(sb->buf + sb->len, sb->cap - sb->len, fmt, ap);
    va_end(ap);

    if (n < 0 || (size_t)n >= sb->cap - sb->len) {
        sb->buf[sb->len] = '\0';
        sb->full = true;
        return;
    }

    sb->len += (size_t)n;
}

/* ---------------- responses ---------------- */

static mhd_result_t send_json(struct MHD_Connection *conn, unsigned int status,
                              const char *json)
{
    struct MHD_Response *res = MHD_create_response_from_buffer(
        strlen(json), (void *)json, MHD_RESPMEM_MUST_COPY);
    if (!res) {
        return MHD_NO;
    }

    MHD_add_response_header(res, "Content-Type", "application/json");
    /* The device is a kiosk showing one page; caching only ever serves a stale
     * UI or a stale reading. */
    MHD_add_response_header(res, "Cache-Control", "no-store");

    mhd_result_t rc = MHD_queue_response(conn, status, res);
    MHD_destroy_response(res);
    return rc;
}

static mhd_result_t send_ok(struct MHD_Connection *conn)
{
    return send_json(conn, MHD_HTTP_OK, "{\"ok\":true}");
}

static mhd_result_t send_error(struct MHD_Connection *conn, unsigned int status,
                               const char *message)
{
    char buf[256];
    snprintf(buf, sizeof(buf), "{\"error\":\"%s\"}", message);
    return send_json(conn, status, buf);
}

/* ---------------- GET /api/status ---------------- */

static mhd_result_t handle_status(struct MHD_Connection *conn)
{
    /* Twelve lanes with labels and owners, plus the theme, is the largest this
     * gets. Sized with room to spare because the UI polls it once a second and
     * a truncated document would blank the screen. */
    char json[4096];
    char esc[256];
    sbuf_t sb;

    /* Audio is console-wide — one mic, one amplifier — so there is one reading,
     * and it only means anything while a lane is held. */
    int level = hw_audio_level();
    if (level < 0) {
        level = 0;
    }

    sb_init(&sb, json, sizeof(json));

    console_lock();
    console_state_t *s = console_state();

    if (s->active_lane == CONSOLE_NO_LANE || s->mute || s->held) {
        level = 0;
    }

    sb_add(&sb, "{\"online\":%s", s->online ? "true" : "false");

    json_escape(esc, sizeof(esc), s->title);
    sb_add(&sb, ",\"title\":\"%s\"", esc);

    /* Lanes are an array, not a count: every console's view of every lane —
     * including the ones other consoles hold — arrives here. */
    sb_add(&sb, ",\"lanes\":[");
    for (int i = 0; i < s->lane_count; i++) {
        const lane_t *lane = &s->lanes[i];

        json_escape(esc, sizeof(esc), lane->label);
        sb_add(&sb, "%s{\"id\":%d,\"label\":\"%s\"", i ? "," : "", lane->id, esc);

        json_escape(esc, sizeof(esc), lane->tag);
        sb_add(&sb, ",\"tag\":\"%s\",\"status\":\"%s\"", esc,
               console_lane_status_name(lane->status));

        /* null, not "", so the UI can tell "nobody holds it" from a console
         * whose name happens to be blank. */
        if (lane->owner[0]) {
            json_escape(esc, sizeof(esc), lane->owner);
            sb_add(&sb, ",\"owner\":\"%s\"", esc);
        } else {
            sb_add(&sb, ",\"owner\":null");
        }

        sb_add(&sb, ",\"out_volume\":%d}", lane->out_volume);
    }
    sb_add(&sb, "]");

    sb_add(&sb, ",\"lane\":%d,\"held\":%s,\"mic\":%s,\"mute\":%s",
           s->active_lane,
           s->held ? "true" : "false",
           s->mic  ? "true" : "false",
           s->mute ? "true" : "false");

    sb_add(&sb, ",\"level\":%d,\"volume\":%d,\"brightness\":%d",
           level, s->volume, s->brightness);

    json_escape(esc, sizeof(esc), s->audio_out);
    sb_add(&sb, ",\"audio_out\":\"%s\"", esc);

    /* Only modules that are fitted, so the UI never draws a button for hardware
     * this console does not have. */
    sb_add(&sb, ",\"modules\":[");
    for (int i = 0; i < s->module_count; i++) {
        json_escape(esc, sizeof(esc), s->modules[i].id);
        sb_add(&sb, "%s{\"id\":\"%s\"", i ? "," : "", esc);
        json_escape(esc, sizeof(esc), s->modules[i].name);
        sb_add(&sb, ",\"name\":\"%s\"}", esc);
    }
    sb_add(&sb, "]");

    sb_add(&sb, ",\"layout\":\"%s\",\"hardware\":%s",
           console_layout_name(s->layout),
           hw_present() ? "true" : "false");

    /* Validated at load, so it goes out as written. An empty file leaves the
     * stylesheet's compiled defaults in place. */
    sb_add(&sb, ",\"theme\":%s", s->theme[0] ? s->theme : "{}");

    /* User management is designed for but not in v1. */
    sb_add(&sb, ",\"user_mgmt\":false");

    json_escape(esc, sizeof(esc), s->message);
    sb_add(&sb, ",\"message\":\"%s\"}", esc);

    bool full = sb.full;
    console_unlock();

    if (full) {
        return send_error(conn, MHD_HTTP_INTERNAL_SERVER_ERROR,
                          "status too large");
    }

    return send_json(conn, MHD_HTTP_OK, json);
}

/* ---------------- GET /api/logs ---------------- */

static mhd_result_t handle_logs(struct MHD_Connection *conn)
{
    /* 50 entries at 96 characters, escaped. Heap rather than a static buffer:
     * MHD answers on several threads and two troubleshooting screens polling at
     * once would otherwise write over each other's document. */
    enum { LOG_JSON_MAX = 16384 };
    char *json = malloc(LOG_JSON_MAX);
    if (!json) {
        return send_error(conn, MHD_HTTP_INTERNAL_SERVER_ERROR, "out of memory");
    }

    char esc[256];
    sbuf_t sb;

    sb_init(&sb, json, LOG_JSON_MAX);
    sb_add(&sb, "{\"entries\":[");

    console_lock();
    console_state_t *s = console_state();

    /* Newest first: the troubleshooting screen is read top-down by someone who
     * wants to know what just happened. */
    for (int i = s->log_count - 1; i >= 0; i--) {
        const log_entry_t *entry = &s->logs[i];

        json_escape(esc, sizeof(esc), entry->when);
        sb_add(&sb, "%s{\"t\":\"%s\"", i == s->log_count - 1 ? "" : ",", esc);

        json_escape(esc, sizeof(esc), entry->level);
        sb_add(&sb, ",\"level\":\"%s\"", esc);

        json_escape(esc, sizeof(esc), entry->text);
        sb_add(&sb, ",\"text\":\"%s\"}", esc);
    }

    sb_add(&sb, "]}");
    bool full = sb.full;
    console_unlock();

    /* send_json copies, so the buffer is ours to free either way. */
    mhd_result_t rc = full
        ? send_error(conn, MHD_HTTP_INTERNAL_SERVER_ERROR, "logs too large")
        : send_json(conn, MHD_HTTP_OK, json);

    free(json);
    return rc;
}

/* ---------------- POST command endpoints ---------------- */

static mhd_result_t handle_command(struct MHD_Connection *conn, const char *url,
                                   const char *body)
{
    mhd_result_t rc;
    int value = 0;
    bool on = false;
    char text[64];

    console_lock();

    if (strcmp(url, "/api/lane") == 0) {
        /* 0 releases the lane; so does re-selecting the one already held.
         *
         * This is a REQUEST, not a decision. Another console may already be
         * talking to the lane, in which case it is refused and the UI has to
         * show why — so the refusal carries the Master Controller's reason
         * rather than a generic error. */
        const char *reason = "no such lane";

        if (!json_int(body, "lane", &value)) {
            rc = send_error(conn, MHD_HTTP_BAD_REQUEST, "bad lane");
        } else if (console_select_lane(value, &reason) == 0) {
            rc = send_ok(conn);
        } else {
            rc = send_error(conn, MHD_HTTP_CONFLICT, reason);
        }

    } else if (strcmp(url, "/api/hold") == 0) {
        /* Parking keeps the lane ours but drops audio. Another console may then
         * take it — that is the difference between hold and busy. */
        rc = (json_bool(body, "on", &on) && console_set_hold(on) == 0)
                 ? send_ok(conn)
                 : send_error(conn, MHD_HTTP_CONFLICT, "no lane selected");

    } else if (strcmp(url, "/api/mic") == 0) {
        /* No lane id: there is one microphone enable, and it only opens while
         * a lane is held. */
        rc = (json_bool(body, "on", &on) && console_set_mic(on) == 0)
                 ? send_ok(conn)
                 : send_error(conn, MHD_HTTP_CONFLICT, "no lane selected");

    } else if (strcmp(url, "/api/mute") == 0) {
        rc = (json_bool(body, "on", &on) && console_set_mute(on) == 0)
                 ? send_ok(conn)
                 : send_error(conn, MHD_HTTP_CONFLICT, "no lane selected");

    } else if (strcmp(url, "/api/volume") == 0) {
        /* Master volume: no lane id, which is what makes it structurally
         * impossible to apply console volume to one lane by mistake. */
        rc = (json_int(body, "value", &value) && console_set_volume(value) == 0)
                 ? send_ok(conn)
                 : send_error(conn, MHD_HTTP_BAD_REQUEST, "bad value");

    } else if (strcmp(url, "/api/brightness") == 0) {
        rc = (json_int(body, "value", &value) &&
              console_set_brightness(value) == 0)
                 ? send_ok(conn)
                 : send_error(conn, MHD_HTTP_BAD_REQUEST, "bad value");

    } else if (strcmp(url, "/api/audio-out") == 0) {
        rc = (json_str(body, "id", text, sizeof(text)) &&
              console_set_audio_out(text) == 0)
                 ? send_ok(conn)
                 : send_error(conn, MHD_HTTP_BAD_REQUEST, "no such module");

    } else if (strcmp(url, "/api/layout") == 0) {
        console_layout_t layout;
        rc = (json_str(body, "position", text, sizeof(text)) &&
              console_layout_parse(text, &layout) &&
              console_set_layout(layout) == 0)
                 ? send_ok(conn)
                 : send_error(conn, MHD_HTTP_BAD_REQUEST, "bad position");

    } else if (strcmp(url, "/api/lanes") == 0) {
        rc = (json_int(body, "count", &value) &&
              console_set_lane_count(value) == 0)
                 ? send_ok(conn)
                 : send_error(conn, MHD_HTTP_BAD_REQUEST, "bad count");

    } else if (strcmp(url, "/api/lane-config") == 0) {
        /* Label, tag and outgoing volume are independent — the config screen
         * sends whichever one the installer just changed. */
        int lane = 0;
        if (!json_int(body, "lane", &lane)) {
            rc = send_error(conn, MHD_HTTP_BAD_REQUEST, "bad lane");
        } else {
            bool ok = true;

            if (json_str(body, "label", text, sizeof(text))) {
                ok = ok && console_set_lane_label(lane, text) == 0;
            }
            if (json_str(body, "tag", text, sizeof(text))) {
                ok = ok && console_set_lane_tag(lane, text) == 0;
            }
            if (json_int(body, "out_volume", &value)) {
                ok = ok && console_set_lane_out_volume(lane, value) == 0;
            }

            rc = ok ? send_ok(conn)
                    : send_error(conn, MHD_HTTP_BAD_REQUEST, "bad lane config");
        }

    } else if (strcmp(url, "/api/title") == 0) {
        rc = (json_str(body, "text", text, sizeof(text)) &&
              console_set_title(text) == 0)
                 ? send_ok(conn)
                 : send_error(conn, MHD_HTTP_BAD_REQUEST, "bad title");

    } else if (strcmp(url, "/api/auth") == 0) {
        /* Passwords are compared here and never leave the device. The UI is
         * told yes or no and nothing else. */
        char scope[16];
        rc = (json_str(body, "scope", scope, sizeof(scope)) &&
              json_str(body, "password", text, sizeof(text)) &&
              console_check_password(scope, text))
                 ? send_ok(conn)
                 : send_error(conn, MHD_HTTP_FORBIDDEN, "incorrect");

    } else {
        rc = send_error(conn, MHD_HTTP_NOT_FOUND, "unknown endpoint");
    }

    console_unlock();
    return rc;
}

/* ---------------- static files ---------------- */

static const char *mime_for(const char *path)
{
    const char *dot = strrchr(path, '.');
    if (!dot) return "application/octet-stream";

    if (strcmp(dot, ".html") == 0) return "text/html; charset=utf-8";
    if (strcmp(dot, ".js") == 0)   return "application/javascript; charset=utf-8";
    if (strcmp(dot, ".css") == 0)  return "text/css; charset=utf-8";
    if (strcmp(dot, ".svg") == 0)  return "image/svg+xml";
    if (strcmp(dot, ".png") == 0)  return "image/png";
    if (strcmp(dot, ".ico") == 0)  return "image/x-icon";
    if (strcmp(dot, ".json") == 0) return "application/json";
    return "application/octet-stream";
}

static mhd_result_t handle_static(struct MHD_Connection *conn, const char *url)
{
    /* Reject anything that could climb out of www/. The console serves a fixed
     * set of files from one directory; there is no case where a request needs
     * a parent path, so refusing outright beats trying to normalise. */
    if (strstr(url, "..") != NULL) {
        return send_error(conn, MHD_HTTP_FORBIDDEN, "forbidden");
    }

    const char *rel = (strcmp(url, "/") == 0) ? "/index.html" : url;

    char path[1024];
    if ((size_t)snprintf(path, sizeof(path), "%s%s", g_www_root, rel) >=
        sizeof(path)) {
        return send_error(conn, MHD_HTTP_URI_TOO_LONG, "path too long");
    }

    int fd = open(path, O_RDONLY);
    if (fd < 0) {
        return send_error(conn, MHD_HTTP_NOT_FOUND, "not found");
    }

    struct stat st;
    if (fstat(fd, &st) < 0 || !S_ISREG(st.st_mode)) {
        close(fd);
        return send_error(conn, MHD_HTTP_NOT_FOUND, "not found");
    }

    struct MHD_Response *res =
        MHD_create_response_from_fd((size_t)st.st_size, fd);
    if (!res) {
        close(fd);
        return MHD_NO;
    }

    MHD_add_response_header(res, "Content-Type", mime_for(path));
    MHD_add_response_header(res, "Cache-Control", "no-store");

    mhd_result_t rc = MHD_queue_response(conn, MHD_HTTP_OK, res);
    MHD_destroy_response(res);
    return rc;
}

/* ---------------- dispatch ---------------- */

static mhd_result_t on_request(void *cls, struct MHD_Connection *conn,
                               const char *url, const char *method,
                               const char *version, const char *upload_data,
                               size_t *upload_data_size, void **con_cls)
{
    (void)cls;
    (void)version;

    if (strcmp(method, MHD_HTTP_METHOD_GET) == 0) {
        if (strcmp(url, "/api/status") == 0) {
            return handle_status(conn);
        }
        if (strcmp(url, "/api/logs") == 0) {
            return handle_logs(conn);
        }
        if (strncmp(url, "/api/", 5) == 0) {
            return send_error(conn, MHD_HTTP_NOT_FOUND, "unknown endpoint");
        }
        return handle_static(conn, url);
    }

    if (strcmp(method, MHD_HTTP_METHOD_POST) != 0) {
        return send_error(conn, MHD_HTTP_METHOD_NOT_ALLOWED, "method not allowed");
    }

    /* MHD calls this three times for a POST: once to let us set up, once (or
     * more) with body data, and once with none left to signal the end. */
    body_buf_t *buf = *con_cls;
    if (!buf) {
        buf = calloc(1, sizeof(*buf));
        if (!buf) {
            return MHD_NO;
        }
        *con_cls = buf;
        return MHD_YES;
    }

    if (*upload_data_size != 0) {
        size_t room = sizeof(buf->data) - buf->len - 1;
        size_t take = *upload_data_size < room ? *upload_data_size : room;
        memcpy(buf->data + buf->len, upload_data, take);
        buf->len += take;
        buf->data[buf->len] = '\0';
        *upload_data_size = 0;
        return MHD_YES;
    }

    mhd_result_t rc = handle_command(conn, url, buf->data);
    free(buf);
    *con_cls = NULL;
    return rc;
}

static void on_request_completed(void *cls, struct MHD_Connection *conn,
                                 void **con_cls, enum MHD_RequestTerminationCode toe)
{
    (void)cls;
    (void)conn;
    (void)toe;

    /* A connection dropped mid-upload never reaches the final callback, so the
     * buffer is freed here as well. */
    if (*con_cls) {
        free(*con_cls);
        *con_cls = NULL;
    }
}

int webserver_start(unsigned short port, const char *www_root)
{
    snprintf(g_www_root, sizeof(g_www_root), "%s", www_root);

    /* Loopback only. */
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family      = AF_INET;
    addr.sin_port        = htons(port);
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);

    g_daemon = MHD_start_daemon(
        MHD_USE_INTERNAL_POLLING_THREAD | MHD_USE_ERROR_LOG,
        port, NULL, NULL,
        &on_request, NULL,
        MHD_OPTION_SOCK_ADDR, (struct sockaddr *)&addr,
        MHD_OPTION_CONNECTION_TIMEOUT, (unsigned int)30,
        MHD_OPTION_NOTIFY_COMPLETED, &on_request_completed, NULL,
        MHD_OPTION_END);

    return g_daemon ? 0 : -1;
}

void webserver_stop(void)
{
    if (g_daemon) {
        MHD_stop_daemon(g_daemon);
        g_daemon = NULL;
    }
}
