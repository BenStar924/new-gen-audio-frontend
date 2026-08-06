# Operator Console — front end

Touchscreen UI for the Raspberry Pi Zero 2 W Operator Console, in Hamilton
livery — navy masthead over a white working field, the way the brand is applied
on the building and in print. Static files in
`www/`, served by the C backend (libmicrohttpd) and rendered by Cog / WPE WebKit
in fullscreen kiosk mode.

This directory is the device front end. It is **not** part of the Next.js
coach-platform app and shares nothing with it — no npm runtime dependency, no
React. ESLint ignores this directory.

```
operatorconsole/
  www/
    index.html          # markup + Alpine bindings
    app.js              # Alpine component: state, polling, actions
    vendor/
      app.css           # built, purged Tailwind — do not edit
      alpine.min.js     # vendored Alpine 3.15.0
  input.css             # Tailwind source (v4 CSS-first config)
  build-css.sh          # one-time CSS build
  dev-server.py         # mock backend for development (not deployed)
  kiosk.sh              # cog launch + backend wait + auto-restart
  README.md
```

## Running it

```sh
python3 dev-server.py                        # http://127.0.0.1:8000, 4 lanes
python3 dev-server.py 8000 --lanes 7         # try an odd lane count
python3 dev-server.py --layout bottom        # start with the control rail
```

`dev-server.py` serves `www/` and implements the API below against in-memory
state, with a simulated per-lane audio level so the meter moves. Use it on a
laptop, or on the bench card before the C backend exists.

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

## Building the CSS

Tailwind emits only the classes it can find in `www/`, so **a class added to the
markup does not exist until you rebuild**:

```sh
./build-css.sh          # writes www/vendor/app.css (~16 KB minified)
```

The script prefers a standalone `./tailwindcss` binary (no Node.js needed on the
build machine — download it from the tailwindlabs/tailwindcss releases page and
`chmod +x`), and falls back to `npx @tailwindcss/cli@4`.

This is **Tailwind v4**, which uses a CSS-first config: the theme and content
globs live in `input.css` and there is no `tailwind.config.js`. A v3 toolchain
will not read it — pin the major version. The device never compiles CSS; the
Play CDN is a runtime in-browser compiler, unreachable offline and wasteful on
this board.

Alpine is vendored at a pinned version. To update it, fetch the same file from a
networked machine and drop it in:

```sh
npm pack alpinejs@3.15.0 && tar xzf alpinejs-3.15.0.tgz package/dist/cdn.min.js
cp package/dist/cdn.min.js www/vendor/alpine.min.js
```

## Lanes

A console has **1–8 lanes**. **Mic and speaker state are per-lane; volume is
not.** There is a single master volume for the whole console.

The mic and speaker controls act on whichever lane is selected, so that card is
headed `LANE n · MIC & SPEAKERS` and the operator never has to guess what
they're about to change. The volume readout is captioned `MASTER VOL` for the
same reason — it sits in the same cluster, so it has to say plainly that it is
not the selected lane's volume.

Because the per-lane controls follow the active lane, each lane button carries
two status dots — mic (green when live) and speakers (amber when un-muted).
That's the only way to see that another lane is live without switching to it.
Lane tiles deliberately show no volume figure: there isn't a per-lane one.

**Sizing correction.** The lane count is site-configurable *and* the lane area
changes shape with the control position, so the grid can't be a fixed template.
`app.js` picks the column count that squares up best for both, and the flex rule
in `input.css` lets a short final row stretch to fill the width, so odd counts
read as deliberate rather than ragged:

| Lanes | Controls at a side | Controls at the bottom |
| --- | --- | --- |
| 1 | selector hidden | selector hidden |
| 2 | 2 | 2 |
| 3 | 2 + 1 | 3 |
| 4 | 2 + 2 | 2 + 2 |
| 5 | 2 + 2 + 1 | 3 + 2 |
| 6 | 3 + 3 | 3 + 3 |
| 7 | 3 + 3 + 1 | 4 + 3 |
| 8 | 3 + 3 + 2 | 4 + 4 |

