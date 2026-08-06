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
    char json[1024];

    /* Audio is console-wide — one mic, one amplifier — so there is one reading,
     * and it only means anything while a lane is held. */
    int level = hw_audio_level();
    if (level < 0) {
        level = 0;
    }

    console_lock();
    console_state_t *s = console_state();

    if (s->active_lane == CONSOLE_NO_LANE || s->mute) {
        level = 0;
    }

    snprintf(json, sizeof(json),
             "{\"lanes\":%d,\"lane\":%d,\"mic\":%s,\"mute\":%s,"
             "\"level\":%d,\"volume\":%d,\"brightness\":%d,"
             "\"layout\":\"%s\",\"hardware\":%s,\"message\":\"%s\"}",
             s->lane_count, s->active_lane,
             s->mic ? "true" : "false",
             s->mute ? "true" : "false",
             level, s->volume, s->brightness,
             console_layout_name(s->layout),
             hw_present() ? "true" : "false",
             s->message);
    console_unlock();

    return send_json(conn, MHD_HTTP_OK, json);
}

/* ---------------- POST command endpoints ---------------- */

static mhd_result_t handle_command(struct MHD_Connection *conn, const char *url,
                                   const char *body)
{
    mhd_result_t rc;
    int value = 0;
    bool on = false;
    char text[32];

    console_lock();

    if (strcmp(url, "/api/lane") == 0) {
        /* 0 releases the lane; so does re-selecting the one already held. */
        rc = (json_int(body, "lane", &value) && console_select_lane(value) == 0)
                 ? send_ok(conn)
                 : send_error(conn, MHD_HTTP_BAD_REQUEST, "no such lane");

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

    } else if (strcmp(url, "/api/lanes") == 0) {
        rc = (json_int(body, "count", &value) &&
              console_set_lane_count(value) == 0)
                 ? send_ok(conn)
                 : send_error(conn, MHD_HTTP_BAD_REQUEST, "bad count");

    } else if (strcmp(url, "/api/layout") == 0) {
        console_layout_t layout;
        rc = (json_str(body, "position", text, sizeof(text)) &&
              console_layout_parse(text, &layout) &&
              console_set_layout(layout) == 0)
                 ? send_ok(conn)
                 : send_error(conn, MHD_HTTP_BAD_REQUEST, "bad position");

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
