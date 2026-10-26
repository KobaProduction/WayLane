#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 1 || ! -f "$1/index.html" || ! -d "$1/statics" ]]; then
  echo "Usage: $0 /path/to/waylane-frontend/build (needs index.html and statics/)" >&2
  exit 2
fi
target="$(cd "$(dirname "$0")/.." && pwd)/dashboard/build"
mkdir -p "$target"
rsync -a --delete "$1/" "$target/"
cp "$target/index.html" "$target/404.html"
echo "Installed frontend artifact at $target"
