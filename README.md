# WayLane — flat service-folder monorepo

Staged migration branch, NOT production authority. One Git source, separate
independent service owners, following Briareus style. Do not delete or archive
the original three repositories until cutover is verified.

| Owner folder | Runtime | Deployment |
| --- | --- | --- |
| services/backend/ | Python 3.14, FastAPI, scheduler, node RPC | independent Coolify backend |
| services/frontend/ | React/Bun, static Nginx | independent Coolify frontend |
| services/node/ | Go, Xray, WireGuard | independently deployed privileged VPN nodes |
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
Read docs/deployment/README.md for gates and topology.
