# WayLane monorepo — service owners

KobaProduction/WayLane main is the authoritative source layout following
monorepo merge. All active source, CI, release and Coolify build contexts must use this single monorepo.
The former frontend and node repositories have been removed. No separate
repository is authoritative for backend/frontend/node builds or deployments.

Read ArthurKoba/ai-agent-workflow/AGENTS.md and the skill for the task.
Read intens-cloud/infrastructure/AGENTS.md before infrastructure operations.

## Ownership

- services/backend/: FastAPI API, durable data, subscriptions, scheduler,
  node orchestration, telemetry relay/export.
- services/frontend/: separately built React/Vite UI and browser diagnostics.
  No database credentials or SigNoz bearer may enter this bundle.
- services/agent/: stable installed supervisor and rootless Docker owner;
  manages replaceable Node image via private versioned Unix control API.
- services/node/: independently updatable Go Node image; owns one outbound
  WSS/Protobuf connection and manages VPN Cores using Agent-owned private Docker.
- deploy/node/{classic,discovery}/: separate Git-backed deployment owners,
  each with `docker-compose.yaml` and `docker-compose.coolify.yaml`; shared operator README at `deploy/node/README.md`.
  See `deploy/node/README.md` for Watch Paths, secrets and test boundaries.
- services/external/: approved contracts for separate PostgreSQL,
  API load balancer/ingress, external SigNoz OTLP endpoints.
- shared/contracts/: narrowly scoped protocol DTOs, no ORM imports.

Each internal service owns its Dockerfile, dependencies, tests and deployment.
CI and Coolify Watch Paths must scope independently to each module.
Preserve all original upstream licenses and service history.
No production DNS, data, token or routing changes before authorized cutover.

## Agent -> Node -> Core architecture

The installation has exactly two outer Compose services: one-shot privileged
bootstrap and a stable non-root Agent. Agent owns private rootless Docker
Engine 29.5+ and independently versioned Node lifecycle/recovery.
Node is a separate inner container with the only outbound binary WSS to Panel,
and manages VPN Cores (AWG, WireGuard, Xray and future engines) as siblings.
There is no additional host daemon or host Docker socket.
Agent's private Unix API v1 updates Node without reinstalling Agent.

The outer Agent uses Linux host networking, and Node/Cores use host networking
inside rootless Docker 29.5+. No static Compose port mappings: each Core
controls its own unprivileged TCP/UDP listeners dynamically. Privileged ports
below 1024, cloud firewalls, Docker Desktop network modes, TUN, UDP traffic
and restart acceptance remain distinct operational gates. Source CI is not
runtime acceptance.
