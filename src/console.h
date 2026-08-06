/* Console state, shared between main.c (which owns it) and webserver.c (which
 * reads and mutates it from libmicrohttpd's handler threads).
 *
 * Every access goes through console_lock()/console_unlock(). MHD runs handlers
 * on its own threads, so state that looks single-threaded from the UI's point
 * of view is not.
 */

#ifndef OPERATORCONSOLE_CONSOLE_H
#define OPERATORCONSOLE_CONSOLE_H

#include <stdbool.h>

#define CONSOLE_MIN_LANES 1
#define CONSOLE_MAX_LANES 8

typedef enum {
    LAYOUT_LEFT,
    LAYOUT_RIGHT,
    LAYOUT_BOTTOM,
} console_layout_t;

typedef struct {
    bool mic;    /* microphone enabled for this lane */
    bool mute;   /* amplifier muted for this lane */
    int  level;  /* 0-100, or -1 when no reading is available */
} lane_t;

typedef struct {
    lane_t           lanes[CONSOLE_MAX_LANES];
    int              lane_count;
    int              active_lane;    /* 1-based */
    int              volume;         /* master, 0-100 — one per console */
    int              brightness;     /* 0-100 */
    console_layout_t layout;
    char             message[128];
} console_state_t;

void console_lock(void);
void console_unlock(void);

/* The live state. Only touch it while holding the lock. */
console_state_t *console_state(void);

/* Mutators. Each validates, updates state, drives hardware, and sets the
 * status message. Call with the lock held. Return 0 on success, -1 on a
 * rejected argument. */
int console_select_lane(int lane);
int console_set_mic(int lane, bool live);
int console_set_mute(int lane, bool muted);
int console_set_volume(int percent);
int console_set_brightness(int percent);
int console_set_lane_count(int count);
int console_set_layout(console_layout_t layout);

/* Operator settings that must survive a power cycle: lane count, layout,
 * volume and brightness. Config is written on change and read at startup. */
int  console_config_load(const char *path);
int  console_config_save(const char *path);

const char *console_layout_name(console_layout_t layout);
bool        console_layout_parse(const char *name, console_layout_t *out);

#endif /* OPERATORCONSOLE_CONSOLE_H */