Two mappings because the shape differs: with controls at the bottom the lane
area is wide and short, so lanes spread out; at a side it is narrower and
taller, so fewer columns keeps tiles from going skinny. At one lane the selector
is hidden — a one-option selector is noise — and the audio card is capped and
centred rather than stretched edge to edge.

Every lane tile carries a small meter and two status dots, so a live lane is
visible without selecting it. When the bottom rail squeezes the grid into more
than one row the `LANE n` sublabel is dropped: the numeral already identifies
the lane, and the label is the first thing worth losing to keep tiles whole.

## Control position

The mic / speakers / volume cluster can sit on the **left**, the **right** or
the **bottom**, chosen by the operator in Settings and persisted by the backend.
The panel does not always sit square in front of whoever is using it — mounted
to one side of a machine, reach is not symmetric.

It is one layout, not three: only the flex direction of `<main>` changes, and
the lane grid and control cluster are the same markup in every position. In the
bottom position the cluster becomes a true rail — its heading and meter turn
vertical rather than eating the height the lane grid needs.

Verified at 800×480 in all three positions across every lane count 1–8: no
ragged rows, no clipped tiles, no overflow, and every control at or above the
64px touch minimum (smallest lane tile 141×78).

Lane count is set by the operator in Settings and persisted by the backend. The
UI clamps 1–8 and posts the new count; the backend owns the real list, since it
has to add or drop hardware state.

## API contract

The front end holds no authority over hardware state. It polls `GET
/api/status` once a second, mirrors what comes back, and POSTs commands. This is
the contract the C backend needs to implement.

`GET /api/status` → JSON. Every field optional; the UI keeps its current value
for anything omitted, so a partial response never blanks a control.

| Field | Type | Meaning |
| --- | --- | --- |
| `lanes` | array | One object per lane (below) |
| `lane` | int | Active lane id |
| `volume` | int | 0–100 — **master volume, console-wide** |
| `layout` | string | Control position: `left`, `right` or `bottom` |
| `brightness` | int | 0–100 |
| `message` | string | Status line text |

Each entry in `lanes`:

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | int | Lane id, 1-based |
| `mic` | bool | Microphone enabled (GPIO24) |
| `mute` | bool | Amplifier muted (GPIO25, active high) |
| `level` | int | 0–100, current level for the meter |

There is no per-lane `volume`. A lane object carrying one is ignored.

Commands, all `POST` with a JSON body, all returning `{"ok": true}`:

| Endpoint | Body | Scope |
| --- | --- | --- |
| `/api/lane` | `{"lane": 3}` | Select active lane |
| `/api/mic` | `{"lane": 3, "on": true}` | Per lane |
| `/api/mute` | `{"lane": 3, "on": false}` | Per lane |
| `/api/volume` | `{"value": 65}` | Console — master volume, no lane id |
| `/api/brightness` | `{"value": 80}` | Console |
| `/api/lanes` | `{"count": 6}` | Console — set lane count |
| `/api/layout` | `{"position": "bottom"}` | Console — control position |

Per-lane commands name their lane explicitly rather than implying "whatever is
selected", so a command can't land on the wrong lane if a selection races with a
poll. `/api/volume` carries no lane id at all, which is what makes it
structurally impossible to apply master volume to one lane by mistake.

Controls are optimistic: a tap updates the UI immediately, then POSTs. The next
poll reconciles against the hardware's real state, so a rejected or failed
command corrects itself within a second rather than leaving the UI lying about
the amplifier. Requests abort after 2s so a hung call can't stack up behind the
poll interval.

## Design decisions

