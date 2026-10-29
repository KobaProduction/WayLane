#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v go >/dev/null || { echo "Go 1.27+ is required" >&2; exit 2; }
./scripts/prepare-test-runtime.sh
export XRAY_EXECUTABLE_PATH="$PWD/.test-runtime/xray/xray"
export XRAY_ASSETS_PATH="$PWD/.test-runtime/xray"
export XRAY_LOCATION_ASSET="$XRAY_ASSETS_PATH"
# Both controller TestMain suites bind localhost:8002; serialize packages.
go test -p 1 -parallel 1 ./...
go vet ./...
go build -o /dev/null ./cmd/node
# The new autonomous controller and legacy node are distinct commands
# inside this one Go module. The general test gate above covers both.
go build -o /dev/null ./cmd/agent
sh -n bootstrap.sh
echo "WayLane node verification passed (legacy node, agent, bootstrap checks)."
