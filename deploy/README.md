# WayLane — independently updateable deployment resources

WayLane's application stack is split into independent Coolify Compose
resources. **Do not** use the all-in-one root Compose or legacy
`deploy/backend-postgres/` for this installation.

| Resource | Base directory | Compose location | Deployment updates |
| --- | --- | --- | --- |
| **WayLane / Dependencies** | `/deploy/dependencies` | `/docker-compose.coolify.yaml` | **Manual only**; PostgreSQL is not redeployed for app changes |
| **WayLane / Backend** | `/services/backend` | `/docker-compose.coolify.yaml` | Manual today; when Git push integration is available, deploy on any relevant commit |
| **WayLane / Frontend** | `/services/frontend` | `/docker-compose.coolify.yaml` | Manual today; when Git push integration is available, deploy on any relevant commit |
| VPN Node (future pilot) | `/services/node` | `/docker-compose.node.discovery.coolify.yaml` (or Classic) | Manual until runtime acceptance |

The resource named **WayLane / Dependencies** contains the single PostgreSQL
service `waylane-postgres`; it is not a second application runtime.
An isolated PostgreSQL lifecycle must never be tied to frontend/backend
source pushes. Do not configure a file-path filter just to achieve this:
keep the dependency resource on manual deployment. For application resources,
`Watch Paths` are optional; if GitHub push automation is later enabled,
the intended behavior is a deployment for source changes without requiring
narrow file globs. A placeholder `x-watch-path-coolify` has no effect on
Coolify's actual deployment configuration.

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
must not trigger a PostgreSQL redeploy; keep Dependencies manual.

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

## Telemetry identity

Backend exports OpenTelemetry logs and traces directly to the configured
`OTEL_ENDPOINT`. The canonical resource identity is
`service.namespace=WayLane`, `service.name=backend`, and
`deployment.environment.name=development`. Source-controlled service
settings use `OTEL_NAMESPACE`, `OTEL_SERVICE_NAME`, and
`DEPLOYMENT_ENVIRONMENT`; no secret value is embedded in the image.
The runtime still recognizes the previous internal namespace/environment
keys for older deployment variants.

Frontend Nginx access logs identify themselves as
`service.namespace=WayLane`, `service.name=frontend` on stdout.
**Stdout alone is not OTLP**: verify an independent Docker log collector
before claiming that Nginx access logs are available in SigNoz. Never inject
a collector Bearer into the browser or frontend container solely to label
its logs. Authenticated, sanitized browser errors are sent to the Backend
telemetry endpoint and exported by that trusted service.

## Validation, restarts and rollback

1. Verify generic Compose and Coolify-inherited Compose for every resource.
2. Check the Git revision and the dependency's manual-deployment policy before Deploy.
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
