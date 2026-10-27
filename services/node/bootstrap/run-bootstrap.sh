#!/bin/sh
set -eu
# Portable one-shot volume preparation; no host namespaces or systemd.
[ "$(id -u)" -eq 0 ] || { echo "container root required" >&2; exit 1; }
for dir in /state/agent /state/rootless-data /state/rootless-run; do
  [ -d "$dir" ] && [ ! -L "$dir" ] || { echo "Unsafe volume: $dir" >&2; exit 1; }
  owner="$(stat -c %u:%g "$dir")"
  if [ "$owner" = 0:0 ]; then chown 1000:1000 "$dir"
  elif [ "$owner" != 1000:1000 ]; then echo "Unexpected owner $owner on $dir" >&2; exit 1
  fi
  chmod 0700 "$dir"
done
echo "WayLane rootless volumes prepared"
