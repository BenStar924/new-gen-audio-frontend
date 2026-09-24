#!/bin/sh
# Builds the handoff release: dist/hubs-audio-ui-<version>.tar.gz
#
# One archive holds everything the device and the backend engineer need:
#
#   www/                the UI, ready to serve as-is (CSS already built)
#   kiosk.sh, deploy/   boot straight into the console (systemd + install.sh)
#   HANDOFF.md          what the backend has to do, in order
#   API.md              the API contract (v2), field by field
#   contract-test.py    how to tell when the backend is done
#   dev-server.py       reference backend in Python (the whole v2 contract)
#   src/, CMakeLists.txt, toolchain-pi.cmake
#                       C hardware reference (API v1); builds from the unpacked folder
#   README.md, VERSION, SHA256SUMS
#
# Left out: mockups/, .vscode/, and the Tailwind source and build script. Those
# are for changing the front end, not for deploying it or building a backend.
#
#     ./make-release.sh

set -eu
cd "$(dirname "$0")"

VERSION=$(sed -n 's/^ *uiVersion: "\([^"]*\)".*/\1/p' www/app.js)
API=$(sed -n 's/^var API_VERSION = \([0-9][0-9]*\);.*/\1/p' www/app.js)
if [ -z "$VERSION" ] || [ -z "$API" ]; then
    echo "make-release: cannot read uiVersion/API_VERSION from www/app.js" >&2
    exit 1
fi

NAME="hubs-audio-ui-$VERSION"
STAGE="dist/$NAME"
rm -rf "$STAGE" "dist/$NAME.tar.gz"
mkdir -p "$STAGE"

cp -R www deploy src "$STAGE/"
cp kiosk.sh contract-test.py dev-server.py HANDOFF.md API.md README.md \
   CMakeLists.txt toolchain-pi.cmake "$STAGE/"
printf 'ui %s\napi %s\n' "$VERSION" "$API" > "$STAGE/VERSION"

# Checksums, so a copy that arrives on the device over USB or scp can be
# checked with `sha256sum -c SHA256SUMS` before installing it.
# Written outside the tree first, so the list can never include itself.
SUMS=$(mktemp)
(cd "$STAGE" && find . -type f | LC_ALL=C sort | xargs sha256sum) > "$SUMS"
mv "$SUMS" "$STAGE/SHA256SUMS"
chmod 0644 "$STAGE/SHA256SUMS"

tar -C dist -czf "dist/$NAME.tar.gz" "$NAME"

echo "dist/$NAME.tar.gz"
echo "  UI v$VERSION, API contract v$API, $(find "$STAGE" -type f | wc -l | tr -d ' ') files, $(du -h "dist/$NAME.tar.gz" | cut -f1)"
