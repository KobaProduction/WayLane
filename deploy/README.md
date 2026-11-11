# WayLane — independently updateable deployment resources

WayLane's application stack is split into independent Coolify Compose
resources. **Do not** use the all-in-one root Compose or legacy
`deploy/backend-postgres/` for this installation.

| Resource | Base directory | Compose location | Watch Paths |
| --- | --- | --- | --- |
| **WayLane / Dependencies** | `/deploy/dependencies` | `/docker-compose.coolify.yaml` | `deploy/dependencies/**` |
| Backend | `/services/backend` | `/docker-compose.coolify.yaml` | `services/backend/**` |
| Frontend | `/services/frontend` | `/docker-compose.coolify.yaml` | `services/frontend/**` |
| VPN Node | `/services/node` | `/docker-compose.node.discovery.coolify.yaml` (or Classic) | `services/node/**` |

The **Coolify resource** is named `WayLane / Dependencies` even while
PostgreSQL is its only Compose service. This follows the shared-dependency
deployment ownership pattern; do not name the resource after a database
image. The inner Docker service remains `waylane-postgres` for private DNS.

**Watch Paths shown above are intended scopes, not automatically activated
by the `x-watch-path-coolify` documentation key.** A Coolify Public Git
source may not provide provider webhook Watch Paths. In that situation set
**Manual deployments only** on every affected resource and update each one
explicitly. Enable Git provider automatic deploys only after verifying
the GitHub App can access this repository, saving the actual Watch Paths
in each resource and testing independent deployments.

Use the same approved Git ref for the backend/frontend/dependency resources.
A new Node requires a **panel-issued** invitation or Classic token, not a
randomly fabricated registration secret. Node runs a separate Compose
resource and does not share the API database volume.

## Dependency isolation

PostgreSQL 17 is the only dependency in this stack initially. The
`waylane-postgres` service owns `postgres-data` exclusively. There is no
Valkey, PgBouncer or database container inside Backend. PostgreSQL is not
host-published. Backend must not start before PostgreSQL readiness and
successful schema migration. Separate Coolify resources do not support a
cross-resource `depends_on`; deploy and validate the dependency first.

Select the same **real predefined Docker network** for all three Coolify
resources and enable *Connect To Predefined Network* on each resource.
Verify that `waylane-postgres:5432` resolves/replies from Backend and
`waylane-api:8000` resolves from Frontend, rather than assuming a
Destination label creates a network connection. Do not publish the database
or API host port. Only Frontend owns a Coolify-managed HTTP(S) route:
`SERVICE_URL_FRONTEND_8080`.

Frontend builds the existing `deploy/nginx.bundled.conf`, which proxies
`/api/`, subscription requests and binary WebSocket upgrades to the
independent `waylane-api` service. Frontend and Backend source changes
must not trigger a PostgreSQL redeploy; verify persisted Watch Paths.

## Secret ownership

At the intended **Environment Shared** scope provision these keys without
putting their values in Git or revealing them in logs:

- `WAYLANE_POSTGRES_PASSWORD` — used by resource
  `POSTGRES_PASSWORD={{environment.WAYLANE_POSTGRES_PASSWORD}}`
  on both PostgreSQL and Backend
- `WAYLANE_TLS_SECRET_KEY` — Fernet encryption for backend-held TLS keys,
  referenced as `{{environment.WAYLANE_TLS_SECRET_KEY}}`
- `WAYLANE_DEVICE_WEBHOOK_SECRET` — internal webhook HMAC,
  referenced as `{{environment.WAYLANE_DEVICE_WEBHOOK_SECRET}}`

Use existing Team Shared `OTEL_ENDPOINT` and `OTEL_BEARER_TOKEN`
with `OTEL_ENDPOINT={{team.OTEL_ENDPOINT}}` and
`OTEL_BEARER_TOKEN={{team.OTEL_BEARER_TOKEN}}` **only after verifying
the keys exist**. The Backend maps these operator inputs to its internal
`OTLP_ENDPOINT` and `OTLP_BEARER_TOKEN` exporter configuration. Never
place the Bearer token in the frontend build or client bundle. Do not
generate parser `SERVICE_PASSWORD_*` credentials. Keep runtime-only secrets
out of image build arguments and read-only inspection surfaces.

PostgreSQL's database and user are both `waylane`. These are nonsecret,
stable topology defaults. Database password must be identical between
dependencies and Backend but the value must exist only once in the shared
scope. The Backend creates both runtime and migration DSNs internally using
`compose_db_entrypoint.py`.

## Validation, restarts and rollback

1. Verify generic Compose and Coolify-inherited Compose for every resource.
2. Check the Git revision and each resource's own Watch Paths before Deploy.
3. Start PostgreSQL and validate login, durable volume and readiness.
4. Start Backend, apply Alembic, verify authenticated API and the OTLP export
   without exposing credentials.
5. Start Frontend, verify HTTPS, same-origin `/api/` and WSS Upgrade.
6. Register and approve the separately deployed VPN Node; verify identity,
   core lifecycle and real client TCP/UDP traffic.

Changes to Backend/Frontend may replace only their own containers.
Keep PostgreSQL storage and the original database resource untouched until
a separately authorized database upgrade. Roll back an application using
its previous accepted Git revision; do not revert Alembic blindly or delete
its persistent state. A healthy container does not prove consumer readiness.
