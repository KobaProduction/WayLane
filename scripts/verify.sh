#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v uv >/dev/null || { echo "uv is required" >&2; exit 2; }
uv lock --check
uv run --no-project --python 3.14 python -m compileall -q app cli dashboard/__init__.py main.py waylane-cli.py
test -f LICENSE
test -f app/templates/subscription/index.html
test -f dashboard/__init__.py
echo "WayLane backend bootstrap verification passed (syntax/lock/assets)."
