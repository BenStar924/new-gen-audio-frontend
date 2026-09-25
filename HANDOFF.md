# HUBS Audio console — backend handoff

**Front end: UI v1.0.0 · API contract v2.**

The front end is done. This file lists what the backend has to provide for it,
in the order to build it, and how to tell when you're finished. The contract
itself, field by field, is [API.md](API.md).

## The boundary

```
Cog (fullscreen, tty1)  ──HTTP──▶  127.0.0.1:8000  ──▶  your backend
                                    ├── /            static files from www/
                                    └── /api/...     JSON, see API.md
```

One process serves **both** the static UI (`www/`) and `/api` on the same
origin. The UI uses relative URLs, so that isn't optional. Bind to loopback
only.

`www/` needs no build step on the device. The CSS is prebuilt, so the files in
`www/` go on the device exactly as they are.

## "Done" means the contract test passes

```sh
python3 contract-test.py                                  # read-only: safe on any unit
python3 contract-test.py --bench --dev-password <pw>      # bench system ONLY
python3 contract-test.py --bench --user "<admin>" --pin <pin>   # when login is required
```

`--bench` **places a real call** on an idle lane and changes settings (and puts
them back). Never run it on a site in service. It never creates users, restores
a backup, restarts lane audio, or reboots.

`dev-server.py` passes all of it in every mode: Master and Operator, locked,
teller and technician sessions, and 15 lanes. When yours passes, the UI works
against it.

## Build order

Each step makes more of the UI work, and you can watch it happen on the screen.

1. **Serve `www/`** with `Cache-Control: no-store`. The page loads, and the
   status light is red ("No response from the console backend").
2. **`GET /api/status`** with `"api": 2`, `console`, and `devices`, and
   `session.login_required: false` plus the built-in user. The lane grid
   renders.
3. **Calls**: `/api/call`, `/hangup`, `/hold`, `/mic`, `/volume`, `/output`.
   One-touch connect, the Active Call screen, and the LED states.
4. **Troubleshooting**: `codes` and `health` on devices, the heartbeats in
   `console`, `/api/logs/export`, `/api/help` with page images,
   `/api/reboot`, and `/api/lane/restart-audio`.
5. **Developer**: `/api/auth`, and `POST`/`GET /api/app-log`.
6. **Settings (Master only)**: `GET /api/settings`, then the `/api/settings/*`,
   `/api/users*`, and `/api/config/*` commands, plus `status.sync`.
7. **Login**: `login_required: true`, `/api/login/check`, `/api/login`, and
   `/api/lock`.
8. **Master Controller integration**: roles, the other consoles' lanes as
   `busy`/`held`, and ringing on every console.

## Rules API.md states that are easy to miss

1. **The backend enforces every permission.** The UI hides what a user can't
   use, but a hidden button isn't security. Enforce the capability table in
   API.md.
2. **States are from this console's point of view.** A lane in a call on
   another console is `busy` here and `active` there. Never tell the UI *which*
   console has it.
3. **Hold hangs up.** `POST /api/call/hold` ends this console's audio and leaves
   the lane `held` on every console, until anyone calls it again.
4. **No call means no audio path.** Boot with nothing routed, and never restore
   a call across a restart.
5. **A call suppresses the lock.** Refuse `/api/lock` and `/api/reboot` with
   `409` while a call is up.
6. **Don't let login lock everybody out.** Refuse to turn on `login_required`
   (`409`) until a non-built-in admin or owner account exists.
7. **Status codes:** `400` malformed (reject; never clamp), `403` not allowed,
   `404` unknown, `409` not possible right now. Every error body is
   `{"error": "..."}` and **the UI shows that text to the operator**, so write
   it for a teller.
8. **PINs never leave the backend**: store them hashed, and don't put them in
   `/api/settings`. The same goes for the developer password.
9. **`/api/status` is polled every second.** Keep it cheap, and never block on
   hardware inside it.
10. **The UI uses `now`** for timers and the clock, so send the Master
    Controller's time.

## Reference implementations

- **`dev-server.py`**: all of API v2 in one Python file, with simulated
  hardware. It's the easiest place to read what each endpoint does, including
  every guard and error message.
- **`src/`** (C, libmicrohttpd + libgpiod v2): implements the **old API v1**.
  Use it for the hardware side only: pin map, mute-first ordering, atomic
  config writes, and a traversal-safe static server. See README.

## Deploying on the device

```sh
sudo ./deploy/install.sh                       # UI + kiosk unit
sudo ./deploy/install.sh --with-backend-unit   # also the backend unit template
sudo reboot
```

- **`operatorconsole-kiosk.service`** runs Cog fullscreen on tty1 with no login
  prompt. It waits for port 8000 and doesn't name the backend's unit, so yours
  can be called anything.
- **`operatorconsole.service`** is a template. Point its `ExecStart` at your
  backend.
- **After updating `www/`,** run `install.sh` again. It clears Cog's caches.
- To stage files without a device: `./deploy/install.sh --destdir /tmp/stage`.

## Open items (need a decision; the UI has a placeholder)

| Item | UI today |
| --- | --- |
| Full permission matrix | Provisional `CAPABILITIES` table (API.md) |
| Hamilton's 16 lane LED colours | 16 placeholder colours in `LANE_COLORS` (`app.js`) and `LANE_COLORS` (`dev-server.py`) |
| Master Console cue (subtle, non-text) | Tiny lavender dot in the corner of the logo plate |
| Lane identity artwork (graphic of the physical unit) | Placeholder rounded unit shape with the lane number |
| Answering from Locked without a PIN | Behind `features.answer_without_pin` (off); gives a restricted call |
| Manual lock / switch user | Not provided. Auto-lock only |
| Log export and config backup transport (USB? A2B SPI?) | `409` with a reason when there's nowhere to write |
| Tier-1 audio settings | Settings → Audio shows "not yet specified" |
| Terminal on the Developer screen | Explains that the backend must provide it |
| Timer lengths (idle, auto-lock, developer) | `TIMERS` in `app.js`, inside the stated ranges |
| Nightly restart / resync | Backend job; not in the UI |
| Customer branding | Built but behind `features.customer_branding` (off) |

## Changing the contract

Change `api` in the backend and `API_VERSION` in `www/app.js` **together**, and
update API.md and `contract-test.py` in the same change. If the two sides
disagree, the UI's status line says "Backend API vN, this UI needs vM".
