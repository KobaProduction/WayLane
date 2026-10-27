# WayLane Agent v2 — opt-in rootless Docker Compose

No host systemd. No privileged long-lived services. No host Docker socket.

- One-shot root bootstrap initializes three private named volumes and exits.
  It has CHOWN/FOWNER/DAC_OVERRIDE capabilities, no privileged flag, no
  host namespace access and no network.
- Official docker:29.5-dind-rootless runs as UID 1000 inside a separate
  Compose service and owns child containers. Its API is a private Unix
  socket on a volume shared only with the agent.
- Agent runs as UID 1000 with all capabilities dropped, read-only rootfs
  and persistent identity.

Nested user namespaces on tested Docker Desktop require SETUID/SETGID,
device /dev/net/tun, and disabled outer seccomp/AppArmor profiles. This
weakens isolation substantially despite absence of privileged mode.
Restrict nested Docker to trusted images, networks and volumes.

Compose owns lifecycle, restart and readiness; neither systemd nor a
custom process supervisor is required. No Docker API TCP port is exposed.
Volumes retain private agent credentials and nested image storage.

Currently the agent performs discovery/auth/IP reporting only, and does
not yet manage VPN core containers. Existing node services and TLS are
unaffected. Kernel VPN networking and core updates remain unverified.
See Issue #11.
