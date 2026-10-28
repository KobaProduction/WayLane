# WayLane — flat service-folder monorepo

The monorepo was merged into main on 2026-10-26; this did not deploy
WayLane to production. One Git source has independent service owners.
The former frontend and node repositories were removed. The canonical
GitHub source for all three services is KobaProduction/WayLane.

| Owner folder | Runtime | Deployment |
| --- | --- | --- |
| services/backend/ | Python 3.14, FastAPI, scheduler, node RPC | independent Coolify backend |
| services/frontend/ | React/Bun, static Nginx | independent Coolify frontend |
| services/node/ | Go, Xray, kernel WireGuard and opt-in AmneziaWG 3.1 | independently deployed VPN nodes; rootless hardening pending |
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

## Deployment options

Use ordinary Compose or Coolify adapters for either a self-contained
PostgreSQL/backend/frontend management stack or separately deployed
backend/frontend with external DB and API ingress. The deployment inputs,
credentials, smoke commands and security constraints are documented at
[deploy/compose/README.md](deploy/compose/README.md).

For Intens VPN, the active Git-backed deployment definitions live in
[intens-cloud/infrastructure/deploy/vpn/waylane](https://gitlab.com/intens-cloud/infrastructure/-/tree/master/deploy/vpn/waylane).
That resource uses external PostgreSQL/PgBouncer and is distinct from the
customer Web App and VPN Core. The backend/frontend build inputs come
from this monorepo, not deleted standalone repositories.
