# HUBS Audio console — API contract v2

The interface between the console UI (`www/`) and the backend on the device.
`dev-server.py` implements all of it, and `contract-test.py` checks a backend
against it. When the test passes, the UI works.

Built from the *HUBS Audio UI/UX Design KPI* (post-meeting revision). Anything
that document leaves open is marked **(open)** here, and the backend should
treat it as provisional.

## Conventions

- **Same origin.** One process on `127.0.0.1:8000` serves the static UI (`www/`)
  and `/api/...`. The UI uses relative URLs. Bind to loopback only.
- **JSON in, JSON out.** Every `POST` takes a JSON object body. A successful
  command answers `{"ok": true}`, plus any fields listed for that endpoint.
- **`Cache-Control: no-store`** on everything, pages included. WPE caches
  aggressively.
- **Status codes are part of the contract:**

  | Code | Meaning |
  | --- | --- |
  | `400` | Malformed: a field is missing, has the wrong type, or is out of range. **Reject, never clamp or guess.** |
  | `403` | Not allowed: wrong password or PIN, not logged in, or a device/user role that lacks the capability |
  | `404` | Unknown endpoint |
  | `409` | Well-formed but not possible right now: no active call, lane busy, lane out of service, a call during reboot |

  Every error body is `{"error": "<human-readable reason>"}`. **The UI shows
  that text to the operator**, so write it for a teller, not a developer.
- **The backend enforces every permission.** The UI hides what a user can't
  use, but that's for convenience only; it isn't security.

## Device roles

Every console is hardware-identical. The **Master Controller** designates the
console closest to it in the A2B chain as the **Master Console**, and every
other console is an **Operator Console**. Nothing on the device can change this.

- The Main screen looks the same on both roles. The only difference is that
  Operators have **no Settings screen at all**, so its button isn't rendered.
- Master-only extras: Settings, the A2B chain diagram, and Restart Lane Audio.

## Users and sessions

Roles: `user` (teller) · `technician` · `admin` · `owner` · `disabled`.
Accounts are never deleted; to remove someone's access, move them to `disabled`.
PINs are exactly **4 digits**, stored hashed, and live only on the Master
Console.

The session is **per console** and belongs to the backend, so reloading the
page doesn't unlock it. When `login_required` is false (the MVP default), every
console is permanently logged in as the built-in **Hamilton Audio** user
(`owner`), there's no Locked screen, and auto-lock doesn't run.

A locked console stays fully on the A2B chain: lanes still ring, and it still
takes calls.

### Capability matrix — provisional **(open)**

The doc defers the full permission matrix. The UI uses this table, defined in a
single place in `app.js` (`CAPABILITIES`), and the backend must enforce the same
rules:

| Capability | Console | User roles |
| --- | --- | --- |
| `troubleshooting` | any | any logged-in user |
| `settings` | **master** | technician, admin, owner |
| `manage_users` | **master** | admin, owner |
| `chain_diagram` | **master** | any logged-in user |
| `restart_lane_audio` | **master** | technician, admin, owner |
| Developer screen | any | **password only**, whoever is logged in |

A call answered without a PIN (see `answer_without_pin`) gets **no**
capabilities. The only controls it gets are Hang Up and volume.

## `GET /api/status`

The UI polls this once a second. Keep it cheap: never block on hardware inside
the handler.

```json
{
  "api": 2,
  "now": 1790000000.25,
  "console": { "id": "console-1", "role": "master",
               "i2c": true, "a2b": true, "master_link": true },
  "devices": [
    { "id": "console-1", "kind": "console", "position": 0, "role": "master",
      "health": "ok", "codes": [] },
    { "id": "lane-1", "kind": "lane", "position": 1, "number": 1, "color": "red",
      "health": "ok", "codes": [],
      "call": { "state": "idle", "since": null } }
  ],
  "call": null,
  "outputs": [ { "id": "speaker", "name": "Built-in speaker" } ],
  "session": { "user": { "name": "Hamilton Audio", "role": "owner" },
               "login_required": false, "last_user": "Hamilton Audio",
               "pin_length": 4, "autolock_minutes": 150 },
  "sync": { "pending": 0, "total": 3 },
  "service_panel": false,
  "features": { "answer_without_pin": false, "customer_branding": false },
  "branding": { "title": "", "theme": {}, "brand": { "base": "", "shade": "base" } },
  "message": ""
}
```

