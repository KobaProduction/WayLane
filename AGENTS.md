# WayLane Node — agent map

Source: https://github.com/KobaProduction/waylane-node

Reusable engineering guidance: https://github.com/ArthurKoba/ai-agent-workflow
Read the workflow AGENTS.md; load relevant Git, Go and service-engineering skills only.

## Code and contracts
- `cmd/node/`: agent entry point.
- `backend/xray/`, `backend/wireguard/`: current engines.
- `controller/`: gRPC and REST integration.
- `common/service.proto`: compatibility contract.
- `scripts/verify.sh`: tests, vet and build.
- `scripts/prepare-test-runtime.sh`: pinned test-only dependencies.
- `UPSTREAM.md` and `LICENSING.md`: source provenance and license scope.

Retain required node/protocol compatibility; do not change production nodes without approval.
