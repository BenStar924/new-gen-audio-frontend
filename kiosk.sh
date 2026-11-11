#!/bin/sh
# Operator Console kiosk launcher.
#
# Waits for the backend to accept connections, then runs Cog fullscreen against
# it and restarts it if it exits. Launching Cog at a URL with nothing listening
# makes it hang on the connection timeout showing a blank screen, which reads as
# "the browser is slow" — so the wait loop below is what stops the most common
# bring-up misdiagnosis.
#
# Autostart this on console login, or wrap it in a systemd unit with
# Restart=always.

URL="${CONSOLE_URL:-http://127.0.0.1:8000}"
PORT="${CONSOLE_PORT:-8000}"

# Display scaling for this device (see "fit to the panel" in www/app.js).
# Leave both empty for 7-inch panels: the UI fits itself. CONSOLE_PANEL_INCHES
# is the panel diagonal, so a bigger panel keeps controls at their designed
# physical size; CONSOLE_SCALE is an exact factor and wins if both are set.
QUERY=""
if [ -n "${CONSOLE_SCALE:-}" ]; then
    QUERY="scale=$CONSOLE_SCALE"
elif [ -n "${CONSOLE_PANEL_INCHES:-}" ]; then
    QUERY="panel=$CONSOLE_PANEL_INCHES"
fi
if [ -n "$QUERY" ]; then
    case "$URL" in
        *\?*) URL="$URL&$QUERY" ;;
        *)    URL="${URL%/}/?$QUERY" ;;
    esac
fi

# Extra arguments for cog: platform/backend selection, scale, and so on.
# Confirm these against the environment guide's Display Configuration section
# during hardware bring-up. Set in deploy/operatorconsole-kiosk.service
# (Environment=COG_ARGS=...) rather than by editing this script.
COG_ARGS="${COG_ARGS:-}"

# Deployment units run cog directly. On the bench, CONSOLE_CAGE=1 runs it under
# cage, which supplies a visible mouse cursor.
if [ "${CONSOLE_CAGE:-0}" = "1" ]; then
    LAUNCH="cage -- cog $COG_ARGS $URL"
else
    LAUNCH="cog $COG_ARGS $URL"
fi

echo "kiosk: waiting for backend on port $PORT"
while ! ss -ltn "sport = :$PORT" | grep -q LISTEN; do
    sleep 1
done
echo "kiosk: backend up, starting browser"

while true; do
    # shellcheck disable=SC2086  # LAUNCH is a command line, word splitting is wanted
    $LAUNCH
    echo "kiosk: browser exited ($?), restarting in 2s"
    sleep 2
done