| Field | Meaning |
| --- | --- |
| `api` | Contract version: **2**. The UI shows a warning on screen if it differs |
| `now` | Backend clock, in Unix seconds. The Master Controller pushes its time to every console. The UI uses this for call timers and the clock, so the browser's clock doesn't matter |
| `console` | **This** console: its `id`, its `role` (`master` or `operator`), and three heartbeats. `i2c` is the link to the onboard audio chip; `a2b` is the link to the daisy chain; `master_link` is the link to the Master Console (always true on the Master itself) |
| `devices` | **The single device-status source.** Every lane and console at the site, in A2B chain order (`position`). Up to **16 devices** in total, lanes and consoles combined. The lane grid, the chain diagram, the status light, and this console's own error list are all drawn from this one array |
| `devices[].health` | `ok`, `warning`, or `error`. A lane in `error` is greyed out and can't be tapped; a lane in `warning` still works |
| `devices[].codes` | Active codes: `[{ "code": "A2B-DOWNSTREAM", "severity": "error", "text": "No signal from the next device" }]` |
| `devices[].number` | Lanes only: the display number, **1–16**. Numbers are unique but needn't be contiguous (a site can have lanes 3, 4 and 5 only) |
| `devices[].color` | Lanes only: one of the 16 lane-color ids (below) |
| `devices[].call` | Lanes only: `{state, since}`. `since` is the Unix time the state began, or `null` |
| `call` | This console's active call, or `null` (details below) |
| `outputs` | Audio outputs **this** console can use right now: the built-in speaker plus any accessory that's been auto-detected, such as a headset or handset |
| `session.user` | `{name, role}`, or `null` when the console is locked |
| `session.last_user` | Pre-fills the username on the Locked screen. It sticks until the console restarts |
| `sync` | How far a Master configuration change has propagated: `pending` is the number of consoles yet to acknowledge it, out of `total` |
| `service_panel` | True if the site has a Service Panel. Lane numbers and colors then come from the Service Panel, and Settings shows them read-only |
| `features` | Switches the backend controls, so policy can change without a UI release. Both are **(open)** |
| `branding` | Customer branding. It's applied only when `features.customer_branding` is on; v1 ships Hamilton branding only |

### Lane call states and the LED

States are reported **from this console's point of view**:

| `state` | LED | Tapping it |
| --- | --- | --- |
| `idle` | Off | Places a new call |
| `ringing` | **Fast blink**. Several lanes can ring at once, and every console shows it | Answers |
| `active` | **Solid**: in a call on *this* console | Not reachable (the Active Call screen replaces the grid) |
| `busy` | **Dim**, about 10%: in a call on *another* console | Nothing |
| `held` | **Slow blink**: the on-hold reminder, shared by the whole system | Places a new call ("rejoining") |

Status is conveyed only by on, off, blink speed, and brightness, never by color.
The UI never shows *which* console has a lane.

### `call`

```json
{ "lane": "lane-3", "since": 1790000000.0, "mic_muted": false,
  "volume": 60, "output": "speaker", "restricted": false }
```

`volume` applies to this call only; after hang-up it resets to the console's
default. `output` starts at the console's default output. `restricted` is true
for a call answered without a PIN.

### The status light (computed by the UI)

- **Red** if any device's `health` is `error`, or if `console.i2c`,
  `console.a2b`, or `console.master_link` is false.
- **Yellow** if nothing is red but some device is at `warning`.
- **Green** otherwise.

## Commands

### Calls

| Endpoint | Body | Notes |
| --- | --- | --- |
| `POST /api/call` | `{"lane": "lane-3"}` | **One-touch connect**: answers if the lane is ringing; otherwise places a call. `409` if it's busy, out of service, or you're already in a call. `403` if the console is locked, unless the lane is ringing and `answer_without_pin` is on, in which case the call comes back with `restricted: true` |
| `POST /api/call/hangup` | `{}` | `409` with no active call |
| `POST /api/call/hold` | `{}` | **Hangs up**, and leaves the lane in `held` for every console. `409` with no active call |
| `POST /api/call/mic` | `{"muted": true}` | `409` with no active call |
| `POST /api/call/volume` | `{"value": 65}` | 0–100, this call only. `409` with no active call |
| `POST /api/call/output` | `{"id": "headset"}` | Must be one of `outputs`, otherwise `400`. This call only |

### Session

| Endpoint | Body | Notes |
| --- | --- | --- |
| `POST /api/login/check` | `{"username": "Alex"}` | Answers `{"ok": true, "valid": bool}`. Checked against the Master's user list; a disabled user isn't valid. The UI flags an unknown name immediately |
| `POST /api/login` | `{"username": "Alex", "pin": "1234"}` | Answers `{"ok": true, "user": {...}}`. `403` for an unknown user or a wrong PIN. Unlimited attempts, and no lockout |
| `POST /api/lock` | `{}` | Sent by the UI when its auto-lock timer fires. It does nothing when `login_required` is false |

### This console

| Endpoint | Body | Notes |
| --- | --- | --- |
| `POST /api/reboot` | `{}` | Restarts the **UI and software only**, not the audio hardware. `409` during an active call |
| `POST /api/logs/export` | `{}` | Exports **this device's** operational log (JSONL `.txt`). Answers `{"ok": true, "file": "..."}`, or `409` if there's nowhere to write it (for example, no USB drive). **Transport (open).** |
| `GET /api/help` | | `{"docs": [{"id", "title", "pages": ["/help/x-1.png", ...]}]}`. WPE has no PDF viewer, so render the help PDFs to page images at install |

