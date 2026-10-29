# VPN node — separate deployment, NEVER All-in-One

**Two different deployment generations coexist.** The generic
`docker-compose.yaml` and `docker-compose.coolify.yaml` below are for the
**legacy single-process node**. Do not transfer their root/NET_ADMIN,
host networking, certificates, or core lifecycle to WayLane Node Agent.

The autonomous WayLane Node is **separate** from the legacy deployment
described below. Its Git-backed Compose definitions are now owned by
`deploy/node/discovery/` and `deploy/node/classic/`, each with exactly
two Compose files and a README. Each autonomous Node has a one-shot
privileged bootstrap and one non-privileged controller with private rootless
Docker. Single-Xray DEV control is implemented; other cores and production
VPN client ingress are not accepted. See `deploy/node/README.md` and
`agent/README.md`. Do not migrate this legacy stack as a side effect.

## Legacy single-process node


Use `docker-compose.yaml` for generic Compose and
`docker-compose.coolify.yaml` (Base Directory `/services/node`,
Compose Location `/docker-compose.coolify.yaml`) for a Git-backed Coolify
resource. Persist Watch Paths `services/node/**`.

This is a transport service on the HOST network, not an HTTP frontend.
Do not create a generated HTTP `SERVICE_FQDN_NODE_*` route: use node
enrollment/TLS reachability and explicitly approved external listener
configuration. Current Go `config.Load` requires non-zero UUID `API_KEY`.
Compose fails closed when the key is missing. Provision the node's TLS
certificate/key at `/var/lib/pg-node/certs/ssl_cert.pem` and `ssl_key.pem`
before startup. Existing host volume `/var/lib/pg-node` is retained for
backward compatibility; protect and back it up as durable secret/config state.

Current WireGuard kernel implementation needs host support, host networking,
and NET_ADMIN for routing/nftables. Compose never sets `privileged: true`,
but the current image runs as root; a non-root redesign is **not validated**.
Do not silently drop root, host networking or NET_ADMIN and declare WireGuard
functional. A narrower user/capability/provisioning lifecycle requires
isolated runtime acceptance, host network threat-model review, and explicit
operator approval. Use no extra operator ENV keys until agreed.

Node health/acceptance requires more than Docker process running:
validate mTLS/API authentication, heartbeat, Xray and kernel WireGuard paths,
certificate renewal, UDP/TCP reachability, restart, and rollback on a
disposable independent host. No production node changes are authorized.


## AWG 3.1 opt-in (Linux)

For AmneziaWG-enabled standalone nodes, use generic Compose with the
additional `docker-compose.amnezia.yaml` override, or in Coolify use
Compose Location `/docker-compose.amnezia.coolify.yaml`.
These variants mount host `/dev/net/tun`; normal kernel-only WireGuard
does not require this mount. Node image includes pinned userspace AWG-Go 3.1.
Old `engine`-less `wg` cores stay on kernel WireGuard.

For an isolated AWG core, choose WireGuard/AmneziaWG in the UI, then apply
the AmneziaWG 3.1 preset (or specify `engine: amneziawg` and an
`amnezia` settings object in core JSON). Prefer a distinct interface
and listen port while preserving active WireGuard clients. The panel exports
Amnezia parameters to client `.conf`; standard WG peers only interoperate
when obfuscation is off.

Validated in source: AWG UAPI/Go regression and upstream binary build.
NOT host-validated: TUN capability, real UDP handshake, traffic forwarding,
mobile client imports or rootless operation. Current AWG uses host network,
NET_ADMIN and /dev/net/tun in this opt-in baseline. Only perform live host
testing after authorization; never reconfigure current production nodes.

## Compatibility and supported client formats

AWG 3.1 uses the same asymmetric WireGuard identity and peer-address model,
but a profile with active AWG obfuscation is NOT interoperable with a plain
WireGuard client. Switching an already-running interface requires a planned
migration of all its active client profiles. For an opt-in AWG 3.1 core, the
backend builds its own AmneziaWG `.conf` profile with matching options.
Clash/Mihomo, sing-box, Xray and ordinary `wireguard://` exporters cannot
yet reproduce all AWG 3.1 obfuscation parameters and therefore skip AWG
hosts instead of advertising broken WireGuard connections. Do not assume
generic VPN clients will import every AWG-specific option.

Backend rejects AWG 3.1 core starts on WayLane Node versions older than
`0.6.0` so old Go agents never silently ignore unsupported settings.
Both backend and Go node must be upgraded together before assigning an AWG
core. Existing WireGuard cores require no config migration.
