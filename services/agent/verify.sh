#!/bin/sh
set -eu
cd "$(dirname "$0")"
go mod verify
go test -count=1 ./...
go vet ./...
go build -trimpath -o /dev/null .
sh -n bootstrap.sh
