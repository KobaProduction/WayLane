# WayLane Node Agent v2 — opt-in foundation

This does NOT replace the existing standalone WayLane Go node or its
certificates, API keys, backend assignment and user traffic.

The Compose entrypoint contains a ONE-SHOT privileged bootstrap joining
host PID/mount namespaces through nsenter. It provisions a dedicated
Linux account, user-level rootless Docker systemd service, lingering and
a persistent agent state directory. It exits when done. The permanent agent
container runs under that UID/GID without capabilities or privileges and sees
only the dedicated rootless Unix socket under /run/user/UID/docker.sock;
never mount rootful /var/run/docker.sock or the Coolify Docker engine.

Supported host: Debian/Ubuntu systemd with Docker official APT rootless
packages. Prerequisites: a reserved unused UID/GID, configured official
Docker APT source, trusted HTTPS panel URL and secret discovery token. Do not
use this Compose on running VPN servers without a separately approved rollout.

A persistent discovery token permits joining as pending only; an admin
must approve before core actions. The agent has a stable private identity
independent of changing IP and reports capabilities/address observations.
No DNS/subscription rewrite follows from an unverified address report.

The first phase proves bootstrapping, registration and rootless daemon access;
it deliberately does not start child VPN containers. In particular, Linux
host networking, kernel WireGuard/AmneziaWG and NET_ADMIN inside rootless
Docker require separate isolated host acceptance. Upgrades require pinned
image digests, health acceptance and rollback; a version field does not
make an updater. Issue #11 tracks remaining gates.
