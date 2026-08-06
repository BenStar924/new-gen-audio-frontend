#!/bin/sh
# Build the purged, minified stylesheet into www/vendor/app.css.
#
# Run this in WSL2 whenever the markup changes — Tailwind emits only the classes
# it can see in www/, so a class added to index.html does not exist until this
# runs. The device never compiles CSS: the Play CDN is a runtime in-browser
# compiler, unreachable offline and wasteful on this board.
#
# Tailwind v4. Config is CSS-first, in input.css — there is no
# tailwind.config.js. A v3 toolchain will not read it.
set -e

cd "$(dirname "$0")"

IN="input.css"
OUT="www/vendor/app.css"

# Prefer the standalone CLI (no Node.js needed on the build machine). Download
# the aarch64 or x64 binary from the tailwindlabs/tailwindcss releases page,
# chmod +x it, and drop it beside this script as ./tailwindcss.
if [ -x "./tailwindcss" ]; then
    echo "building with standalone CLI"
    ./tailwindcss -i "$IN" -o "$OUT" --minify
else
    echo "no ./tailwindcss standalone binary, falling back to npx"
    npx --yes @tailwindcss/cli@4 -i "$IN" -o "$OUT" --minify
fi

echo "wrote $OUT ($(wc -c < "$OUT") bytes)"
