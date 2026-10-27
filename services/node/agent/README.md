# WayLane Node Agent v2 — portable Compose foundation

This opt-in three-service Compose has no systemd, host-user provisioning,
host namespaces or access to the rootful Docker/Coolify socket. It can be
started by Docker Desktop or Linux Docker Engine with its own process lifecycle
and persistent named volumes. It does not change existing VPN nodes or TLS.

### Lifecycle

- **bootstrap**: a one-shot UID0 *inside its own container*. No privileged
  mode or host mounts are needed to initialise private named volumes. It
  validates volume ownership and permissions, then exits successfully. It
  is safe to re-run but refuses unrecognised owners and symlinks.
- **rootless-engine**: an official pinned-version Docker RootlessKit engine,
  UID1000, restarted and healthchecked by Compose, not systemd. Its only API
  socket is a private Unix socket volume. It has NO host Docker socket or
  unauthenticated TCP API. Nested user namespaces require SETUID/SETGID,
  /dev/net/tun and seccomp=unconfined in the current tested Docker Desktop
  kernel. **These are reduced permissions, not full confinement**. The
  system must pass an independent security review before use on multitenant
  or internet-exposed hosts. vfs is slow but portable; consider overlay2
  only after runtime acceptance. The engine data has a dedicated persistent
  volume so upgrades and restarts do not discard child containers.
- **agent**: UID1000, no capabilities, no-new-privileges, read-only image and
  only a read-only mount of the daemon's private Unix socket. It enrolls
  over CA-validated HTTPS. Permanent invitation creates pending identity only.
  Admin approval and a dedicated persisted agent credential are necessary for
  additional permissions.

There is no systemd, cron, sudo or user service. Compose handles start order,
healthchecks and automatic restart. The platform's Docker Compose runtime
must support /dev/net/tun and rootless user namespaces. Refuse unknown hosts,
do not silently switch to rootful Docker, and NEVER add privileged: true to
the permanent engine or agent.

This phase has source-level discovery and Docker readiness only: **agent code
cannot yet deploy/update child VPN cores**. The existing node controller and
TLS remain untouched. Kernel WireGuard/AmneziaWG devices, ports, DNS, public
IP reachability, real HTTPS discovery, image updates and rollback are NOT
accepted. See Issue #11 and the isolated Docker Desktop pilot.
