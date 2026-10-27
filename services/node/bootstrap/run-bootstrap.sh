#!/bin/sh
set -eu
test "$(id -u)" = 0 || exit 1
for dir in /bootstrap/docker-data /bootstrap/docker-run /bootstrap/agent-state; do
  test ! -L "$dir" || exit 1
  mkdir -p "$dir"
  chown 1000:1000 "$dir"
  chmod 0700 "$dir"
  test "$(stat -c %u "$dir")" = 1000 || exit 1
  test "$(stat -c %g "$dir")" = 1000 || exit 1
done
echo "WayLane private volumes ready; no host systemd."
