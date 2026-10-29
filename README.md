# HUBS Audio console — front end

Touchscreen UI for the HUBS Audio drive-thru console (Raspberry Pi Zero 2 W,
800×480), in Hamilton livery. Static files in `www/`, served by the backend on
`127.0.0.1:8000` and rendered by Cog / WPE WebKit in fullscreen kiosk mode.

No npm runtime dependency and no framework build: the UI is plain HTML, Alpine
and a prebuilt Tailwind stylesheet, so `www/` runs on the device as-is.

> **Building the backend?** Start with [HANDOFF.md](HANDOFF.md), then
> [API.md](API.md) (the contract). `contract-test.py` tells you when you're
> done. `./make-release.sh` packages everything into one tarball.

```
new-gen-audio-frontend/
  HANDOFF.md            # START HERE if you are building the backend
  API.md                # the API contract, v2
  contract-test.py      # the contract as a test: passes = the UI works
  dev-server.py         # reference backend in Python: all of API v2, no hardware
  make-release.sh       # -> dist/hubs-audio-ui-<version>.tar.gz
  deploy/
    install.sh                      # puts the UI on the device, boots into it
    operatorconsole-kiosk.service   # Cog fullscreen on tty1, no login
    operatorconsole.service         # backend unit template
  www/
    index.html          # every screen: markup + Alpine bindings
    app.js              # the hubsConsole component: state, polling, actions
    vendor/
      app.css           # built, purged Tailwind — do not edit
      alpine.min.js     # vendored Alpine 3.15.0
  input.css             # Tailwind source (v4 CSS-first config)
  build-css.sh          # CSS build
  kiosk.sh              # cog launch + backend wait + auto-restart
  src/, CMakeLists.txt, toolchain-pi.cmake
                        # C hardware reference (API v1, see below)
```

## Running it

```sh
python3 dev-server.py                          # http://127.0.0.1:8000: 6 lanes, 3 consoles, Master
python3 dev-server.py --lanes 15 --consoles 1  # up to 16 devices in total
python3 dev-server.py --role operator          # an Operator console (no Settings)
python3 dev-server.py --login-required         # start at the Locked screen
python3 dev-server.py --demo                   # lanes ring by themselves
```

Mock users (name / PIN / role): `Alex Teller` 1111 user · `Sam Tech` 2222
technician · `Pat Admin` 3333 admin · `Jo Owner` 4444 owner · `Former Teller`
5555 disabled. Developer password `9999`. With login off, every console is the
built-in `Hamilton Audio` owner.

`/api/sim/...` endpoints (lane, device, console, outputs, features, login,
reset…) put the system into any state without real hardware. They are a
development tool and **not** part of the contract. See `dispatch_sim` in
`dev-server.py`.

On the device, once a backend is serving port 8000:

```sh
./kiosk.sh                     # deployment: cog fullscreen
CONSOLE_CAGE=1 ./kiosk.sh      # bench: under cage, so there is a mouse cursor
```

WPE caches aggressively. `deploy/install.sh` clears the caches; by hand:
`rm -rf ~/.cache/cog ~/.cache/wpe* ~/.local/share/cog`.

## Screens

One document, one Alpine component (`hubsConsole`). The screen on show is the
`view` getter, **derived from backend state**, so it can't drift from it: a
call always takes the whole screen, and a locked console always shows Locked.

| View | Shown when | Who |
| --- | --- | --- |
| Locked | `login_required` and nobody logged in | Username (checked at once); the 10-key PIN pad appears once the name is valid. Lane lights, clock, and Answer buttons when `answer_without_pin` is on |
| Main | Default | Lane grid: one big white button per lane with its number, and a round call light in the lane's colour with a phone icon inside, showing the call status (it pulses while ringing). No status text. One touch answers or calls |
| Active call | `status.call` is set | Mute mic, Hold, volume, output, Hang up. A restricted call gets volume and Hang up only |
| Troubleshooting | Masthead wrench | This console's heartbeats and codes; Download log, Help, Restart software. Master: the A2B chain diagram and Restart lane audio |
| Help | Troubleshooting → Help | Manuals as page images (WPE has no PDF viewer) |
| Developer | Long-press the logo (2 s) on Locked or Troubleshooting, then password | Terminal *or* App log, one per visit |
| Settings | Masthead gear: **Master only**, technician and up | Lanes (number, colour), Consoles (defaults), Users (admin/owner), Site (login, auto-lock), Audio (tier-1, TBD), Backup |
| Screensaver | 5 min idle, not during a call or a ring | Any touch wakes it (and is swallowed); a ring clears it |

**Role gating goes through one check**, `can(capability)`, against the
`CAPABILITIES` table at the top of `app.js`. Nothing in the markup asks "is this
the master?" directly. The backend enforces the same table (API.md).

**Device status has one source**: `status.devices`. The lane grid, the status
light, the chain diagram and "this console's codes" are all derived from it.

**The call light communicates by behaviour, never colour**: unlit (no call),
fast blink with a pulsing ring (ringing), lit (in my call), dim (busy on
another console), slow blink (on hold, system-wide). The light is in the
lane's colour, which is its identity and matches the physical lane LED; the
colour shows in every state, dulled when unlit.

> **Deviation from the design doc:** the doc has a separate identity graphic
> above the LED pushbutton. By request, each lane is one white button with
> its number, the round call light (lane colour, phone icon) sits inside it,
> and the whole button is the tap target.

**The status light** is red on any device error or failed heartbeat (`i2c`,
`a2b`, `master_link`), yellow on warnings only, and green otherwise.

## Building the CSS

Tailwind emits only the classes it can find in `www/`, so **a class added to the
markup does not exist until you rebuild**:

