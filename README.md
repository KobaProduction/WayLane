# WayLane — flat service-folder monorepo

The monorepo was merged into main on 2026-10-26; this did not deploy
WayLane to production. One Git source has independent service owners.
Retain former frontend/node repositories until release consumers and
runtime cutover are verified.

| Owner folder | Runtime | Deployment |
| --- | --- | --- |
| services/backend/ | Python 3.14, FastAPI, scheduler, node RPC | independent Coolify backend |
| services/frontend/ | React/Bun, static Nginx | independent Coolify frontend |
| services/node/ | Legacy Go node (Xray, WireGuard, opt-in AWG 3.1) + separate WayLane Agent prototype | independently deployed; experimental agent owns embedded rootless Docker, not VPN cores yet |
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
ancestries; their historical paths are unchanged. Importing history is not
authorization to delete the original repositories or their releases.
Read docs/deployment/README.md for gates and topology.

## Deployment options

Use ordinary Compose or Coolify adapters for either a self-contained
PostgreSQL/backend/frontend management stack or separately deployed
backend/frontend with external DB and API ingress. The deployment inputs,
credentials, smoke commands and security constraints are documented at
[deploy/compose/README.md](deploy/compose/README.md).

## WayLane Node Agent (opt-in, not a production replacement)

The experimental node uses one Compose resource with only `bootstrap` and
`agent`. The agent manages an embedded rootless Docker daemon; the panel
will control future pluggable VPN-engine containers **through that agent**, never
directly. Deployment, upgrade and rollback of child VPN cores remain unimplemented.
The legacy node remains separate until a deliberate migration and runtime acceptance.
Canonical contract: [services/node/agent/README.md](services/node/agent/README.md).
