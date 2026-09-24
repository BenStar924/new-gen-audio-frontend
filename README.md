# Operator Console — front end

Touchscreen UI for the Raspberry Pi Zero 2 W Operator Console, in Hamilton
livery — navy masthead over a white working field, the way the brand is applied
on the building and in print. Static files in
`www/`, served by the C backend (libmicrohttpd) and rendered by Cog / WPE WebKit
in fullscreen kiosk mode.

This directory is the device front end. It is **not** part of the Next.js
coach-platform app and shares nothing with it — no npm runtime dependency, no
React. ESLint ignores this directory.

> **Building the backend?** Start with [HANDOFF.md](HANDOFF.md): what the
> backend must provide, in what order, and `contract-test.py` to tell you when
> it's done. `./make-release.sh` packages everything into one tarball.

```
operatorconsole/
  HANDOFF.md            # START HERE if you are building the backend
  contract-test.py      # the API contract as a test: passes = the UI works
  make-release.sh       # -> dist/operatorconsole-ui-<version>.tar.gz
  deploy/
    install.sh                      # puts the UI on the device, boots into it
    operatorconsole-kiosk.service   # Cog fullscreen on tty1, no login
    operatorconsole.service         # backend unit (template for the C backend)
  src/
    main.c              # entry point, console state, config persistence
    webserver.c/.h      # libmicrohttpd: static files + /api endpoints
    console.h           # shared state, guarded by a mutex
    hardware/
      hw.h              # the hardware interface
      gpio.c            # libgpiod v2 + PWM backlight
      i2c.c             # /dev/i2c-1
      spi.c             # /dev/spidev0.*
  www/
    index.html          # markup + Alpine bindings
    app.js              # Alpine component: state, polling, actions
    vendor/
      app.css           # built, purged Tailwind — do not edit
      alpine.min.js     # vendored Alpine 3.15.0
  input.css             # Tailwind source (v4 CSS-first config)
  build-css.sh          # one-time CSS build
  dev-server.py         # mock backend for front-end work (not deployed)
  kiosk.sh              # cog launch + backend wait + auto-restart
  CMakeLists.txt
  toolchain-pi.cmake
  README.md
```

## Building the application

On the Pi (or any Linux box, for development):

```sh
sudo apt install libmicrohttpd-dev libgpiod-dev cmake
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
./build/operatorconsole --port 8000 --www www
```

**libgpiod v2 is required.** Raspberry Pi OS Trixie ships 2.2.x as
`libgpiod-dev`; the v1 API is neither available on Trixie nor compatible, and
CMake enforces the floor (`libgpiod>=2.0`) rather than letting it surface as a
wall of compile errors. Note `libgpiod3` (runtime) is installed by default but
`libgpiod-dev` (headers, linker symlink, pkg-config metadata) is not.

Cross-compiling from WSL2 for the board:

```sh
sudo apt install crossbuild-essential-arm64
cmake -B build-pi -DCMAKE_TOOLCHAIN_FILE=toolchain-pi.cmake \
      -DPI_SYSROOT=$HOME/pi-sysroot
cmake --build build-pi
rsync -az build-pi/operatorconsole www/ pi@console.local:~/operatorconsole/
```

`PI_SYSROOT` needs the target's headers and libraries — rsync `/usr/include`
and `/usr/lib` off a running board. Without it pkg-config reports the build
machine's x86-64 flags and the link fails with what look like missing symbols.

The build runs with `-Wall -Wextra -Wpedantic -Werror`: on a device that runs
unattended for months, a warning is a defect that has not surfaced yet.

Without a `/dev/gpiochip0` the program still runs, reports
`"hardware": false` at `/api/status`, and tracks state without driving pins —
so the UI can be worked on anywhere. It says so rather than pretending.

## In VS Code

Open the `operatorconsole` folder itself as the workspace (not the repo root) —
the tasks and launch configs are scoped to it.

`Ctrl+Shift+P` → **Tasks: Run Task**:

| Task | Needs | What it does |
| --- | --- | --- |
| Run mock backend (no build) | Python 3 | Serves the UI on :8000. Works on Windows. |
| CMake build | Linux / WSL2 | Builds the C application |
| Run operatorconsole (C) | Linux / WSL2 | Builds, then runs the real program |
| Build CSS | Tailwind CLI or npx | Rebuilds `www/vendor/app.css` |

