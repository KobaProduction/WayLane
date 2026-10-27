# Coolify independent deployment and Watch Path design

| Owner | Base Directory | Dockerfile | Watch Paths |
| --- | --- | --- | --- |
| Backend | /services/backend | /Dockerfile | services/backend/** |
| Frontend | /services/frontend | /Dockerfile | services/frontend/** |
| Node | /services/node | /Dockerfile | services/node/** |
| Owned backend+PostgreSQL+PgBouncer | /deploy/backend-postgres | /docker-compose.coolify.yaml | deploy/backend-postgres/**, deploy/compose/pgbouncer/**, services/backend/** |
| All-in-One Control Plane | / | /docker-compose.coolify.yaml | docker-compose*.yaml, deploy/compose/**, services/backend/**, services/frontend/** |

Add narrow shared/contracts paths to watches only when a service actually
imports those exact contracts. Docker Compose Location is relative to
Base Directory, not repository root. Git-backed Dockerfile applications can
be created separately without one all-app Compose build.

DEV: development branch, each Coolify site connects GitHub App to the single
source and receives its own push webhook. Watch Paths must be persisted and
verified on both https://coolify.intens.cloud and
https://coolify.koba-nexus.ru. Do not guess deploy hook URL or secrets.
A push only rebuilds the affected module, not the whole repository.

PROD: independent signed/pinned release images. Proposed component-prefixed
tags backend-vX.Y.Z, frontend-vX.Y.Z and node-vX.Y.Z. Promote by immutable
digest, not floating latest, and keep rollback to previous artifacts.

PostgreSQL is internal ONLY in the separately selected All-in-One or owned
backend+PgBouncer deployment; the other standalone backend uses an external
PostgreSQL. The API balancer for split frontend/backend remains external.
Generated domains, HTTPS,
Coolify networks and protected env references are runtime state and need
explicit owner approval. Existing vpn-core/web-app/prod PostgreSQL remain intact.

Telemetry target: browser frontend reports bounded, authenticated, sanitized
events to backend; backend directly exports its own logs and traces via
OTLP/HTTP to the approved SigNoz. Never embed bearer in frontend. This replaces
the proposed always-on collector in this project's app topology, not the
already running external observability stack. Validate ingestion and refusal
of unauthorized browser reports before enabling.

Acceptance: native per-module verification; independent image builds;
parsed Coolify Watch Paths, generated domains and secret references; signed
GitHub App webhooks received on both sites; health/login/API; OTLP logs and
traces visible by service.name/namespace/environment; DB backup and rollback.
Merging to main does not activate or validate any Coolify deployment.
The frontend adapters declare SERVICE_FQDN_FRONTEND_8080 to request the
auto-generated domain. Verify the actual parser-generated domain, TLS,
frontend health, /api routing and /sub access policy on a DEV resource.

## Source-controlled Compose variants

The root docker-compose.yaml / docker-compose.coolify.yaml provide a
PostgreSQL+backend+frontend management stack; the module-local ordinary and
Coolify Compose pairs under services/backend and services/frontend support
independent Watch Paths and an externally owned database/API balancer.

Exact external variable contract, commands, security and source ownership:
deploy/compose/README.md. Actual Coolify parser resolution, Shared secrets,
domain generation and persistence must be verified before live deployment.

## Experimental WayLane Node Agent (not the legacy node)

The existing Node row above describes **legacy** `services/node/Dockerfile`
and host-network node deployment. Do not apply those assumptions to Node
WayLane Agent. The optional `services/node/docker-compose.agent.yaml` is one
independent **two-service** deployment: an exited privileged bootstrap plus
an autonomous non-privileged agent that supervises its own embedded rootless
Docker daemon. It has **no systemd, extra Compose rootless-engine service or
host Docker socket**. Future independently deployable VPN engine containers
are managed exclusively by the agent, but their lifecycle and real traffic
have not yet been implemented or accepted. This manifest is generic Compose;
no Coolify v2 resource, ENV or managed-domain contract has been approved.
Canonical details and runtime gates: `services/node/agent/README.md`.
