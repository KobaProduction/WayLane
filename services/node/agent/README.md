# WayLane Node Agent v2: two services

This optional deployment leaves legacy nodes, core bindings, TLS, user traffic
and the rootful Coolify/Docker Desktop socket unchanged.

1. bootstrap: the ONLY privileged container. It verifies/initializes three
   private persistent named volumes owned by UID/GID 1000, then exits.
   No systemd, host namespaces, host account provisioning or second daemon.
2. agent: a single autonomous Go application running as UID/GID 1000.
   It starts and supervises official RootlessKit/dockerd inside its OWN
   container. Its private Docker Unix socket never leaves that container.
   Rootless Docker data and agent identity persist across recreation.

Agent is not privileged and has no host Docker socket. To create user and
network namespaces in Docker Desktop, RootlessKit needs SETUID/SETGID,
seccomp/AppArmor unconfined and /dev/net/tun. These runtime exceptions remain
security-sensitive and must be tested on every supported host.

Currently implemented: discovery registration, manual approval, private
identity and heartbeat, Docker daemon verification and lifetime supervision.
NOT implemented or accepted: installing/updating VPN cores, host-side
WireGuard/AmneziaWG NET_ADMIN and routing, rollback, subscription IP cutover.
Do not deploy production VPN traffic before those acceptance checks.
