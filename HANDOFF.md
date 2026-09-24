# Operator Console — backend handoff

**Front end: UI v0.7.0 · API contract v1.**

The front end is done. This file describes everything the backend has to
provide for it, in the order to build it, and how to tell when it's finished.
For deeper background (the lane model, the pin map, the design decisions) see
[README.md](README.md). You shouldn't need it to get started.

## The boundary

```
Cog (fullscreen, tty1)  ──HTTP──▶  127.0.0.1:8000  ──▶  your backend
                                    ├── /            static files from www/
                                    └── /api/...     JSON, the contract below
```

One process serves **both** the static UI (`www/`) and the `/api` endpoints on
the same origin. The UI uses relative URLs such as `/api/status`, so that isn't
optional. Bind to loopback only (127.0.0.1): the UI is local to the device.

`www/` needs no build step on the device. The Tailwind CSS in
`www/vendor/app.css` is prebuilt and committed, so the four files in `www/` go
on the device exactly as they are.

## "Done" means the contract test passes

```sh
python3 contract-test.py                                   # read-only: safe on any unit
python3 contract-test.py --bench --config-password <pw>    # bench unit ONLY
```

`--bench` selects lanes, so on real hardware it **opens the microphone and
unmutes the amplifier**. Never run it on a console that is in service. It puts
back every setting it changes.

Both reference backends pass it today:

| Backend | Result |
| --- | --- |
| `dev-server.py` (Python mock) | 134 passed |
| `src/` (C, libmicrohttpd + libgpiod v2) | 126 passed, 2 skipped: no add-on modules fitted, no Master Controller |

When yours passes, the UI works against it. Treat the test as the interface
rather than either codebase: the test was what surfaced every place the two
reference backends disagreed.

## Build order

Each step makes more of the UI work. The UI keeps its last value for any field
the backend leaves out, so you can check each step on the screen as you go.

1. **Serve `www/` as static files** with `Cache-Control: no-store`. The page
   loads and shows `NO BACKEND`.
2. **`GET /api/status`** with `"api": 1` and the `lanes` array. The screen
   renders.
3. **`POST /api/lane`**: select and release. The mic and speakers follow the
   selection.
4. **`/api/hold`, `/api/mic`, `/api/mute`, `/api/volume`**.
5. **Configuration screen**: `/api/auth`, `/api/lanes`, `/api/lane-config`,
   `/api/title`, `/api/brand`.
6. **`/api/logs`, `/api/audio-out`** (with `modules`), `/api/brightness`,
   `/api/layout`.
7. **Master Controller link**: `online`, plus other consoles' lanes as
   `busy` / `hold` / `error` with `owner`.

The field-by-field tables are in [README.md → API contract](README.md#api-contract).

## Rules the field tables can't express

1. **Mute first, always.** Mute the amplifier before moving the mux, and unmute
   only after the routing has settled. Doing it in any other order puts a click
   through the speakers.
2. **A lane tap is a request.** Refuse it with `409` and a readable reason in
   `error`. The UI shows that reason to the operator.
3. **`busy`** means another console is talking: refuse. **`hold`** means another
   console parked the lane: allow. **`error`** means out of service: refuse.
4. **Open gap:** when *this* console holds a lane it reports `self` plus
   `"held": true` to itself, but **other** consoles have to see that lane as
   `hold`. That translation belongs to the Master Controller, and nothing does
   it yet. Until something does, a lane parked on one console shows as `busy`
   on the others and can't be picked up.
5. **No lane held means `mic: false`, `mute: true`, `level: 0`**, which is the
   boot state. Never keep the selected lane across a restart: a console that
   comes back from a power cut with its microphone open is a serious problem.
6. **Status codes:**
   - `400` for a malformed request: a field that's missing, the wrong type, or
     out of range. Reject it; never clamp or guess.
   - `409` for a request that's well-formed but not possible right now.
   - `403` only for a wrong password.
   - `404` for an unknown endpoint.
7. **Passwords never leave the backend.** `/api/auth` answers yes or no and
   nothing else.
8. **`theme` is served exactly as stored**, so only ever store a validated,
   flat JSON object in it.
9. **`/api/status` is polled every second.** Keep it cheap, and don't block on
   hardware inside the handler.
10. **Put `"api": 1` in `/api/status`.** The UI compares it with its own
    version and shows a warning on screen if they differ.

## Reference implementations

Start from either of these, or from neither:

- **`dev-server.py`**: the whole contract in about 480 lines of Python, with no
  hardware. The easiest place to read what each endpoint does.
- **`src/`**: C11 on libmicrohttpd and libgpiod **v2** (v1's API is
  incompatible). It covers the pin map, mute-first ordering, and the config and
  theme persistence. See README → *Building the application*.

## Deploying on the device

```sh
cmake --install build                          # the reference backend (or install your own)
sudo ./deploy/install.sh --with-backend-unit   # UI, kiosk, and both systemd units
sudo reboot
```

- **`operatorconsole-kiosk.service`** runs Cog fullscreen on tty1, with no login
  prompt. It waits until something is listening on port 8000, and it does
  **not** refer to the backend's unit by name, so you can call yours anything.
- **`operatorconsole.service`** is a template for the reference backend. If you
  build your own backend, replace its `ExecStart` and keep the rest.
- **After updating `www/`,** run `install.sh` again. It clears Cog's caches,
  which would otherwise keep showing the old UI.
- To stage files without a device: `./deploy/install.sh --destdir /tmp/stage`.

## Open items you'll run into

| Item | State | Where |
| --- | --- | --- |
| XVF3800 level meter and gain | Stubbed: they return -1. The meter reads 0; volume is stored but not applied | `src/hardware/i2c.c`, `TODO(xvf3800)` |
| 12 lanes vs. the mux | 2 select lines (GPIO12/16) address 4 lanes, and `hw_select_lane` refuses anything above 4 | `HW_MUX_LANES` in `hw.h`. Needs 4 select lines in hardware |
| Master Controller link | Not implemented: `online` is false and there's no `busy`/`hold`/`error` from other consoles | Backend |
| `held` → `hold` for other consoles | See rule 4 | Master Controller |
| Cog display flags | Not yet confirmed on hardware | `COG_ARGS` in the kiosk unit |
| Backlight PWM | Writing to sysfs may need root or a udev rule when the unit runs unprivileged | `operatorconsole.service` |
| Add-on module detection | Read from the config file (`module1=headset:Headset`) until a hardware probe exists | `add_module` in `main.c` |
| `/api/brand` | Not behind the configuration password; it should be | Backend |
| Default passwords | `1234` / `9999` are placeholders | Set `pw_config` / `pw_developer` in `/var/lib/operatorconsole/config` |
| Logo upload | Undecided: a USB stick, or pushed at install | — |
| UART | The guide gives it to the hardware layer; not implemented | `src/hardware/` |

## Changing the contract

Change `api` in the backend and `apiVersion` in `www/app.js` **together**, and
update `contract-test.py` and the README tables in the same change. If the two
sides ever disagree, the UI shows "Backend API vN, this UI needs vM" in its
status line and "MISMATCH" on the developer screen.
