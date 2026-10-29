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
- services/node/: one autonomous Go Node module (single Go entrypoint),
  rootless child container control, bootstrap and WSS/Protobuf. Source,
  not a deployment manifest.
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

## WayLane Node architecture authority

Read `services/node/AGENTS.md` and `services/node/README.md`
before changing the autonomous Node. Each Node deployment entrypoint has **exactly two services**:
a one-shot privileged `bootstrap` preparing private volumes, then one
non-privileged autonomous `agent` service running the sole Node
program. The Node supervises its own embedded rootless Docker daemon
through a private Unix socket. Neither host systemd,
a separate Compose Docker sidecar, nor the host/system Docker socket belongs
in this architecture. Only the Node communicates with the panel; the Node
owns one DEV Xray child (deploy/pull/update/restart/stop,
per-inbound account updates), while multi-engine drivers, public UDP/TCP ingress
and production traffic are **not** accepted.
The security/runtime pilot is not accepted by source CI alone.

The control channel and Node-versus-Core ownership contract is
documented in shared/contracts/NODE_CONTROL.md. One Agent owns machine
telemetry; child cores own only their separate service traffic and state.
The Node control protocol is **WebSocket with binary Protobuf frames only**. The Node initiates one WSS session and receives panel commands on the same connection. No separate gRPC Node control server/client, HTTP polling, SSE, JSON frames, or compatibility transport aliases are supported. See shared/contracts/NODE_CONTROL.md; production WSS acceptance is separate.
