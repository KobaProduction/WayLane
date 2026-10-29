# WayLane Compose deployment variants

WayLane is independent of Intens infrastructure. The canonical source is the
WayLane monorepo; one Coolify application can switch between the two profiles
below. VPN nodes are deployed separately and are never added to these manifests.
The public frontend proxies the private backend's `/api/`, binary Agent WSS,
and `/sub/` routes without exposing backend or database ports.

## 1. Full stack — PostgreSQL + PgBouncer + backend + frontend

- Source directory: repository root `/`
- Generic: `/docker-compose.yaml`
- Coolify: `/docker-compose.coolify.yaml`
- Four Compose services, two private named volumes (`postgres_data`,
  `backend_data`), isolated `application` and `database` networks.
- Local generic frontend listens at `127.0.0.1:8080`. Coolify frontend
  uses bundled Nginx (no host-port or repository-file mounts) and one managed
  `SERVICE_URL_FRONTEND_8080` public HTTPS route.

Resource/operator variables are `POSTGRES_USERNAME` (defaults to `waylane`),
`POSTGRES_PASSWORD` (required; no generated default),
`POSTGRES_DATABASE` (defaults to `waylane`). These map to the official
PostgreSQL image's `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`.
Changing their values after initializing a persistent PostgreSQL data volume
does not change existing database credentials or data.

The backend uses `database:5432` (PgBouncer) at runtime and
`postgres:5432` (direct PostgreSQL) for Alembic. Its entrypoint constructs
percent-escaped `SQLALCHEMY_DATABASE_URL` and
`SQLALCHEMY_MIGRATION_DATABASE_URL`; only the migration connection is used
for DDL. No separate database schema or login is invented.

## 2. External dependencies — backend + frontend only

- Source directory: `/deploy/external`
- Generic: `/docker-compose.yaml` relative to this source directory
- Coolify: `/docker-compose.coolify.yaml` relative to this directory
- Only two services: backend and frontend. **No local PostgreSQL, PgBouncer,
  database-initialization container, Valkey or standalone gateway.**
- `EXTERNAL_NETWORK_NAME` (required) is the **existing Docker network name**.
  Backend joins it with alias `waylane-api` and frontend joins the same
  network. A separate `egress` bridge is used for outbound traffic. The
  named `waylane-data` volume preserves the prior external-resource mount
  key `/var/lib/pasarguard`; confirm its actual physical identity in Coolify
  before switching an existing running application.

### Database input contract

Use the same operator-facing database primitives as VPN Core: a database
identity, a hostname, and a port. WayLane runs migrations in the backend
startup, so an optional separate direct-PostgreSQL hostname is used for
Alembic. Both connections share the same port by default.

| Resource variable | Default | Meaning |
| --- | --- | --- |
| `POSTGRES_USERNAME` | empty | PostgreSQL login; can reference Shared Variables |
| `POSTGRES_PASSWORD` | empty | Protected PostgreSQL password |
| `POSTGRES_DATABASE` | `waylane` | Logical database name |
| `POSTGRES_HOST` | `database` | Runtime hostname, typically PgBouncer |
| `POSTGRES_PORT` | `5432` | Port for both runtime and migrations |
| `POSTGRES_MIGRATION_HOST` | `postgres` | Direct PostgreSQL hostname for migrations |

**No operator-facing URLs** are used. Both full SQLAlchemy DSNs are built
internally using the same credentials, database and port. For installations
without PgBouncer, set `POSTGRES_MIGRATION_HOST` to the same value as
`POSTGRES_HOST`. If the migration hostname is empty in the backend, it
falls back to the runtime hostname; the external Compose defaults to
`postgres` for the PgBouncer scenario.

The backend does **not** provision, recreate, or rename the external database.
Its `POSTGRES_AUTO_CREATE_DB` remains false. Startup migrations operate
against the selected preexisting database using the direct migration DSN.

For a Coolify resource, the short operator keys can refer to existing shared
entries (illustrative references, **not committed secret values**):

```dotenv
EXTERNAL_NETWORK_NAME=<existing-docker-network-name>
POSTGRES_USERNAME={{environment.WAYLANE_POSTGRES_USERNAME}}
POSTGRES_PASSWORD={{environment.WAYLANE_POSTGRES_PASSWORD}}
POSTGRES_DATABASE=waylane
POSTGRES_HOST=database
POSTGRES_PORT=5432
POSTGRES_MIGRATION_HOST=postgres
```

Do not pass these reference strings as literal Compose values. Coolify must
resolve them from the approved scoped Shared variables. Verify the actual
network contains both the PgBouncer and direct PostgreSQL DNS names, and
that the user/database exists; no automatic cross-resource network attachment
is implied by a Coolify Destination label.

### Optional OpenTelemetry in both variants

| Variable | Default | Effect |
| --- | --- | --- |
| `OTEL_ENDPOINT` | empty | No telemetry export when empty |
| `OTEL_BEARER_TOKEN` | empty | Adds `Authorization: Bearer <token>` only when set |
| `OTEL_NAMESPACE` | `waylane` | `service.namespace` |
| `DEPLOYMENT_ENVIRONMENT` | `development` | `deployment.environment.name` |

The Compose adapter maps these names to the backend's existing
`OTLP_ENDPOINT`, `OTLP_BEARER_TOKEN`,
`OTEL_SERVICE_NAMESPACE`, and
`OTEL_DEPLOYMENT_ENVIRONMENT_NAME`. Both signals use OTLP/HTTP
(`/v1/traces`, `/v1/logs`). HTTPS endpoints may have or omit the Bearer
token. Explicit private-network HTTP collector endpoints are permitted;
public hosts must use HTTPS. The browser never receives exporter credentials.
Empty endpoint + nonempty token still exports **nothing**.

## Existing Coolify resource: migration without creating another app

The previous infrastructure-sourced WayLane application uses a two-service
Compose resource and an existing external PostgreSQL/PgBouncer. Keep its
resource ID, environment, current public domain and database owner; switch
the Git source to `KobaProduction/WayLane` branch `main`, set Base Directory
`/deploy/external`, Compose Location `/docker-compose.coolify.yaml`, and
set Watch Paths `deploy/external/**`, `services/backend/**` and
`services/frontend/**`. `x-watch-path-coolify` is documentation, not
an automatic Coolify setting. Enable GitHub push-triggered Auto Deploy after
the configuration has passed parser and runtime acceptance.

**Before the first redeploy**, preserve the old effective Compose,
protected Shared variable references, real network, physical data volume,
public route, working DB and last known-good commit. Confirm that
`waylane-data` resolves to the **existing** physical volume; Coolify may
rename volumes when changing Compose source/base directory. Do not blindly
create or attach a blank volume or a second writable PostgreSQL.

The currently running deployment may still use API keys beginning with
`pg_key_`; new WayLane source accepts `waylane_` keys and old credentials
must be rotated before this backend cutover. Preserve the old service until
the new source successfully passes database migration, health, owner login,
frontend, `/api/`, `/sub/`, and authenticated binary Agent WSS checks.
Rollback requires restoring the prior Git/Compose source and physical
volume/DB authority, not only changing the branch.

**Source test success is not live redeploy acceptance.** No live Coolify
resources, credentials, domains, databases, or networks are modified merely
by publishing these Compose files. Production promotion remains separate.

## Other preexisting variants

An isolated backend with its own PostgreSQL+PgBouncer remains available at
`deploy/backend-postgres/`. The backend-only `services/backend/` and
frontend-only `services/frontend/` manifests are retained for independent
service management; they do not replace the two canonical grouped profiles.
