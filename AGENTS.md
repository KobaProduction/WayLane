# WayLane monorepo — service owners

KobaProduction/waylane main is the authoritative source layout following
monorepo merge. The former frontend/node repositories remain available for
compatibility and rollback until the retirement audit, dependency migration
and deployment cutover are accepted.

Read ArthurKoba/ai-agent-workflow/AGENTS.md and the skill for the task.
Read intens-cloud/infrastructure/AGENTS.md before infrastructure operations.

## Ownership

- services/backend/: FastAPI API, durable data, subscriptions, scheduler,
  node orchestration, telemetry relay/export.
- services/frontend/: separately built React/Vite UI and browser diagnostics.
  No database credentials or SigNoz bearer may enter this bundle.
- services/node/: separately deployed legacy Go VPN node plus an opt-in WayLane Node Agent prototype.
  The legacy node remains host-network/NET_ADMIN dependent; do not ascribe those
  privileges or its single-core lifecycle to WayLane Node Agent.
- services/external/: approved contracts for separate PostgreSQL,
  API load balancer/ingress, external SigNoz OTLP endpoints.
- shared/contracts/: narrowly scoped protocol DTOs, no ORM imports.

Each internal service owns its Dockerfile, dependencies, tests and deployment.
CI and Coolify Watch Paths must scope independently to each module.
Preserve all original upstream licenses and service history.
No production DNS, data, token or routing changes before authorized cutover.

## WayLane Node Agent architecture authority

Read `services/node/AGENTS.md` and `services/node/agent/README.md` before
changing the autonomous agent-based node. Its single Compose entrypoint has **exactly two services**:
a one-shot privileged `bootstrap` preparing private volumes, then one
non-privileged autonomous `agent`. The agent supervises its own embedded
rootless Docker daemon through a private Unix socket. Neither host systemd,
a separate Compose Docker sidecar, nor the host/system Docker socket belongs
in this architecture. Only the agent communicates with the panel; the agent
will eventually own child-container lifecycle for Xray, sing-box, WireGuard,
AmneziaWG and plugin engines. That child lifecycle is **deferred**, not shipped.
The security/runtime pilot is not accepted by source CI alone.
