/* Console state, shared between main.c (which owns it) and webserver.c (which
 * reads and mutates it from libmicrohttpd's handler threads).
 *
 * Every access goes through console_lock()/console_unlock(). MHD runs handlers
 * on its own threads, so state that looks single-threaded from the UI's point
 * of view is not.
 *
 * Audio state is console-wide, not per-lane, because the hardware is: one
 * amplifier mute (GPIO25), one microphone enable (GPIO24), one master volume.
 * A lane is a routing destination — selecting one points the mux at it and
 * opens the mic and speakers; releasing it closes them again.
 */

#ifndef OPERATORCONSOLE_CONSOLE_H
#define OPERATORCONSOLE_CONSOLE_H

#include <stdbool.h>

#define CONSOLE_MIN_LANES 1
#define CONSOLE_MAX_LANES 8

/* No lane selected. Nothing is routed, the mic is off and the amplifier is
 * muted — the same state the board boots into. */
#define CONSOLE_NO_LANE   0

typedef enum {
    LAYOUT_LEFT,
    LAYOUT_RIGHT,
    LAYOUT_BOTTOM,
} console_layout_t;

typedef struct {
    int              lane_count;
    int              active_lane;    /* 1-based, or CONSOLE_NO_LANE */

    /* Console-wide audio. Driven by lane selection, overridable by hand while
     * a lane is held. */
    bool             mic;            /* microphone enabled */
    bool             mute;           /* amplifier muted */
    int              level;          /* 0-100, or -1 when no reading */

    int              volume;         /* master, 0-100 */
    int              brightness;     /* 0-100 */
    console_layout_t layout;
    char             message[128];
} console_state_t;

void console_lock(void);
void console_unlock(void);

/* The live state. Only touch it while holding the lock. */
console_state_t *console_state(void);

/* Selecting a lane routes it and opens the mic and speakers. Passing
 * CONSOLE_NO_LANE — or the lane already selected — releases it and closes them.
 * Call with the lock held. Returns 0, or -1 on a lane outside the count. */
int console_select_lane(int lane);

/* Manual overrides while a lane is held. Both fail with -1 when no lane is
 * selected: there is nothing routed to open a microphone into. */
int console_set_mic(bool live);
int console_set_mute(bool muted);

int console_set_volume(int percent);
int console_set_brightness(int percent);
int console_set_lane_count(int count);
int console_set_layout(console_layout_t layout);

/* Operator settings that must survive a power cycle: lane count, layout,
 * volume and brightness. Deliberately NOT the selected lane — a console that
 * came back from a power cut with a microphone already open would be a nasty
 * surprise. */
int  console_config_load(const char *path);
int  console_config_save(const char *path);

const char *console_layout_name(console_layout_t layout);
bool        console_layout_parse(const char *name, console_layout_t *out);

#endif /* OPERATORCONSOLE_CONSOLE_H */