### Master only

| Endpoint | Body | Notes |
| --- | --- | --- |
| `POST /api/lane/restart-audio` | `{"lane": "lane-3"}` | Remotely restarts a lane's audio hardware over A2B. Drops any call on that lane. `403` on an Operator console |

### Developer

| Endpoint | Body | Notes |
| --- | --- | --- |
| `POST /api/auth` | `{"scope": "developer", "password": "..."}` | `403` for the wrong password. **Always** required, whoever is logged in |
| `POST /api/app-log` | `{"entries": [{t, level, message, source, line, column, stack}]}` | The UI sends its own uncaught errors, unhandled promise rejections, and `console.error`/`console.warn` output here, in batches. Append them to the application log file |
| `GET /api/app-log` | | `{"entries": [...]}`, oldest first, capped at the most recent 500. The Developer screen polls this for its live viewer |

## Settings (Master only)

Every endpoint here answers `403` on an Operator console, or for a user without
the `settings` capability (`manage_users` for the user endpoints).

`GET /api/settings`:

```json
{
  "lanes":    [ { "id": "lane-1", "number": 1, "color": "red" } ],
  "consoles": [ { "id": "console-1", "position": 0, "role": "master",
                  "outputs": [ { "id": "speaker", "name": "Built-in speaker" } ],
                  "default_output": "speaker", "default_volume": 60,
                  "noise_cancellation": true } ],
  "site":     { "login_required": false, "autolock_minutes": 150, "pin_length": 4 },
  "users":    [ { "name": "Hamilton Audio", "role": "owner", "builtin": true } ],
  "lane_colors": [ "red", "orange", "..." ],
  "tier1":    []
}
```

`users` is included only for callers with `manage_users`.

| Endpoint | Body | Notes |
| --- | --- | --- |
| `POST /api/settings/lane` | `{"lane", "number"?, "color"?}` | `number` must be 1–16 and not used by another lane, and `color` must be a lane-color id; otherwise `400`. `409` when `service_panel` is on |
| `POST /api/settings/console` | `{"console", "default_output"?, "default_volume"?, "noise_cancellation"?}` | Per-console defaults, set centrally. `default_output` must be one of that console's `outputs` |
| `POST /api/settings/site` | `{"login_required"?, "autolock_minutes"?}` | Auto-lock takes 15–480 minutes. Turning login off signs the console in as Hamilton Audio |
| `POST /api/users` | `{"name", "pin", "role"}` | Creates a user. The name must be unique (case-insensitive), the PIN exactly 4 digits, and the role one of `user`, `technician`, `admin`, `owner`. `409` if the name already exists |
| `POST /api/users/update` | `{"name", "role"?, "pin"?}` | Changes a role (`disabled` included) or resets a PIN. The built-in user can't be changed (`409`). Nothing is ever deleted |
| `POST /api/config/save` | `{}` | Backs up **everything**: lanes, per-console values, users, and Tier 1 audio. Answers `{"ok": true, "file": "..."}`. **Transport (open)**; A2B SPI is one candidate |
| `POST /api/config/restore` | `{}` | `409` if there's no backup to restore |

After any settings change, report the propagation in `status.sync` until every
console has acknowledged it.

**Tier 1 audio parameters** are **(open)**: they're owned by the audio team and
not yet specified. `tier1` stays an empty list until they are.

### Customer branding (later version)

`POST /api/brand` and `POST /api/title` keep their v1 behaviour for when
`features.customer_branding` is turned on. v1 ships Hamilton branding only.

## Lane colors **(open)**

A site has at most 16 devices, and there are 16 lane colors, so every lane can
have its own. The ids below are placeholders; replace them with **Hamilton's
pneumatic LED color list**, which hasn't been supplied yet:

`red` · `orange` · `amber` · `yellow` · `lime` · `green` · `teal` · `cyan` ·
`sky` · `blue` · `indigo` · `violet` · `purple` · `magenta` · `pink` · `white`

## Timers

| Timer | Length | Owner |
| --- | --- | --- |
| Auto-lock | Site setting, 15–480 min (about 2–3 h) **(open)** | UI. It sends `POST /api/lock`, and is **suppressed while a call is active** |
| Settings idle | 10 min **(open, 5–20)** | UI; returns to Main |
| Troubleshooting idle | 10 min **(open, 5–20)** | UI; returns to Main |
| Developer idle | 3 min **(open, 2–5)** | UI; closes the Developer screen. It must also be aware of running processes once a terminal exists |
| Screensaver | 5 min, fixed | UI. It clears on a touch or an incoming ring, and never appears during a call |
| Nightly restart | about 8–10 h **(open)** | Backend. It doubles as the nightly settings resync |

The UI-side constants live together in `app.js` (`TIMERS`).
