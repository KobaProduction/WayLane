# WayLane deployment variants (source files, not deployed)

The monorepo supports a bundled management stack and independently
deployable API/UI. A VPN node is never part of this management stack;
enroll and deploy nodes independently. Current node NET_ADMIN and host
network assumptions require separate least-privilege hardening.
No existing data volumes, credentials, Coolify apps or domains are touched
by committing these manifests.

## Bundled Compose (repository root)

* docker-compose.yaml — PostgreSQL 17, backend FastAPI, frontend Nginx.
* docker-compose.coolify.yaml — extends ordinary Compose with Coolify
  variable discovery and repository-relative Watch Paths.

A local, untracked .env file supplies the required upstream PostgreSQL
variables POSTGRES_USER and POSTGRES_PASSWORD. POSTGRES_DB defaults to
waylane. Both PostgreSQL and the backend use the SAME DB name and credentials.
The backend constructs its SQLALCHEMY_DATABASE_URL using SQLAlchemy escaping,
rather than unsafe unencoded password interpolation in YAML.

Run locally from the repository root:

    cp .env.example .env
    # Edit .env to set nonempty POSTGRES_USER and POSTGRES_PASSWORD.
    docker compose -f docker-compose.yaml config --quiet
    docker compose -f docker-compose.yaml up -d --build

Access http://127.0.0.1:8080/. The bundled nginx proxy forwards /api/ and
/sub/ to the private backend, ensuring same-origin auth and telemetry.
Only loopback 8080 is published; backend/database remain private. WayLane's TLS-free startup binds to the private Docker network alias waylane-api (not 0.0.0.0), allowing Nginx to reach it without exposing the API to public host interfaces.
For public use, add an approved HTTPS edge proxy: do not expose plaintext
admin UI to the Internet.

Coolify: Base Directory /, Compose Location /docker-compose.coolify.yaml.
Watch Paths include both service directories and the root/deploy Compose
sources. A change to either backend or frontend rebuilds this resource.

The DB volume is persistent. Changing POSTGRES_USER, POSTGRES_PASSWORD or
POSTGRES_DB on an existing PostgreSQL volume does NOT migrate existing
users, passwords or databases; perform explicit DB administration and backup.

## Split backend (services/backend)

* docker-compose.yaml — FastAPI only, external PostgreSQL and local port 8000.
* docker-compose.coolify.yaml — Coolify extends base with backend-only watches.

One Required existing backend variable SQLALCHEMY_DATABASE_URL contains the
external DB hostname, DB username, percent-encoded password and database
name. This is one canonical connection contract, not a redundant set of
POSTGRES_USER/PASSWORD plus DSN. Example shape ONLY, not a real secret:

    postgresql+asyncpg://USER:PERCENT_ENCODED_PASS@db.internal:5432/DATABASE

The external database owner supplies the actual DB name; the bundled DB
default does not silently override an independent database. Configure the
DSN via protected Coolify Shared scope. For the local plain Compose, use
an untracked environment file at services/backend/.env.

Coolify Base Directory /services/backend, Compose Location
/docker-compose.coolify.yaml; Watch Paths services/backend/** and
shared/contracts/backend/** (if consumed). Network access to the external DB
and ingress must be explicitly verified; the Compose does not create them.

## Split frontend (services/frontend)

* docker-compose.yaml — standalone Nginx UI, binds 127.0.0.1:8081.
* docker-compose.coolify.yaml — frontend-only watches, build-time API base.

The existing build argument VITE_BASE_API defaults to /. A separately owned
API load balancer or HTTPS ingress must forward /api/ and /sub/ to backend
while / goes to frontend. This enables one immutable frontend image across
environments. Separate-domain API hosting requires a separate frontend
build with an explicit HTTPS VITE_BASE_API and exact backend CORS allowlist.
Coolify Base Directory /services/frontend, Compose Location
/docker-compose.coolify.yaml; Watch Paths services/frontend/** and
shared/contracts/frontend/** (if consumed).

## Required/default variable contract proposed for Coolify

Variable                     Owner          Required   Default     Secret
POSTGRES_USER                bundled PG/BE  YES        blank       protected
POSTGRES_PASSWORD            bundled PG/BE  YES        blank       YES
POSTGRES_DB                  bundled PG/BE  no         waylane     no
SQLALCHEMY_DATABASE_URL      split backend  YES        blank       YES
VITE_BASE_API                split UI build no         /           no
OTLP_ENDPOINT                backend        optional   blank       metadata
OTLP_BEARER_TOKEN            backend        optional   blank       YES
OTEL_SERVICE_NAMESPACE       backend        optional   waylane     no
OTEL_DEPLOYMENT_ENVIRONMENT_NAME backend    optional   development no

OTLP_ENDPOINT and OTLP_BEARER_TOKEN must be set together for direct export.
These source defaults are NOT proof that Coolify has generated correct
Required/default flags or resolved the actual protected Shared values.
The full operator-variable contract, Coolify parser behavior, FQDN, networks
and production application placement require explicit approval/verification.

## Security and browser telemetry

The browser sends only allowed event-kind and coarse route-group fields to
/api/telemetry/browser; it never receives the OTLP bearer. Existing WayLane
admin authentication governs this endpoint. Unknown users get 401/403
without payload export, and only a throttled fixed
browser.unauthenticated WARN without IP, raw request, credential or URL.
Authorized browser events become sanitized server logs and can be exported
directly over OTLP/HTTP with the backend-only protected bearer.

This does NOT magically forward separate PostgreSQL or VPN node container
stdout/stderr logs; those need separately approved sources if required.

## Production

These are source-build developer/operator examples. GHCR release images
are produced by independently versioned component GitHub Releases.
Production must use verified immutable image digests, an explicit approved
deployment, data backup and rollback. Never auto-build production on push.
