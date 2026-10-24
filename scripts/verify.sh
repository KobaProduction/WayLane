#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v bun >/dev/null || { echo "Bun is required" >&2; exit 2; }
bun install --frozen-lockfile

# The default gate runs the project's pinned TypeScript 5 compiler.
# The low-memory native gate is intended for 2 GiB development containers;
# GitHub Actions still runs the standard compiler before accepting the result.
if [[ "${WAYLANE_LOW_MEMORY_TYPECHECK:-0}" == "1" ]]; then
  cp tsconfig.app.json tsconfig.app.native.tmp.json
  cp tsconfig.node.json tsconfig.node.native.tmp.json
  trap 'rm -f tsconfig.app.native.tmp.json tsconfig.node.native.tmp.json' EXIT
  sed -i '/"baseUrl":/d' tsconfig.app.native.tmp.json tsconfig.node.native.tmp.json
  sed -i '/"skipLibCheck": true,/a\    "types": ["node", "bun"],' tsconfig.app.native.tmp.json
  command -v bun >/dev/null
  export GOMAXPROCS=1
  for cfg in tsconfig.app.native.tmp.json tsconfig.node.native.tmp.json; do
    bun x --package @typescript/native-preview@7.0.0-dev.20260707.2 \
      tsgo --singleThreaded --checkers 1 -p "$cfg" --noEmit
  done
else
  bun run typecheck
fi

VITE_BASE_API=/ bun run build
echo "WayLane frontend verification passed (types/build)."
