# Intens VPN — WayLane management panel

**Prepared, not yet deployed.** WayLane is a management panel distinct from the Intens customer Web App and VPN Core. The resource contains its own backend (FastAPI) and frontend (Nginx). VPN nodes are independently deployed; none is added to this Compose. Its PostgreSQL server and login role are provided by the separate Intens infrastructure dependency stack; its logical database is created by WayLane itself during initial startup.

## Coolify source deployment

- Repository: KobaProduction/waylane, branch main.
- Base Directory: /deploy/intens.
- Docker Compose Location: /docker-compose.coolify.yaml.
- Watch Paths: deploy/intens/**, services/backend/**, services/frontend/**; add shared/contracts/** only when code consumes it.
- Build: DEV builds both images from their owner folders; PROD requires reviewed immutable image digests.
- DEPLOYMENT_ENVIRONMENT=development uses network intens-vpn-development; production uses intens-vpn-production. Both Coolify resources must be on the same Docker host, and infrastructure must create the network before WayLane starts.
- Coolify generates one public frontend route: SERVICE_URL_FRONTEND_8080, port 8080. The backend stays private as waylane-api:8000. Do not configure a second domain or manually supply a redundant canonical URL.
- The frontend image bakes the tracked `services/frontend/deploy/nginx.intens.conf` at build time using `NGINX_CONFIG=deploy/nginx.intens.conf`; there is **no runtime host file bind mount** to fail under Coolify. The generic standalone frontend still defaults to `services/frontend/deploy/nginx.conf`. The bundled Nginx proxy sends `/api/` and `/sub/` to backend over the private Docker network, suppressing `/sub/` access logs because paths can contain bearer-like tokens.

## Database and secrets

Infrastructure provisions the dedicated login role `waylane` with `CREATEDB` and configures the third PgBouncer database entry, but **does not create the WayLane database**. The one-shot `db-init` connects directly to `postgres:5432` as `waylane`, creates the missing `waylane` database if needed, and completes before the backend performs Alembic migrations. Role and database are fixed as waylane in Compose/bootstrap; the only WayLane DB operator input is required secret WAYLANE_POSTGRES_PASSWORD. The backend maps those to upstream POSTGRES_* and constructs SQLALCHEMY_DATABASE_URL using the existing SQLAlchemy helper (no duplicate manually maintained DSN). PgBouncer is reachable as database:5432, not via host ports. Passwords embedded in its configuration URI must be URL-safe. Never reuse VPN Core or ZITADEL logical databases.

Persistent waylane-data is **application-local** (not a second PostgreSQL volume); back it up and verify its purpose before resetting. Backend runs Alembic migrations at startup. Do not connect an old production panel database without an explicit migration and backup.

Backend telemetry goes to the private collector on port 4318 without distributing a remote Bearer. Environment resource attributes derive from DEPLOYMENT_ENVIRONMENT. Never pass bearer, PostgreSQL credentials or private endpoints into the frontend bundle. WAYLANE_WORKERS defaults to 1; scaling worker count requires separate migration/scheduler locking review.

DEV: WAYLANE_PULL_POLICY=build and default local image tags work with the Git-backed source. PROD: require verified GHCR image digests for frontend and backend and WAYLANE_PULL_POLICY=always. This Compose retains build metadata and is **not production approved** without a separate image/release acceptance review.

## First managed administrator

Once the backend Alembic migrations succeed, use the interactive Coolify Terminal for the backend and run waylane-cli generate-temp-key. The CLI now uses the same PostgreSQL identity as the API rather than silently falling back to a local SQLite file. Pass the one-use key (valid five minutes) to POST /api/setup/owner with a secure owner password; never record the key in chat or CI logs. This setup neither uses the PostgreSQL superuser nor the local-development-only init-owner command. Verify owner login, then issue a scoped pg_key_* management API key for the VPN Core provider adapter.

## Acceptance

Verify in Coolify's Deployable Compose that there are three services (including one-shot `db-init`), one named volume, correct external private network, valid mounts and only the frontend receives a generated domain. Then verify the frontend health, same-origin /api/ login, /sub/ routing and log sanitization, database migrations, panel persistence, telemetry and restart/rollback. Existing WayLane admin authorization is still legacy: merely connecting this deployment to a ZITADEL-capable infrastructure does **not** implement ZITADEL OIDC.

Publishing the source does not create the Coolify application or change currently running VPN Core/Web App/WayLane nodes.
