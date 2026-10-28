# WayLane monorepo — service owners

KobaProduction/waylane main is the authoritative source layout following
monorepo merge. All active source, CI, release and Coolify build contexts must use this single monorepo.
Former split repositories are not active source authorities and must not be used
for builds, release checks or deployments; removal requires repository administration.

Read ArthurKoba/ai-agent-workflow/AGENTS.md and the skill for the task.
Read intens-cloud/infrastructure/AGENTS.md before infrastructure operations.

## Ownership

- services/backend/: FastAPI API, durable data, subscriptions, scheduler,
  node orchestration, telemetry relay/export.
- services/frontend/: separately built React/Vite UI and browser diagnostics.
  No database credentials or SigNoz bearer may enter this bundle.
- services/node/: Go VPN agent, Xray and WireGuard; currently NET_ADMIN/host-network dependent,
  independently deployed and never a sidecar in frontend/backend.
- services/external/: approved contracts for separate PostgreSQL,
  API load balancer/ingress, external SigNoz OTLP endpoints.
- shared/contracts/: narrowly scoped protocol DTOs, no ORM imports.

Each internal service owns its Dockerfile, dependencies, tests and deployment.
CI and Coolify Watch Paths must scope independently to each module.
Preserve all original upstream licenses and service history.
No production DNS, data, token or routing changes before authorized cutover.
