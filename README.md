# WayLane — flat service-folder monorepo

The monorepo was merged into main on 2026-10-26; this did not deploy
WayLane to production. One Git source has independent service owners.
The former frontend and node repositories were removed. The canonical
GitHub source for all services is KobaProduction/WayLane.

| Owner folder | Runtime | Deployment |
| --- | --- | --- |
| services/backend/ | Python 3.14, FastAPI, scheduler, binary WSS Node control | independent Coolify backend |
| services/frontend/ | React/Bun, static Nginx | independent Coolify frontend |
| services/agent/ | Go supervisor, private rootless Docker, immutable Node upgrades | stable outer Agent; separate Node image lifecycle |
| services/node/ | Go WSS/Protobuf Node, child Xray, gated AWG 3.1 | replaceable internal Node (owned by Agent) |
| deploy/node/ | Classic/Discovery Git-backed Compose | independent, manual-only Node installation |
| services/external/ | managed PostgreSQL, ingress, API balancer, OTLP | separately owned infrastructure |
| shared/contracts/ | narrowly shared wire DTOs | docs / protocol checks |

Each service keeps a native Dockerfile, dependency lock and tests.
Root CI validates the affected service owners independently. The active
Coolify Backend/Frontend resources deploy manually; Dependencies and the
unaccepted Node installer must not redeploy on ordinary source pushes.
DEV builds from source in Coolify; PROD releases use pinned image digests.

## Git validation

**Delivery policy:** validate locally and push fast-forward commits directly to
`origin/main` when it is unprotected and GitHub authorizes it. No PR is
required by default. Never bypass branch protection, hooks or a required
review; report the restriction instead. A source push does not imply a
deployment of PostgreSQL or the unaccepted Node runtime.

This monorepo already tracks a root `pre-push` gate. Use
`git config --local core.hooksPath .githooks` in a regular checkout, or
`git config --worktree core.hooksPath .githooks` in a linked worktree.
The hook runs `scripts/verify-layout.py` and each affected owner's
`services/{backend,frontend,node}/scripts/verify.sh` and
`services/agent/verify.sh` based on the changes since `origin/main`.
A cross-service release checks all four owners.
Follow each service's documented toolchain requirements before pushing.
This gate does not replace backend integration tests or real VPN/Node acceptance.

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
For all-in-one and external-dependency Compose variants, use the
[deployment contracts](deploy/compose/README.md).

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

## WayLane Agent → Node → VPN Cores

The Git-backed Node installer contains exactly two **outer** Compose services:
a one-shot privileged `bootstrap` and a stable non-root `agent`. The Agent
owns the single embedded rootless Docker engine and an independently updatable
Node container. Only the Node communicates with the Panel via one outbound
binary-Protobuf WSS session, and only the Node owns child VPN Cores.

Xray source-level lifecycle support exists; AmneziaWG 3.1 is an explicitly
disabled experimental profile until real TUN/UDP handshakes and routing are
validated. Neither source tests nor running containers establish production
VPN readiness. The deprecated combined PasarGuard Node RPC runtime is removed
from this integration; preserved upstream history remains in Git.

Installation source: [deploy/node/README.md](deploy/node/README.md).
Architecture: [Agent](services/agent/README.md) and
[replaceable Node](services/node/README.md).
