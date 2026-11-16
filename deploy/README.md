# WayLane — independently updateable deployment resources

WayLane's application stack is split into independent Coolify Compose
resources. **Do not** use the all-in-one root Compose or legacy
`deploy/backend-postgres/` for this installation.

| Resource | Base directory | Compose location | Saved Coolify Watch Paths | Deployment policy |
| --- | --- | --- | --- | --- |
| **WayLane / Backend** | `/services/backend` | `/docker-compose.coolify.yaml` | `services/backend/**` | GitHub App → automatic on `main` push |
| **WayLane / Frontend** | `/services/frontend` | `/docker-compose.coolify.yaml` | `services/frontend/**` | GitHub App → automatic on `main` push |
| **WayLane / Dependencies** | `/deploy/dependencies` | `/docker-compose.coolify.yaml` | Not applicable while manual; future scope `deploy/dependencies/**` | **Manual only**; PostgreSQL must not redeploy for app changes |
| **WayLane / Node** (server pilot) | `/deploy/node/discovery` | `/docker-compose.yaml` (Raw mode) | Not applicable while manual | **Manual only**; stable Agent, inner Node and VPN Core have independent lifecycles |

The resource named **WayLane / Dependencies** contains the single PostgreSQL
service `waylane-postgres`; it is not a second application runtime.
An isolated PostgreSQL lifecycle must never be tied to frontend/backend
source pushes. Keep Dependencies and Node/Agent strictly manual-only, even
when changing their source; a Watch Path is not a replacement for that gate.
Backend and Frontend use the existing `koba-coolify-github-deploy` GitHub App.
Their **persisted Coolify Watch Paths** are precisely the two globs above,
not empty. Both Docker build contexts, Dockerfiles, lockfiles, generic and
Coolify Compose entrypoints, and the bundled Nginx configuration are inside
their own service folders. `shared/contracts/**` is documentation, not a
source dependency in these builds. A change outside these folders must
not implicitly redeploy either application. `x-watch-path-coolify` in
Compose is documentation only and does not configure Coolify.

## Watch Paths — required Coolify operator checks

These are **Git-repository-relative globs**, with no leading slash. In Coolify
open **WayLane → development → the application → General → Build & deploy →
Watch paths**, enter the exact corresponding patterns in the matrix above
(one pattern per line), and **Save changes**. Open the resource again and
verify the persisted Watch paths; a Git comment, Compose `x-*` field, GitHub
Actions path filter or successful webhook does **not** set this field.
Also confirm **Advanced → Auto deploy → Deploy on push (webhooks)** for
Backend/Frontend, **Git Source → `koba-coolify-github-deploy`** on `main`,
and **Manual deployments only** for Dependencies and Node/Agent.

Before a new or moved Docker build input is committed, review the build
context, `COPY`/`ADD`, Compose entrypoint, generated source, local service
locks, and used shared packages. **Update the corresponding persisted Coolify
Watch Paths and this table first**, then publish the source change. A missing
input can leave an outdated live service; an empty Watch Paths field instead
triggers unrelated builds. Do not add a broad repository-wide `**` glob to
work around missing paths. All currently consumed Frontend and Backend build
inputs are already under their corresponding `services/<owner>/` folder.

For acceptance, publish separate *validated* commits touching only each
service folder and confirm that **only the matching application** gets an
automatic Coolify deployment. Confirm the other application, PostgreSQL and
Node/Agent receive **no new deployment**. Check the recorded commit SHA,
health and an actual HTTP/API consumer outcome; a working GitHub Actions
build alone does not establish the Coolify webhook or filter. Changing
Coolify's Watch Paths does **not** retroactively deploy earlier commits.

If a dependency-image, database bootstrap or Agent change is required, follow
its manual upgrade/rollback and backup acceptance process. Never enable
automatic Dependencies or Agent updates as a shortcut to apply that change.

Use the same approved Git ref for the backend/frontend/dependency resources.
A new Node requires a **panel-issued** invitation or Classic token, not a
randomly fabricated registration secret. Node runs a separate Compose
resource and does not share the API database volume. The **stable Agent**
owns rootless Docker and a replaceable internal Node container, which in
turn manages its sibling VPN Cores. The outer Compose has only bootstrap
and Agent; use the standalone [Node deployment guide](node/README.md).
The tambov Node is deployed, approved by a panel-issued Discovery
invitation (now revoked), and retains identity and WSS telemetry on restart.
Its named volumes are independent. New Nodes still require an authorized,
panel-issued enrollment path. A **loopback-only Xray SOCKS5 TCP test**
now passes through the Node-managed Core; this is not public VPN service.
Do not expose or assume working public VPN TCP/UDP traffic until
external client connectivity, routing and firewall rules are accepted.

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

## Standalone subscription testing (Backend DEV only)

Backend's `WAYLANE_DEV_ALLOW_UNPAID_SUBSCRIPTIONS` is optional and defaults
`false`. To test subscription issuance without the separate VPN Core payment
integration, explicitly set it to `true` **as a Backend runtime variable** in
Coolify development. It only works with the exact internal
`DEPLOYMENT_ENVIRONMENT=development` value, and must never be enabled in
production. This suppresses the `payment_required` **admission check** for
`/sub` and explicit Node-managed Xray clients, not the other admission rules.
It neither changes stored entitlement nor weakens the Core service API-key
restriction. Turn it off to restore the existing payment gate immediately.

## Telemetry identity

Backend exports OpenTelemetry logs and traces directly to the configured
`OTEL_ENDPOINT`. The canonical resource identity is
`service.namespace=WayLane`, `service.name=backend`, and
`deployment.environment.name=development`. Source-controlled service
settings use `OTEL_NAMESPACE`, `OTEL_SERVICE_NAME`, and
`DEPLOYMENT_ENVIRONMENT`; no secret value is embedded in the image.
The runtime still recognizes the previous internal namespace/environment
keys for older deployment variants.

Frontend application diagnostics travel over same-origin HTTPS:
`browser → /api/telemetry/browser → private Backend → OTLP → SigNoz`.
The browser sends only an allowlisted event kind and a coarse route group;
it never sends raw messages, stacks, user identifiers, URLs or bearer values.
Backend authenticates each request, applies a per-admin rate limit and uses
its own protected OTLP credentials to export logs under
`service.namespace=WayLane`, `service.name=frontend`. Backend runtime logs
and traces remain under `WayLane/backend`. No collector, SDK exporter or
OTLP bearer is installed in the Frontend image.

Nginx is only the static UI server and reverse proxy; its access log is
disabled, and ordinary proxy errors stay on stderr for local diagnostics.
Nginx logs are **not** an application telemetry source. Unauthenticated
browser events are intentionally not submitted; initial login/owner
diagnostics require server-side observation until an admin authenticates.

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
