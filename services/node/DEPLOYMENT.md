# VPN node — separate deployment, NEVER All-in-One

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
