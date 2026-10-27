#!/usr/bin/env bash
set -euo pipefail
: "$WAYLANE_NODE_USER" "$WAYLANE_NODE_UID" "$WAYLANE_NODE_GID"
[[ "$(id -u)" == 0 ]] || { echo "Host root required" >&2; exit 1; }
[[ "$(cat /proc/1/comm)" == systemd ]] || { echo "Only systemd hosts supported" >&2; exit 1; }
. /etc/os-release
[[ "$ID" == debian || "$ID" == ubuntu ]] || { echo "Only Debian/Ubuntu hosts supported" >&2; exit 1; }
[[ "$WAYLANE_NODE_USER" =~ ^[a-z_][a-z0-9_-]{0,30}$ ]] || { echo "Invalid user" >&2; exit 1; }
[[ "$WAYLANE_NODE_UID" =~ ^[0-9]+$ && "$WAYLANE_NODE_GID" =~ ^[0-9]+$ ]] || exit 1
(( WAYLANE_NODE_UID >= 1000 && WAYLANE_NODE_UID <= 60000 && WAYLANE_NODE_GID >= 1000 && WAYLANE_NODE_GID <= 60000 )) || exit 1

# Do not replace/disable rootful Docker: Coolify may depend on it.
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends uidmap dbus-user-session slirp4netns fuse-overlayfs util-linux
if ! command -v dockerd-rootless-setuptool.sh >/dev/null; then
  if ! apt-cache show docker-ce-rootless-extras >/dev/null 2>&1; then
    echo "Official Docker apt repository / docker-ce-rootless-extras is required on the host" >&2
    exit 1
  fi
  apt-get install -y --no-install-recommends docker-ce-rootless-extras docker-ce-cli
fi
for bin in docker dockerd-rootless-setuptool.sh newuidmap newgidmap loginctl runuser; do
  command -v "$bin" >/dev/null || { echo "Missing host command: $bin" >&2; exit 1; }
done

if getent passwd "$WAYLANE_NODE_USER" >/dev/null; then
  [[ "$(id -u "$WAYLANE_NODE_USER")" == "$WAYLANE_NODE_UID" ]] || { echo "Existing UID mismatch" >&2; exit 1; }
  [[ "$(id -g "$WAYLANE_NODE_USER")" == "$WAYLANE_NODE_GID" ]] || { echo "Existing GID mismatch" >&2; exit 1; }
else
  if getent passwd "$WAYLANE_NODE_UID" >/dev/null || getent group "$WAYLANE_NODE_GID" >/dev/null; then
    echo "Reserved UID/GID already in use" >&2; exit 1
  fi
  groupadd --gid "$WAYLANE_NODE_GID" "$WAYLANE_NODE_USER"
  useradd --uid "$WAYLANE_NODE_UID" --gid "$WAYLANE_NODE_GID" --create-home --shell /bin/bash "$WAYLANE_NODE_USER"
fi
# Existing accounts with extra host privileges are not valid WayLane runtimes.
if id -nG "$WAYLANE_NODE_USER" | tr ' ' '\n' | grep -Eq '^(docker|sudo|wheel)$'; then
  echo "Dedicated agent account must not belong to privileged host groups" >&2
  exit 1
fi
for file in /etc/subuid /etc/subgid; do
  awk -F: -v user="$WAYLANE_NODE_USER" '$1==user && $3>=65536 {found=1} END {exit !found}' "$file" ||
    { echo "No >=65536 subordinate ID range for $WAYLANE_NODE_USER in $file" >&2; exit 1; }
done

state=/var/lib/waylane-agent
[[ ! -L "$state" ]] || { echo "Symlinked agent state forbidden" >&2; exit 1; }
install -d -m 0700 -o "$WAYLANE_NODE_UID" -g "$WAYLANE_NODE_GID" "$state"

loginctl enable-linger "$WAYLANE_NODE_USER"
systemctl start "user@$WAYLANE_NODE_UID.service"
xdg="/run/user/$WAYLANE_NODE_UID"
[[ -S "$xdg/bus" ]] || { echo "No systemd user bus in $xdg" >&2; exit 1; }
as_user() {
  runuser -u "$WAYLANE_NODE_USER" -- env \
    HOME="$(getent passwd "$WAYLANE_NODE_USER" | cut -d: -f6)" \
    XDG_RUNTIME_DIR="$xdg" DBUS_SESSION_BUS_ADDRESS="unix:path=$xdg/bus" "$@"
}
if ! as_user systemctl --user is-enabled docker.service >/dev/null 2>&1; then
  as_user dockerd-rootless-setuptool.sh install --force
fi
as_user systemctl --user enable --now docker.service
for attempt in 1 2 3 4 5; do
  if as_user docker --host "unix://$xdg/docker.sock" info --format '{{json .SecurityOptions}}' 2>/dev/null | grep -q rootless; then
    echo "WayLane rootless Docker healthy (UID $WAYLANE_NODE_UID)"
    exit 0
  fi
  sleep 2
done
echo "Rootless Docker failed healthcheck; agent must not start" >&2
exit 1
