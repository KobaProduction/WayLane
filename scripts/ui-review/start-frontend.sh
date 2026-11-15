#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT/services/frontend"
export WAYLANE_UI_REVIEW_API='http://127.0.0.1:8321'
export VITE_BASE_API='/'
export BASE_URL='/'
exec bun run dev -- --host 0.0.0.0 --port 5174 --strictPort