`F5` builds and debugs the C application under gdb. There is a second launch
config for `gdb-multiarch` against `gdbserver` on the board.

**The Live Server extension will not work on its own.** It serves static files,
so the page loads but every `/api/status` poll 404s — the console shows
**NO LINK** and no control does anything. The UI is deliberately backed by a
real API; run the mock backend instead and open `http://127.0.0.1:8000`.

**On Windows, the C application does not build** — libmicrohttpd and libgpiod
are Linux libraries. Use the **Remote – WSL** extension (`Ctrl+Shift+P` →
*WSL: Reopen Folder in WSL*) and build there. The Python mock runs natively on
Windows either way.

## Running it

```sh
python3 dev-server.py                        # http://127.0.0.1:8000, 4 lanes
python3 dev-server.py 8000 --lanes 7         # try an odd lane count
python3 dev-server.py --layout bottom        # start with the control rail
```

`dev-server.py` serves `www/` and implements the same API against in-memory
state, with a simulated audio level so the meter moves. It is a front-end
development tool and is **not deployed** — the C application above is what
ships. Its advantages are the simulated meter, which the C build cannot yet
produce (see below), and the `/api/sim*` endpoints that put lanes into `busy`,
`hold` and `error` without a second console on the wire. Config password `1234`,
developer password `9999`.

The C application takes the same shape:

```sh
./build/operatorconsole --port 8000 --www www \
    --config /var/lib/operatorconsole/config \
    --theme  /var/lib/operatorconsole/theme.json
```

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

## Lanes and audio

A console has **1–12 lanes**. A lane is a **routing destination**, not an audio
channel — because the hardware has one of everything: one amplifier mute
(GPIO25), one microphone enable (GPIO24), one master volume.

So the model is:

- **Selecting a lane opens it.** The mux points at that lane, the microphone
  goes live and the speakers un-mute.
- **Tapping the held lane again releases it.** Mic off, speakers muted — the
  same state the board boots into. The held tile's label reads `RELEASE`
  rather than `LANE n`, because tapping again is the only way to let go and
  that is not otherwise discoverable.
- **Tapping a different lane** moves straight there; no need to release first.
- **With no lane held, the mic and speaker buttons are disabled** and
  `/api/mic` and `/api/mute` return `409 no lane selected`. There is nothing
  routed to open a microphone into.
- **Mic and speakers can still be overridden by hand while a lane is held**,
  which is what lets an operator cut the microphone without giving up the lane.
  Selecting any lane re-opens both.

Ordering is deliberate wherever the amplifier is touched: **mute first, always**.
The amp must never be live while the mux is mid-change, and must not come out
of mute before routing settles — either puts a click through the speakers.

The selected lane is **not** persisted across a restart. A console that came
back from a power cut with a microphone already open would be a nasty surprise.

### Lane states and who owns a lane

Several consoles compete for the same lanes, and **the Master Controller
arbitrates** — this console does not decide what it gets. Each lane arrives in
`/api/status` with a status:

| `status` | Meaning | Selectable? |
| --- | --- | --- |
| `idle` | Nobody has it | yes |
| `self` | **This** console is talking to it | yes — tapping releases |
| `busy` | Another console is talking to it | **no** |
| `hold` | Another console parked it | **yes** — taking it is the point of hold |
| `error` | Faulted, out of service | no, greyed |

`busy` and `hold` differ precisely because the spec asks them to: a lane someone
is *talking* to must not be seized, while a lane someone *parked* is exactly the
one a second operator should be able to pick up.

Because ownership is granted rather than assumed, **a tap is a request**. The UI
posts to `/api/lane` and waits for the next poll to confirm, holding a brief
pending state in between. A refused grab comes back `409` with the reason in the
body, and that reason is what the status line shows — it never flickers as
success first.

Volume is split the way the hardware is: **one console-wide volume** for what
the operator hears (there is one amplifier), and a **per-lane outgoing volume**
for what the customer hears at the lane, set once at install on the
configuration screen.

**Hold** parks the held lane. Audio drops — mic off, speakers muted, meter at
zero — but the lane stays this console's until another console takes it.

### Sizing correction

The lane count is site-configurable *and* the lane area changes shape with the
control position, so the grid can't be a fixed template. `app.js` picks the
column count that squares up best for both, and the flex rule in `input.css`
lets a short final row stretch to fill the width, so odd counts read as
deliberate rather than ragged:

| Lanes | Controls at a side | Controls at the bottom |
| --- | --- | --- |
| 1 | selector hidden | selector hidden |
| 2 | 2 | 2 |
| 3 | 2 + 1 | 3 |
| 4 | 2 + 2 | 2 + 2 |
| 5 | 2 + 2 + 1 | 3 + 2 |
| 6 | 3 + 3 | 3 + 3 |

Two mappings because the shape differs: with controls at the bottom the lane
area is wide and short, so lanes spread out; at a side it is narrower and
taller, so fewer columns keeps tiles from going skinny. At one lane the selector
is hidden — a one-option selector is noise — and the audio card is capped and
centred rather than stretched edge to edge.

**Past six lanes the grid splits into tabs**, six per tab, and the table above
applies within a tab. A lane on the tab you are not looking at can be live,
busy or faulted, so **each tab header carries a status dot** showing the worst
state among its lanes — otherwise tabs would hide exactly the thing the operator
needs to see.

Each lane tile carries a **colour tag as a small corner square**, configured per
lane. A square in the corner rather than the tile's fill, so a site's colour
coding cannot collide with the state colours above.

## Control position

The mic / speakers / volume cluster can sit on the **left**, the **right** or
the **bottom**, chosen by the operator in Settings and persisted by the backend.
The panel does not always sit square in front of whoever is using it — mounted
to one side of a machine, reach is not symmetric.

It is one layout, not three: only the flex direction of `<main>` changes, and
the lane grid and control cluster are the same markup in every position. In the
bottom position the cluster becomes a true rail — its heading and meter turn
vertical rather than eating the height the lane grid needs.

Verified at 800×480 in all three positions across every lane count 1–12 and
both tabs, driven against the C backend with pointer events: no ragged rows, no
tile clipped past its card's padding box, no overflow, and every control at or
above the 44px minimum (smallest lane tile 208×85).

Lane count is set on the configuration screen and persisted by the backend. The
UI clamps 1–12 and posts the new count; the backend owns the real list, since it
has to add or drop hardware state.

## Screens

Four screens live in **one document**, switched by an Alpine `screen` value.
No page loads: a reload costs seconds of WPE startup, which is the wrong price
for opening a settings screen.

| Screen | Reached from | Gate |
| --- | --- | --- |
| `main` | — | none |
| `troubleshooting` | Settings | **none** — a technician in front of a broken console should not need a password to read the logs |
| `config` | Settings | password |
| `developer` | Troubleshooting | separate password |

Passwords are checked by the backend at `/api/auth` and **never sent to the UI**.
Leaving a protected screen re-locks it: an installer who walks away should not
leave the configuration open behind them.

**No physical keyboard exists on a deployed console**, so every text and number
entry goes through the on-screen keyboard modal — numeric pad or full alpha,
chosen per field. Keys are 56px and above.

## Theming — colours are data, not code

**Marketing and Sales have not approved a palette, and production is late 2027.**
So no colour is baked into the build. Tailwind v4 theme tokens compile to CSS
custom properties, which means a palette is a **runtime value**: the backend
serves one and `app.js` applies it with `setProperty`. Marketing can restyle the
product without a developer touching the code, and development does not wait on
approval.

A theme file is a flat JSON object of token names **without** the `--color-`
prefix, which the UI adds:

```json
{ "navy": "#7A1F3D", "lane-self": "#7A1F3D", "lane-self-line": "#4E1226" }
```

Pass one with `--theme PATH` (default `/var/lib/operatorconsole/theme.json`).
It is validated at load — a file that is not a JSON object is ignored with a log
line rather than breaking every poll — and served verbatim in `theme`. Values
are filtered UI-side to colour-shaped strings. With no theme file the
stylesheet's compiled defaults stand.

### Brand colour — a picker, not a hand-painted palette

The installer does not author a whole palette. The configuration screen offers a
row of vetted **hues** and a **light / base / dark** shade for the chosen one;
`app.js` **derives** the six chrome/lane tokens from that single colour and posts
them with the selection to `/api/brand`, which stores both and serves the theme
back. A custom hex is available for an exact brand match and runs through the
same derivation.