**Nothing is loaded from a network.** Tailwind is built to a static purged
stylesheet and Alpine is vendored at a pinned version, both under `www/vendor/`.
No CDN links, no web fonts, no external icons — the production device is offline
and CDN requests hang until timeout rather than failing fast, which leaves an
unstyled, unresponsive UI. Icons are inline SVG, type is the system font stack,
and the favicon is an inline data URI (without it, every load spends a request on
a `/favicon.ico` that isn't there). Verify before shipping:

```sh
curl -s http://127.0.0.1:8000 | grep -iE "cdn|https?://"    # expect no output
```

**Branding is inline SVG, not an image file.** The header carries mark A from
`mockups/logo-h-marks.html` — the solid navy tile — chosen because it is one of
only two marks that survives at 24px, and the header can fall back that far. To
swap in mark H (the squared steel plate), replace the `<svg>` in the header and
the favicon data URI with:

```html
<rect x="2" y="2" width="44" height="44" rx="4" fill="#1B1A3E"/>
<rect x="6" y="6" width="36" height="36" rx="2" fill="none" stroke="#4A48A0" stroke-width="1.6"/>
<path d="M14.5 13h6v8.6h7V13h6v22h-6v-8.7h-7V35h-6z" fill="#fff"/>
```

Navy is `#2B2A5E`, sampled from the wordmark. **Status colours are deliberately
not brand colours:** mic-live green and speaker amber have to be
distinguishable from navy and from each other at a glance, because they carry
state that matters — a navy "live" indicator would disappear into the chrome.

**Taps are bound to `pointerup`, not `click`,** which does not fire reliably
under WPE. Alpine's `.throttle` modifier is the duplicate-event guard, so a panel
that reports one tap as several pointer events still acts exactly once. Two
timings, deliberately: **250ms on latching controls** (mic, speakers, lane
select), where a double fire flip-flops and looks like nothing happened; **150ms
on steppers** (volume, brightness, lane count), because 250ms also swallowed
deliberate rapid taps. Duplicate pointer events arrive within a few milliseconds,
so 150ms is still a wide guard.

**Press feedback is a delegated pointer listener**, not `:active`, which sticks
on some touch panels when a press is cancelled mid-gesture. It lives outside
Alpine because it's presentation only — routing it through component state would
re-render the tree on every press.

**Muted is the default state,** matching the `config.txt` boot state
(`gpio=25=op,dh` — GPIO25 is an active-high mute on the TPA3116D2, set high at
boot so the amplifier doesn't pop through the speakers during startup). The UI
comes up agreeing with the hardware instead of contradicting it for the first
second. The meter reads zero while muted: it reports what reaches the speakers,
not what reaches the ADC.

**Touch targets are 64px minimum** (`min-h-16` / `min-w-16`). Text selection,
double-tap zoom and tap highlighting are disabled, and the shell never scrolls.

**No CSS transitions or animations.** The VideoCore GPU only supports OpenGL ES
2.0 and has no frames to spare; press feedback needs to be immediate rather than
eased.

## Open questions

- **Lane labels.** Buttons show the lane number. Do lanes have real names at a
  site ("Bay A", a customer name), and if so where do they come from?
- **Who may change the lane count?** It's in Settings with no gate. If an
  operator shouldn't be able to reconfigure the site mid-shift, this needs a PIN
  or to move out of the console UI entirely.
- **What else belongs on the main screen?** There's room beside the audio card,
  and at a single lane most of the screen is free.
- **Are mic and speakers really per-lane?** The hardware guide describes a
  single amplifier mute (GPIO25) and a single mic enable (GPIO24), which points
  at those being console-wide too — the same correction that moved volume out of
  the lane object. Left per-lane for now because only volume was corrected.
- **Should control position be per-operator or per-site?** It is currently one
  device-wide setting, like lane count. If two people share a console on
  different shifts they will fight over it.
- **Faults.** Failures currently land in the status line. A hardware fault
  probably deserves something louder.
- **Brightness** is in the API but has no backend behind it yet — it needs a
  backlight path on the device.
- **Panel size and orientation.** Built for 800×480 landscape. If the panel is
  physically rotated, set rotation at the KMS/kernel level, not in Cog.

## Before shipping

Measure on a **Lite** card, not the bench Desktop card — desktop benchmarks are
pessimistic and something that works with the desktop present (fonts, GL
libraries, session services) can need an explicit package on Lite. Check cold
start time, `free -h` headroom at idle, and that every control responds to a
single tap by finger, not just by mouse.

The guide notes Tailwind and Alpine were never load-tested on this hardware.
They're small vendored (~16 KB CSS, ~44 KB JS raw), but startup time and touch
responsiveness with them in place should be measured on the target before
committing to them.
