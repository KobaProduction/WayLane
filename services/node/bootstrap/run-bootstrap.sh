#!/usr/bin/env bash
set -euo pipefail
[[ "$EUID" == 0 ]] || { echo "Root required for the ONE-SHOT bootstrap" >&2; exit 1; }
: "$WAYLANE_NODE_USER" "$WAYLANE_NODE_UID" "$WAYLANE_NODE_GID"
[[ -r /proc/1/ns/mnt ]] || exit 1
# PID 1 is the host's due to pid: host. The script is passed over stdin so
# file lookups, binaries and apt/systemctl operate on the host, not Alpine.
exec nsenter -t 1 -m -u -i -n -p -r -w -- /bin/bash -s < /opt/waylane/bootstrap-host.sh