The derivation is where the legibility guarantee lives: the on-chrome text colour
(`--color-onchrome`) is chosen from the chrome's brightness, so a **light** shade
becomes a pale masthead with dark text — never white text on a pale ground — and
**base**/**dark** shades are darkened until white text clears WCAG 4.5:1. That is
what lets a customer have their brand colour without anyone producing an
unreadable console.

> The contrast guarantee currently lives in `app.js` (the only renderer), and the
> backend stores whatever derived theme it is sent. If a second client is ever
> added, move the derivation + contrast check server-side so a bad theme cannot
> be posted directly. The `/api/brand` endpoint should also be behind the
> configuration password in production.

A **template** for a vertical (Bank, Pharmacy) is then just a theme file plus a
title and lane labels. There is no layout branch per vertical, so templates do
not multiply the test matrix.

## API contract

The front end holds no authority over hardware state. It polls `GET
/api/status` once a second, mirrors what comes back, and POSTs commands. This is
the contract shared with the C backend; `dev-server.py` implements the same one.
`contract-test.py` checks a backend against it — both of those pass.

**Status codes are part of the contract.** `400` for a malformed request
(missing field, wrong type, out of range: rejected, never clamped); `409` for a
well-formed request that is not possible right now (no lane held, lane busy);
`403` only for a wrong password; `404` for an unknown endpoint.

`GET /api/status` → JSON. Every field optional; the UI keeps its current value
for anything omitted, so a partial response never blanks a control.

| Field | Type | Meaning |
| --- | --- | --- |
| `api` | int | Contract version, currently `1`. The UI warns on screen if it differs from its own |
| `online` | bool | Master Controller link up |
| `title` | string | Configurable screen title |
| `lanes` | **array** | One object per lane (below) |
| `lane` | int | Lane held by **this** console, 1-based — **0 means none** |
| `held` | bool | This console's lane is parked on hold |
| `mic` | bool | Microphone enabled (GPIO24) — console-wide |
| `mute` | bool | Amplifier muted (GPIO25, active high) — console-wide |
| `level` | int | 0–100 for the meter; 0 with no lane held or on hold |
| `volume` | int | 0–100 master volume — what the **operator** hears |
| `brightness` | int | 0–100 |
| `audio_out` | string | `speaker` or a module id |
| `modules` | array | Add-on modules **fitted**: `{id, name}`, 0–2 |
| `layout` | string | Control position: `left`, `right` or `bottom` |
| `theme` | object | Colour tokens, applied at runtime |
| `brand` | object | `{base, shade}` — the chosen hue and `light`/`base`/`dark`, so the picker shows what is applied |
| `user_mgmt` | bool | Reserved; `false` in v1 |
| `hardware` | bool | False when running without a gpiochip |
| `message` | string | Status line text |

Each entry in `lanes`:

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | int | 1-based |
| `label` | string | Configurable name, ≤23 chars |
| `tag` | string | Colour tag: `none`/`red`/`blue`/`green`/`yellow`/`purple` |
| `status` | string | `idle`/`self`/`busy`/`hold`/`error` |
| `owner` | string \| null | Which console holds it, for display |
| `out_volume` | int | 0–100 heard **at the lane** |

`GET /api/logs` → `{"entries": [{"t", "level", "text"}]}`, **newest first**,
last 50 only. The SD card is not a log server.

Commands, all `POST` with a JSON body, all returning `{"ok": true}`:

| Endpoint | Body | Notes |
| --- | --- | --- |
| `/api/lane` | `{"lane": 3}` | A **request**. `0` — or the lane already held — releases. `409` + reason when the lane is busy or faulted |
| `/api/hold` | `{"on": true}` | Parks the held lane. `409` when none is held |
| `/api/mic` | `{"on": true}` | No lane id. `409` when no lane is held |
| `/api/mute` | `{"on": false}` | No lane id. `409` when no lane is held |
| `/api/volume` | `{"value": 65}` | Console-wide |
| `/api/brightness` | `{"value": 80}` | |
| `/api/audio-out` | `{"id": "headset"}` | `400` for a module that is not fitted |
| `/api/layout` | `{"position": "bottom"}` | |
| `/api/lanes` | `{"count": 6}` | Shrinking below the held lane releases it |
| `/api/lane-config` | `{"lane": 2, "label": …, "tag": …, "out_volume": …}` | Each field independent |
| `/api/title` | `{"text": "Main Branch"}` | |
| `/api/brand` | `{"base": "#1F5FB0", "shade": "light", "theme": {…}}` | Stores the choice and the UI-derived palette. `400` on a bad shade or a `theme` that is not a JSON object |
| `/api/auth` | `{"scope": "config", "password": "…"}` | `403` on a wrong password. Scope is `config` or `developer` |

No command except `/api/lane`, `/api/lane-config` and `/api/sim` carries a lane
id, which is what makes it structurally impossible to apply a console-wide
setting to a single lane by mistake.

`dev-server.py` additionally implements `/api/sim`, `/api/sim-online`,
`/api/sim-theme` and `/api/sim-modules` so every lane state is reachable without
a real multi-console site. **The C backend does not** — those are a development
tool, not a device feature.

Controls are optimistic: a tap updates the UI immediately, then POSTs. The next
poll reconciles against the hardware's real state, so a rejected or failed
command corrects itself within a second rather than leaving the UI lying about
the amplifier. Requests abort after 2s so a hung call can't stack up behind the
poll interval. **Lane selection is the exception** — it is arbitrated, so it
waits for confirmation rather than assuming.

## Device configuration file

`--config PATH` (default `/var/lib/operatorconsole/config`), written atomically
via a temp file and rename so a power cut leaves the previous config intact:

```
lanes=6
layout=bottom
title=Main Branch
volume=60
brightness=80
audio_out=headset
module1=headset:Headset
lane2_label=Bay A
lane2_tag=red
lane2_out=55
pw_config=1234
pw_developer=9999
```

`module1`/`module2` declare which add-on modules are fitted, and the UI draws a
button only for what is listed — **an absent module's button never renders**.
This is where hardware detection replaces the installer setting when the probe
lands; nothing above it has to change.

**The default passwords in the source are placeholders.** A real deployment sets
`pw_config` and `pw_developer` in this file at install.

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

## Pin map — check this against the wiring

Everything this program drives, in one place. **Three of these are inferences,
not statements from the environment guide** — they are the only places the code
and the board can silently disagree, so they are marked and should be confirmed
before first power-on.

| Function | BCM | Header pin | Direction | Boot state | Source |
| --- | --- | --- | --- | --- | --- |
| Amplifier mute | GPIO25 | 22 | out | **HIGH = muted** | guide (`gpio=25=op,dh`) |
| Mic enable | GPIO24 | 18 | out | LOW = off | guide (`gpio=24=op,dl`) |
| XVF3800 reset | GPIO19 | 35 | out | LOW = de-asserted | guide (`gpio=19=op,dl`) |
| Lane mux A | GPIO12 | 32 | out | LOW | **inferred** |
| Lane mux B | GPIO16 | 36 | out | LOW | **inferred** |
| Status LED | GPIO26 | 37 | out | LOW = off | **inferred** |
| Backlight PWM | GPIO18 | 12 | PWM | — | guide (`dtoverlay=pwm,pin=18,func=2`) |
| I2C1 SDA / SCL | GPIO2 / GPIO3 | 3 / 5 | — | — | guide (`dtparam=i2c_arm=on`) |
| SPI0 MOSI/MISO/SCLK | GPIO10/9/11 | 19/21/23 | — | — | guide (`dtparam=spi=on`) |
| SPI0 CE0 / CE1 | GPIO8 / GPIO7 | 24 / 26 | driver-owned | idle high | guide — **never driven by this code** |

### The three inferences

`config.txt` has one line covering five pins:

```
gpio=12,16,22,26,27=op,dl   # mux A/B, pin 15, LED, pin 13 -> low
```

The comment lists four labels for five pins, so the mapping is read positionally:
GPIO12 and GPIO16 are mux A and B, GPIO22 is "pin 15" (its own header number),
GPIO26 is the LED, GPIO27 is "pin 13". That reading is consistent — GPIO22 *is*
header pin 15 and GPIO27 *is* header pin 13 — but it is still a reading.

**If the LED is not GPIO26, or the mux lines are not GPIO12/16, or A and B are
swapped, this code drives the wrong pins.** All three live in `src/hardware/hw.h`
as one-line `#define`s; nothing else needs to change.

### Pins deliberately left alone

`config.txt` also configures GPIO4, 5, 6, 13, 17, 20, 21, 22, 23 and 27 —
inputs with pull-ups, a rising-edge input, an external-pull-up input, and two
unnamed outputs. **This program requests none of them.** They are free for the
hardware engineer to claim without conflicting with the console: libgpiod
requests are exclusive, so two processes cannot hold the same line.

### Lane count vs. mux width — flag this before boards are made

Two select lines (GPIO12/16) address **four** lanes. `hw_select_lane()` refuses
anything above 4 and logs why, rather than wrapping lane 5 onto lane 1 — but the
GUI spec calls for **twelve**, which needs **four select lines**.
`HW_MUX_LANES` in `hw.h` must match whatever is actually wired. This is a
hardware change, not a software one, and it is cheap now and expensive after a
board spin.

### What the UART setup is for

`config.txt` sets `enable_uart=1`, `dtoverlay=disable-bt` to give GPIO14/15 the
real PL011 as `ttyAMA0`, and `cmdline.txt` drops `console=serial0,115200` so a
getty does not hold the port. **This program never opens it.** The guide says
the hardware layer owns the UART; that part is not written. If something needs
serial, it is a new `src/hardware/uart.c` plus a few entries in `hw.h`.

## What the hardware layer does not do yet

Two things are deliberately **unimplemented rather than faked**, because the
environment guide does not document the XVF3800 register map:

- `hw_audio_level()` returns -1 (no reading). The meter therefore reads zero on
  the real backend. `dev-server.py` simulates a level; the C build does not.
- `hw_set_volume()` returns -1 on real hardware. The value is accepted, stored
  and persisted, and the status line says *"not applied (no gain driver)"*.

A meter that invents a level, or a volume control that reports success without
moving anything, is worse than one that admits it has no reading — the UI was
built specifically to show what reaches the speakers.

Also note `hw_select_lane()` refuses lanes above 4. The mux is two select lines
(GPIO12/16 per config.txt), so it addresses four lanes; a site configured for
more needs additional select lines in hardware. Silently routing lane 5 to
lane 1 would be worse than refusing.

## Not viable as specified

**The embedded LLM ("a small AI like llama") cannot run on this board.** The
Pi Zero 2 W has 512 MB and could not start Chromium — the environment guide
documents sustained swap thrashing at 16–39 MB/s. The smallest useful quantised
model needs several times this board's total RAM, and the device is offline, so
a hosted model is out too.

The recommendation is a **static troubleshooting decision tree** — "if this
symptom, check these three things". It is a few KB, needs no inference, runs
instantly, and is more predictable for a field technician than a small model's
guesses. A wrong confident answer about why a bank's drive-thru is down is worse
than no answer.

**Launching a terminal needs a decision.** Cog runs with no window manager, so
there is no window for a terminal to open into. The realistic options are for
the backend to `chvt` to a VT with a getty (needs privileges), or to run a
terminal emulator under `cage` alongside the kiosk. A browser-based terminal is
the wrong answer on a device that ships to banks. The developer screen currently
shows the button **disabled with that explanation on screen**, rather than a
control that does nothing.

**Logo upload has no path yet.** The kiosk has no file manager. Either the
configuration screen enumerates a USB stick, or logos are pushed during install
over the network. Whichever it is, size and format limits must be enforced by
the backend — not by trusting the UI.

## Open questions

- **Does console 1 hold the only configuration screens?** Unresolved in the
  spec. Since the Master Controller already arbitrates lanes, it is the natural
  place to hold site configuration too — that avoids a console-to-console
  protocol existing only for settings. Right now every console has its own.
- **User management.** `user_mgmt` is reserved in the API and `false` in v1.
  Who are the users — operators, installers, technicians — and does a login
  gate the main screen or only the protected screens? A console an operator has
  to log into every shift is a different product from one that is always on.
- **What else belongs on the main screen?** There's room beside the audio card,
  and at a single lane most of the screen is free.
- **Should control position be per-operator or per-site?** It is currently one
  device-wide setting, like lane count. If two people share a console on
  different shifts they will fight over it.
- **Faults.** Failures currently land in the status line. A hardware fault
  probably deserves something louder.
- **Brightness** is in the API but has no backend behind it yet — it needs a
  backlight path on the device.
- **Detailed audio configuration** on the developer screen is marked TBD in the
  spec and is not built.
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
