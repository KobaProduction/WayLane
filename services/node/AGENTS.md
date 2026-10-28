# WayLane Node — agent map

Source: https://github.com/KobaProduction/waylane (services/node/)

Reusable engineering guidance: https://github.com/ArthurKoba/ai-agent-workflow
Read the workflow AGENTS.md; load relevant Git, software-engineering and
services-deployment skills only. Experimental AmneziaWG 3.1 runs solely by
explicit engine=amneziawg; legacy kernel WG must remain the default. Consult
DEPLOYMENT.md and pinned upstream AmneziaWG attribution before changing it.

## Code and contracts
- `cmd/node/`: agent entry point.
- `backend/xray/`, `backend/wireguard/`: current engines, including
  opt-in userspace AmneziaWG 3.1 via the WG bridge and UAPI.
- `controller/`: gRPC and REST integration.
- `common/service.proto`: compatibility contract.
- `scripts/verify.sh`: tests, vet and build.
- `scripts/prepare-test-runtime.sh`: pinned test-only dependencies.
- `UPSTREAM.md` and `LICENSING.md`: source provenance and license scope.

Retain required node/protocol compatibility; do not change production nodes without approval.