```sh
./build-css.sh          # writes www/vendor/app.css (~20 KB minified)
```

The script prefers a standalone `./tailwindcss` binary and falls back to
`npx @tailwindcss/cli@4`. This is **Tailwind v4**: the config lives in
`input.css` and there is no `tailwind.config.js`. The device never compiles CSS.

Alpine is vendored at a pinned version. To update it:

```sh
npm pack alpinejs@3.15.0 && tar xzf alpinejs-3.15.0.tgz package/dist/cdn.min.js
cp package/dist/cdn.min.js www/vendor/alpine.min.js
```

## Design decisions

**Nothing is loaded from a network.** Tailwind is prebuilt, Alpine is vendored,
icons are inline SVG, type is the system font stack, and the favicon is a data
URI. The device is offline, and CDN requests hang until they time out.

```sh
curl -s http://127.0.0.1:8000 | grep -iE "cdn|https?://"    # expect no output
```

**Icons are inline SVG.** The phone icon on the call lights is Lucide's
`phone` (ISC licence, which permits commercial use; keep the notice if the
icon set is ever vendored as files).

**The Hamilton mark is always Hamilton navy** (`#2B2A5E`) on a white plate. It
is the one thing on screen a theme can't repaint.

**Taps are bound to `pointerup`, not `click`**, which doesn't fire reliably
under WPE. Alpine's `.throttle` modifier is the duplicate-event guard: 250 ms
on latching controls, 150 ms on steppers, and 120 ms on keypads.

**Press feedback is a delegated pointer listener**, not `:active`, which sticks
on some touch panels.

**Touch targets are 64 px** (`.btn`), or 44 px (`.btn-sm`) for secondary
actions in dense lists. There's no text selection, no double-tap zoom, and the
shell never scrolls.

**No CSS transitions.** The only animations are the call-light blink, which
uses `steps()` so every frame is fully lit or fully unlit, and the ringing
pulse, which animates only `transform` and `opacity` (compositor-only, no
repaint). Measure it on the device (see *Before shipping*).

**No physical keyboard** exists on a deployed console, so every entry goes
through the on-screen keyboard modal (`promptKeyboard`).

**Errors in the UI are captured** (`window.onerror`, unhandled rejections,
`console.error`/`warn`) and batched to `POST /api/app-log`. The Developer
screen's App log shows them live.

**Every UI timer is in one place**: `TIMERS` in `app.js`.

## Theming

Every chrome colour is a CSS custom property (`input.css` `@theme`), so a
palette is a runtime value that `app.js` applies with `setProperty`. **v1 ships
Hamilton branding only.** The customer-brand picker (a hue, then a light, base
or dark shade, with legibility derived) is built but sits behind
`features.customer_branding`, which is off.

Lane colours are **not** theme. They're identity, matching the physical lane
LEDs, and come from `LANE_COLORS` in `app.js`. The 16 values there are
**placeholders** until Hamilton's LED colour list is supplied.

## The C backend in `src/` — hardware reference, API v1

`src/` is a C11 backend (libmicrohttpd + libgpiod **v2**) written against the
**previous** contract, API v1. It doesn't implement v2, and the v2 UI shows an
API-mismatch warning against it. Keep it as a reference for the parts that
don't change: the GPIO pin map, mute-first amplifier ordering, atomic config
writes, and the static file server with traversal protection.

```sh
sudo apt install libmicrohttpd-dev libgpiod-dev cmake
cmake -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build
```

libgpiod **v2** is required: Raspberry Pi OS Trixie ships 2.2.x as
`libgpiod-dev`. To cross-compile, use `toolchain-pi.cmake` with
`-DPI_SYSROOT=...`. The build runs with `-Werror`.

### Pin map (check against the wiring)

| Function | BCM | Header pin | Boot state | Source |
| --- | --- | --- | --- | --- |
| Amplifier mute | GPIO25 | 22 | **HIGH = muted** | guide (`gpio=25=op,dh`) |
| Mic enable | GPIO24 | 18 | LOW = off | guide (`gpio=24=op,dl`) |
| XVF3800 reset | GPIO19 | 35 | LOW = de-asserted | guide (`gpio=19=op,dl`) |
| Lane mux A / B | GPIO12 / GPIO16 | 32 / 36 | LOW | **inferred** |
| Status LED | GPIO26 | 37 | LOW = off | **inferred** |
| Backlight PWM | GPIO18 | 12 | — | guide (`dtoverlay=pwm,pin=18,func=2`) |
| I2C1 SDA / SCL | GPIO2 / GPIO3 | 3 / 5 | — | guide |
| SPI0 | GPIO10/9/11, CE 8/7 | 19/21/23, 24/26 | driver-owned | guide |

The three **inferred** pins come from reading `gpio=12,16,22,26,27=op,dl`
positionally. Confirm them before first power-on. They're one-line `#define`s
in `src/hardware/hw.h`.

In v2, lane audio travels over the **A2B chain** (up to 16 devices). The v1 C
code routes lanes through a 2-line GPIO mux, which addresses 4 lanes. Whether
that mux survives in the A2B design is a hardware question.

The XVF3800 level and gain are stubbed (they return -1), because the register
map isn't documented.

## Not viable as specified

**An embedded LLM can't run on this board** (512 MB of RAM, offline). A static
troubleshooting decision tree, shipped as Help pages, does the job.

**Terminal.** Cog runs with no window manager, so the UI can't open a terminal
window. The Developer screen's Terminal choice explains this. The backend has to
provide it, either by switching to a VT with a getty or through SSH.

## Before shipping

Measure on a **Lite** card: cold start time, `free -h` headroom at idle, the CPU
cost of blinking and pulsing call lights (several lanes ringing at once), and that every control
responds to a single finger tap, not just a mouse click.
