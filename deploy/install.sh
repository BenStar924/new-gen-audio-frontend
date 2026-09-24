#!/bin/sh
# Installs the Operator Console FRONT END on the device, and makes it start at
# boot. Run it from an unpacked release (or from the repo) on the Pi:
#
#     sudo ./deploy/install.sh                      # front end + kiosk unit
#     sudo ./deploy/install.sh --with-backend-unit  # also the reference backend's unit
#
# What it does:
#   - copies www/ to PREFIX/share/operatorconsole/www (replacing the old copy
#     wholesale, so a removed file does not linger)
#   - installs kiosk.sh to PREFIX/bin
#   - installs operatorconsole-kiosk.service, creates the `opkiosk` user, and
#     enables the unit so the device boots into the console
#   - clears Cog/WPE's caches, which otherwise serve the OLD UI after an update
#
# The backend is not installed from here. The reference C backend installs its
# binary with `cmake --install build`; --with-backend-unit adds its systemd
# unit. A different backend brings its own unit. See HANDOFF.md.
#
# Options:
#   --prefix DIR         install prefix (default /usr/local)
#   --destdir DIR        stage into DIR instead of / (packaging, or testing
#                        without a device); creates no users, touches no systemd
#   --no-systemd         install files only; do not create users or enable units
#   --with-backend-unit  also install and enable deploy/operatorconsole.service

set -eu

PREFIX=/usr/local
DESTDIR=
SYSTEMD=1
BACKEND_UNIT=0

usage() { sed -n '2,31p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --prefix)            PREFIX=$2; shift 2 ;;
        --destdir)           DESTDIR=$2; SYSTEMD=0; shift 2 ;;
        --no-systemd)        SYSTEMD=0; shift ;;
        --with-backend-unit) BACKEND_UNIT=1; shift ;;
        -h|--help)           usage; exit 0 ;;
        *) echo "install.sh: unknown option $1" >&2; usage >&2; exit 2 ;;
    esac
done

# The bundle root: this script lives in deploy/, next to www/ and kiosk.sh, both
# in the repo and in a release.
ROOT=$(CDPATH='' cd "$(dirname "$0")/.." && pwd)
[ -f "$ROOT/www/index.html" ] || { echo "install.sh: no www/index.html under $ROOT" >&2; exit 1; }

if [ -z "$DESTDIR" ] && [ "$(id -u)" -ne 0 ]; then
    echo "install.sh: run as root (sudo), or pass --destdir to stage somewhere else" >&2
    exit 1
fi

WWW="$DESTDIR$PREFIX/share/operatorconsole/www"
BIN="$DESTDIR$PREFIX/bin"
UNITS="$DESTDIR/etc/systemd/system"

echo "Installing the Operator Console front end"
echo "  from   $ROOT"
echo "  prefix $PREFIX${DESTDIR:+ (staged under $DESTDIR)}"

# The UI: replaced wholesale.
rm -rf "$WWW"
mkdir -p "$WWW"
cp -R "$ROOT/www/." "$WWW/"
echo "  www    -> $WWW ($(find "$WWW" -type f | wc -l | tr -d ' ') files)"

mkdir -p "$BIN"
cp "$ROOT/kiosk.sh" "$BIN/kiosk.sh"
chmod 0755 "$BIN/kiosk.sh"
echo "  kiosk  -> $BIN/kiosk.sh"

# Units, with the prefix rewritten if it is not the default.
install_unit() {
    mkdir -p "$UNITS"
    # Comments keep saying /usr/local; only the directives move.
    sed "/^[[:space:]]*#/!s#/usr/local#$PREFIX#g" "$ROOT/deploy/$1" > "$UNITS/$1"
    chmod 0644 "$UNITS/$1"
    echo "  unit   -> $UNITS/$1"
}
install_unit operatorconsole-kiosk.service
[ "$BACKEND_UNIT" = 1 ] && install_unit operatorconsole.service

if [ "$SYSTEMD" = 0 ]; then
    echo "Files installed. Users and systemd left alone (--destdir or --no-systemd)."
    exit 0
fi

# Only the groups this system actually has; a missing one would stop the unit.
existing_groups() {
    out=
    for g in "$@"; do
        if getent group "$g" >/dev/null 2>&1; then out="${out:+$out,}$g"; fi
    done
    echo "$out"
}

make_user() {  # name home groups...
    name=$1; home=$2; shift 2
    groups=$(existing_groups "$@")
    if id "$name" >/dev/null 2>&1; then
        [ -n "$groups" ] && usermod -a -G "$groups" "$name"
        echo "  user   $name exists${groups:+ (groups $groups)}"
    else
        if [ -n "$home" ]; then
            useradd --system --create-home --home-dir "$home" --shell /usr/sbin/nologin \
                ${groups:+--groups "$groups"} "$name"
        else
            useradd --system --no-create-home --shell /usr/sbin/nologin \
                ${groups:+--groups "$groups"} "$name"
        fi
        echo "  user   $name created${groups:+ (groups $groups)}"
    fi
}

# The kiosk needs the display (video, render) and the touchscreen (input).
make_user opkiosk /var/lib/opkiosk video render input

# After an update Cog/WPE would otherwise keep serving the previous UI.
rm -rf /var/lib/opkiosk/.cache/cog /var/lib/opkiosk/.cache/wpe* /var/lib/opkiosk/.local/share/cog
echo "  cache  Cog/WPE caches cleared"

if [ "$BACKEND_UNIT" = 1 ]; then
    make_user opconsole "" gpio i2c spi
    [ -x "$PREFIX/bin/operatorconsole" ] ||
        echo "  NOTE   $PREFIX/bin/operatorconsole not found; run 'cmake --install build' for the reference backend"
fi

systemctl daemon-reload
systemctl enable operatorconsole-kiosk.service
[ "$BACKEND_UNIT" = 1 ] && systemctl enable operatorconsole.service

echo
echo "Done. Reboot, or start now with:"
[ "$BACKEND_UNIT" = 1 ] && echo "    sudo systemctl start operatorconsole.service"
echo "    sudo systemctl start operatorconsole-kiosk.service"
echo "The kiosk waits until something is listening on port 8000, then starts Cog."
