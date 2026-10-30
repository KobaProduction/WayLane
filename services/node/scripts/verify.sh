#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v go >/dev/null || { echo "Go 1.27+ required" >&2; exit 2; }
go mod verify
go test -count=1 ./...
go vet ./...
go build -trimpath -o /dev/null .
echo "WayLane autonomous Node source gate passed"
