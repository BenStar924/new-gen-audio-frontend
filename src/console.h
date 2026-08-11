/* Console state, shared between main.c (which owns it) and webserver.c (which
 * reads and mutates it from libmicrohttpd's handler threads).
 *
 * Every access goes through console_lock()/console_unlock(). MHD runs handlers
 * on its own threads, so state that looks single-threaded from the UI's point
 * of view is not.
 *
 * Audio is console-wide because the hardware is: one amplifier mute (GPIO25),
 * one microphone enable (GPIO24), one master volume. A lane is a routing
 * destination — selecting one points the mux at it and opens the mic and
 * speakers; releasing it closes them again.
 *
 * Lane OWNERSHIP is not ours to decide. Several consoles compete for lanes and
 * the Master Controller arbitrates: it grants a lane, and it tells us which
 * lanes other consoles hold. Everything below marked "from the Master
 * Controller" is received, not decided here.
 */

#ifndef OPERATORCONSOLE_CONSOLE_H
#define OPERATORCONSOLE_CONSOLE_H

#include <stdbool.h>

#define CONSOLE_MIN_LANES 1
#define CONSOLE_MAX_LANES 12
#define CONSOLE_NO_LANE   0

#define CONSOLE_LABEL_MAX 24
#define CONSOLE_TITLE_MAX 32
#define CONSOLE_LOG_MAX   50

/* Two optional add-on audio modules at most, per the GUI spec. */
#define CONSOLE_MODULE_MAX 2

/* A theme is a flat JSON object of colour tokens, held as text and passed
 * straight through to the UI. Marketing has not approved a palette; keeping it
 * as data means approving one later is a file drop, not a firmware release. */
#define CONSOLE_THEME_MAX 1024

typedef enum {
    LAYOUT_LEFT,
    LAYOUT_RIGHT,
    LAYOUT_BOTTOM,
} console_layout_t;

/* LANE_SELF is this console holding it. LANE_BUSY is another console talking
 * and cannot be taken. LANE_HOLD is another console parking it and CAN be
 * taken. LANE_ERROR is unusable. */
typedef enum {
    LANE_IDLE,
    LANE_SELF,
    LANE_BUSY,
    LANE_HOLD,
    LANE_ERROR,
} lane_status_t;

typedef struct {
    int           id;
    char          label[CONSOLE_LABEL_MAX];
    char          tag[16];                  /* colour tag: none/red/blue/... */
    lane_status_t status;
    char          owner[CONSOLE_LABEL_MAX]; /* from the Master Controller */
    int           out_volume;               /* level heard AT the lane, 0-100 */
} lane_t;

typedef struct {
    char   when[24];
    char   level[8];                        /* info | warn | error */
    char   text[96];
} log_entry_t;

/* An add-on module the console physically has. The UI draws a button per entry,
 * so an absent module is absent from this list and its button never renders. */
typedef struct {
    char id[16];                            /* headset | handset | ... */
    char name[CONSOLE_LABEL_MAX];
} module_t;

typedef struct {
    bool             online;                /* link to the Master Controller */
    char             title[CONSOLE_TITLE_MAX];

    lane_t           lanes[CONSOLE_MAX_LANES];
    int              lane_count;
    int              active_lane;           /* 1-based, or CONSOLE_NO_LANE */
    bool             held;                  /* our lane parked on hold */

    bool             mic;
    bool             mute;
    int              volume;                /* master, what the operator hears */
    int              brightness;
    char             audio_out[16];         /* speaker | <module id> */

    module_t         modules[CONSOLE_MODULE_MAX];
    int              module_count;

    console_layout_t layout;
    char             theme[CONSOLE_THEME_MAX];
    char             message[128];

    log_entry_t      logs[CONSOLE_LOG_MAX];
    int              log_count;
} console_state_t;

void console_lock(void);
void console_unlock(void);
console_state_t *console_state(void);

/* Selecting requests a lane and, if granted, opens the mic and speakers.
 * CONSOLE_NO_LANE — or the lane already held — releases it. Returns 0, or -1
 * with *reason set when the Master Controller would refuse. */
int console_select_lane(int lane, const char **reason);

/* Parks the held lane: audio drops, but it stays ours until another console
 * takes it. Fails with -1 when no lane is held. */
int console_set_hold(bool on);

int console_set_mic(bool live);
int console_set_mute(bool muted);
int console_set_volume(int percent);
int console_set_brightness(int percent);
int console_set_audio_out(const char *id);
int console_set_layout(console_layout_t layout);

/* Installer configuration. */
int console_set_lane_count(int count);
int console_set_lane_label(int lane, const char *label);
int console_set_lane_tag(int lane, const char *tag);
int console_set_lane_out_volume(int lane, int percent);
int console_set_title(const char *text);

/* Passwords are checked here and never sent to the UI. */
bool console_check_password(const char *scope, const char *password);

void console_log(const char *level, const char *fmt, ...);

/* Operator settings that survive a power cycle. The HELD LANE deliberately
 * does not: a console returning from a power cut with a microphone already
 * open would be a nasty surprise. */
int  console_config_load(const char *path);
int  console_config_save(const char *path);

/* Reads a theme file into state->theme, to be served verbatim. Rejects anything
 * that is not a flat JSON object so a bad file cannot corrupt /api/status. */
int  console_theme_load(const char *path);

const char *console_layout_name(console_layout_t layout);
bool        console_layout_parse(const char *name, console_layout_t *out);
const char *console_lane_status_name(lane_status_t status);

#endif /* OPERATORCONSOLE_CONSOLE_H */
