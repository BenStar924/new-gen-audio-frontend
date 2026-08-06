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

# Deployment units run cog directly. On the bench, CONSOLE_CAGE=1 runs it under
# cage, which supplies a visible mouse cursor.
if [ "${CONSOLE_CAGE:-0}" = "1" ]; then
    LAUNCH="cage -- cog $URL"
else
    LAUNCH="cog $URL"
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
