# Operator Console — front end

Touchscreen UI for the Raspberry Pi Zero 2 W Operator Console. Static files in
`www/`, served by the C backend (libmicrohttpd) and rendered by Cog / WPE WebKit
in fullscreen kiosk mode.

This directory is the device front end. It is **not** part of the Next.js
coach-platform app and shares nothing with it — no npm, no React, no build step.
ESLint is configured to ignore this directory.

```
operatorconsole/
  www/
    index.html      # markup — the whole screen, no client-side templating
    app.css         # hand-written CSS, no framework
    app.js          # state + render + tap binding
  dev-server.py     # mock backend for development (not deployed)
  kiosk.sh          # cog launch + backend wait + auto-restart
  README.md
```

## Running it

Nothing to install:

```sh
python3 dev-server.py          # http://127.0.0.1:8000
```

`dev-server.py` serves `www/` and implements the API below against in-memory
state, with a simulated audio level so the meter moves. Use it on a laptop, or
on the bench card before the C backend exists.

On the device, once the C backend is serving port 8000:

```sh
./kiosk.sh                     # deployment: cog fullscreen
CONSOLE_CAGE=1 ./kiosk.sh      # bench: under cage, so there is a mouse cursor
```

WPE caches aggressively — if an edit doesn't show up, clear the cache before
relaunching:

```sh
rm -rf ~/.cache/cog ~/.cache/wpe* ~/.local/share/cog
```

## API contract

The front end holds no authority over hardware state. It polls `GET
/api/status` once a second, mirrors what comes back, and POSTs commands. This is
the contract the C backend needs to implement.

`GET /api/status` → JSON. Every field optional; the UI keeps its current value
for anything omitted, so a partial response never blanks a control.

| Field | Type | Meaning |
| --- | --- | --- |
| `lanes` | int | How many lane buttons to show |
| `lane` | int | Selected lane, 1-based |
| `mic` | bool | Microphone enabled (GPIO24) |
| `mute` | bool | Amplifier muted (GPIO25, active high) |
| `volume` | int | 0–100 |
| `brightness` | int | 0–100 |
| `level` | int | 0–100, current input level for the meter |
| `message` | string | Status line text |

Commands, all `POST` with a JSON body, all returning `{"ok": true}`:

| Endpoint | Body |
| --- | --- |
| `/api/lane` | `{"lane": 3}` |
| `/api/mic` | `{"on": true}` |
| `/api/mute` | `{"on": false}` |
| `/api/volume` | `{"value": 65}` |
| `/api/brightness` | `{"value": 80}` |

Controls are optimistic: a tap updates the UI immediately, then POSTs. The next
poll reconciles against the hardware's real state, so a rejected or failed
command corrects itself within a second rather than leaving the UI lying about
the amplifier. Requests abort after 2s so a hung call can't stack up behind the
poll interval.

## Design decisions

**No framework, no build step.** The guide offers Tailwind + Alpine as a
starting point but notes a fixed control panel doesn't need them. This is a
fixed control panel: eight lane buttons, two latching controls, a volume
stepper, a meter and a modal. Hand-written CSS costs ~9 KB and removes the
Tailwind build, the vendored Alpine, and two versions to keep pinned. If the UI
grows enough to want reactivity, vendor Alpine into `www/vendor/` then — the
markup is already attribute-driven and won't need rewriting.

**Nothing is loaded from a network.** No CDN links, no web fonts, no external
icons — the production device is offline and CDN requests hang until timeout
rather than failing fast, which leaves an unstyled, unresponsive UI. Icons are
inline SVG, type is the system font stack. Verify before shipping:

```sh
curl -s http://127.0.0.1:8000 | grep -iE "cdn|https?://"    # expect no output
```

**Taps are bound to `pointerup`, not `click`.** `click` does not fire reliably
under WPE. Each handler is debounced 250ms so a panel that reports one tap as
several pointer events still acts exactly once. Press feedback is a class
toggled on `pointerdown`/`pointercancel` rather than `:active`, which sticks on
some touch panels when a press is cancelled.

**Muted is the default state,** matching the `config.txt` boot state
(`gpio=25=op,dh` — GPIO25 is an active-high mute on the TPA3116D2, set high at
boot so the amplifier doesn't pop through the speakers during startup). The UI
comes up agreeing with the hardware instead of contradicting it for the first
second. The meter reads zero while muted: it reports what reaches the speakers,
not what reaches the ADC.

**Touch targets are 64px minimum,** set by `--touch` in `app.css`. Text
selection, double-tap zoom and tap highlighting are disabled, and the shell
never scrolls.

**No CSS transitions or animations.** The VideoCore GPU only supports OpenGL ES
2.0 and has no frames to spare; press feedback needs to be immediate rather than
eased.

**Layout** is a 2-column grid (lanes | audio) sized for an 800×480 panel,
collapsing to a single column below 640px. Lane buttons and meter segments are
built once at startup and only have classes toggled afterwards — re-creating
them each render would churn the DOM on a board with no headroom for it.

## Open questions

Worth settling before this goes much further:

- **Lane count and labels.** Currently 8 numbered lanes, driven by `lanes` in
  the status response. Real names ("Lane 1"/"Bay A"/something else)?
- **What else belongs on the main screen?** There's room for one more card if
  there are readings the operator needs at a glance.
- **Panel size and orientation.** Sized for 800×480 landscape. If the panel is
  physically rotated, set rotation at the KMS/kernel level, not in Cog.
- **Does the console need to show faults?** Right now failures land in the
  status line. A hardware fault probably deserves something louder.
- **Brightness** is in the API but has no backend behind it yet — it'll need a
  backlight path on the device.

## Before shipping

Measure on a **Lite** card, not the bench Desktop card — desktop benchmarks are
pessimistic and something that works with the desktop present (fonts, GL
libraries, session services) can need an explicit package on Lite. Check cold
start time, `free -h` headroom at idle, and that every control responds to a
single tap by finger, not just by mouse.
