# Coolify independent deployment and Watch Path design

| Owner | Base Directory | Dockerfile | Watch Paths |
| --- | --- | --- | --- |
| Backend | /services/backend | /Dockerfile | services/backend/** |
| Frontend | /services/frontend | /Dockerfile | services/frontend/** |
| Node | /services/node | /Dockerfile | services/node/** |

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

PostgreSQL and API balancer remain external. Generated domains, HTTPS,
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
This migration branch is not itself a running Coolify deployment.
