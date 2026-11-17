#!/bin/sh
# Bundle harness.ts with the app's flash code (web/src/lib) for node → work/harness.cjs (or $1).
# Needs esbuild: npm i --prefix work esbuild
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=${FLASHSIM_WORK:-$HERE/work}
OUT=${1:-$WORK/harness.cjs}
"$WORK/node_modules/.bin/esbuild" "$HERE/harness.ts" --bundle --platform=node --format=cjs \
  --alias:@lib="$HERE/../../web/src/lib" --outfile="$OUT" --log-level=warning
