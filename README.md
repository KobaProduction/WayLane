# WayLane — flat service-folder monorepo

The monorepo was merged into main on 2026-10-26; this did not deploy
WayLane to production. One Git source has independent service owners.
The former frontend and node repositories were removed. The canonical
GitHub source for all three services is KobaProduction/WayLane.

| Owner folder | Runtime | Deployment |
| --- | --- | --- |
| services/backend/ | Python 3.14, FastAPI, scheduler, node RPC | independent Coolify backend |
| services/frontend/ | React/Bun, static Nginx | independent Coolify frontend |
| services/node/ | Legacy Go node (Xray, WireGuard, opt-in AWG 3.1) + separate WayLane Agent prototype | independently deployed; experimental agent owns rootless Docker and one Xray DEV child |
| services/external/ | managed PostgreSQL, ingress, API balancer, OTLP | separately owned infrastructure |
| shared/contracts/ | narrowly shared wire DTOs | docs / protocol checks |

Each service keeps a native Dockerfile, dependency lock and tests.
Root CI and planned Coolify resources watch only affected directories.
DEV builds from source in Coolify; PROD pulls pinned GHCR release images.

Provenance snapshot (2026-10-26):
- backend f5eba56a0315ff99964ffd2c9a2a652513ea48b8
- frontend 4b6656e19b11199e2117163c73bfc22770f4d247
- node a03465c385f3d5bf79dc1766444feeab3aea1803

Source and upstream LICENSE, UPSTREAM and LICENSING remain with each module.
The original frontend and node Git histories are joined as separate merge-parent
ancestries; their historical paths are unchanged. Historical commits remain available in this repository even though the
former standalone repositories were retired. Do not restore their links
in current runtime or release consumers.
Read docs/deployment/README.md for gates and topology.

## Licensing and WayLane identities

The existing upstream licenses remain in force: AGPL-3.0 for imported
panel/frontend and vendored configuration kits; GPL-3.0 for the imported node
and bundled Python bridge. Their source attribution and license texts remain
unchanged. The repository's original MIT `LICENSE` does not relicense any
upstream-derived source. See each component's `LICENSING.md`.

Administrative keys issued after this change are `waylane_<UUIDv4>`.
The bundled Go NodeService and Python bridge use the WayLane-native
`waylane.node.v1.NodeService` RPC identity; previously deployed clients and
API keys with PasarGuard identifiers will no longer authenticate/connect.
Rotate administrative keys and deploy both RPC sides together before using
this version. **This source change does not deploy or migrate running nodes.**

## Deployment options

Use ordinary Compose or Coolify adapters for either a self-contained
PostgreSQL/backend/frontend management stack or separately deployed
backend/frontend with external DB and API ingress. The deployment inputs,
credentials, smoke commands and security constraints are documented at
[deploy/compose/README.md](deploy/compose/README.md).

For Intens VPN, deployment definitions live in
[intens-cloud/infrastructure/deploy/vpn/waylane](https://gitlab.com/intens-cloud/infrastructure/-/tree/master/deploy/vpn/waylane).
The backend and frontend build inputs belong to this monorepo; the customer
Web App and VPN Core are separate services.

## WayLane Node Agent (opt-in, experimental)

The new node uses one Compose resource with exactly two services:
one-shot privileged `bootstrap` and autonomous non-privileged `agent`.
The agent owns its private embedded rootless Docker daemon and controls
a single Xray DEV child; binary WebSocket and gRPC control transports
are implemented in source, but end-to-end acceptance and public VPN traffic
have not been demonstrated for this merged revision. Other engines and
networking/privilege hardening remain future work.
The legacy Go node is preserved independently until migration acceptance.
See [services/node/agent/README.md](services/node/agent/README.md).
