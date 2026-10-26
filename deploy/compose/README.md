# WayLane deployment variants (source files, not deployed)

The monorepo supports a bundled management stack and independently
deployable API/UI. A VPN node is never part of this management stack;
enroll and deploy nodes independently. Current node NET_ADMIN and host
network assumptions require separate least-privilege hardening.
No existing data volumes, credentials, Coolify apps or domains are touched
by committing these manifests.

## Bundled Compose (repository root)

* docker-compose.yaml — PostgreSQL 17, private PgBouncer (SCRAM/session mode),
  backend FastAPI and frontend Nginx. VPN nodes never run in this stack.
* docker-compose.coolify.yaml — extends ordinary Compose, declares frontend
  `SERVICE_FQDN_FRONTEND_8080: /` so Coolify creates the public domain, and
  repeats variable discovery and top-level networks/volumes. Actual domain
  generation requires fresh-parser acceptance in the deployed Coolify version.

The backend's `POSTGRES_HOST=database` is an **internal literal** resolving to
PgBouncer's private service-DNS alias at port 5432. PostgreSQL remains reachable
internally as `postgres:5432` for approved direct administration/migrations.
PgBouncer uses `session` pool mode, SCRAM auth and the SAME approved credentials,
not extra passwords; it writes its auth file with 0600 permissions in a
non-root container. Neither PostgreSQL nor PgBouncer publishes any host ports.
The bootstrap/migrations currently run through PgBouncer session pooling;
different pool modes require a separate compatibility review.

A local, untracked .env file supplies the approved All-in-One operator
variables: WAYLANE_POSTGRES_USERNAME (default waylane, editable),
WAYLANE_POSTGRES_PASSWORD (required nonempty manual input; no generated
password), and WAYLANE_POSTGRES_DATABASE (optional, default waylane).
Compose translates these inputs to the official PostgreSQL container's
POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB variables; the backend receives
the exact same resolved values. The backend constructs its internal
SQLALCHEMY_DATABASE_URL using SQLAlchemy escaping, rather than unsafe
unencoded password interpolation in YAML.

Run locally from the repository root:

    cp .env.example .env
    # Edit .env to set WAYLANE_POSTGRES_PASSWORD (and optional user/database).
    docker compose -f docker-compose.yaml config --quiet
    docker compose -f docker-compose.yaml up -d --build

Access http://127.0.0.1:8080/. The bundled nginx proxy forwards /api/ and
/sub/ to the private backend, ensuring same-origin auth and telemetry.
Only loopback 8080 is published; backend/database remain private. WayLane's TLS-free startup binds to the private Docker network alias waylane-api (not 0.0.0.0), allowing Nginx to reach it without exposing the API to public host interfaces.
For public use, add an approved HTTPS edge proxy: do not expose plaintext
admin UI to the Internet. The Coolify adapter declares the managed
`SERVICE_FQDN_FRONTEND_8080` route for the frontend only; verify generated
HTTPS routing to port 8080 against the actual Coolify parser before public use.
Backend/PostgreSQL remain private and are not assigned managed public domains.
Subscription URL paths carry bearer-like tokens. Bundled Nginx suppresses
`/sub/` access logs and backend access logs redact the subscription token and
exclude query strings; external ingress logs must be sanitized separately.

Coolify: Base Directory /, Compose Location /docker-compose.coolify.yaml.
Watch Paths include both service directories and the root/deploy Compose
sources (including `deploy/compose/pgbouncer/**`). A change to backend or frontend
rebuilds this one resource. Persist these Watch Paths in the Coolify UI/API;
`x-watch-path-coolify` is not an automatic registration mechanism.

The DB volume is persistent. Changing WAYLANE_POSTGRES_USERNAME,
WAYLANE_POSTGRES_PASSWORD or WAYLANE_POSTGRES_DATABASE on an existing volume
does NOT migrate PostgreSQL users, passwords or databases. Never use
re-deployment as a credential-rotation shortcut: back up and migrate explicitly.

## Split backend — owned PostgreSQL and PgBouncer

Use `deploy/backend-postgres/docker-compose.yaml` for ONE dedicated
backend dependency stack (three containers: PostgreSQL, PgBouncer, backend).
Coolify Base Directory `/deploy/backend-postgres` and Compose Location
`/docker-compose.coolify.yaml`. Persist Watch Paths
`deploy/backend-postgres/**`, `deploy/compose/pgbouncer/**` and
`services/backend/**` in the resource. Shared PostgreSQL credentials use
exactly the three approved `WAYLANE_POSTGRES_*` inputs: no second
PgBouncer identity, no frontend, and no node.

The externally accessible backend listener in plain Compose is loopback
port 8000. On another host/resource, backend-to-frontend communication
requires an explicitly approved network/HTTPS ingress route; the selected
Coolify Destination label alone does not attach private networks.
Never run this stack and an existing writable PostgreSQL volume concurrently.
No production database/volume migration is authorized by these manifests.

## Split backend — external PostgreSQL (services/backend)

* docker-compose.yaml — FastAPI only, external PostgreSQL and local port 8000.
* docker-compose.coolify.yaml — Coolify extends base with backend-only watches.

One Required existing backend variable SQLALCHEMY_DATABASE_URL contains the
external DB hostname, DB username, percent-encoded password and database
name. This is one canonical connection contract, not a redundant set of
POSTGRES_USER/PASSWORD plus DSN. Example shape ONLY, not a real secret:

    postgresql+asyncpg://USER:PERCENT_ENCODED_PASS@db.internal:5432/DATABASE

The external database owner supplies the actual DB name; the bundled DB
default does not silently override an independent database. Place the DSN
in an approved protected secret-provider scope (exact scope pending agreement).
For local plain Compose use an untracked environment file at
services/backend/.env.

Coolify Base Directory /services/backend, Compose Location
/docker-compose.coolify.yaml; Watch Paths services/backend/** and
shared/contracts/backend/** (if consumed). Network access to the external DB
and ingress must be explicitly verified; the Compose does not create them.

## Split frontend (services/frontend)

* docker-compose.yaml — standalone Nginx UI, binds 127.0.0.1:8081.
* docker-compose.coolify.yaml — frontend-only watches, build-time API base.

The existing build argument VITE_BASE_API defaults to /. The Coolify adapter
declares `SERVICE_FQDN_FRONTEND_8080: /`, allowing Coolify to generate the
frontend domain without an operator-entered FQDN. The domain routes to the
static frontend only; it does NOT automatically link an independently deployed
backend. A separately owned API load balancer or HTTPS ingress must forward
/api/ and /sub/ to backend while / goes to frontend. This enables one immutable frontend image across
environments. Separate-domain API hosting requires a separate frontend
build with an explicit HTTPS VITE_BASE_API and exact backend CORS allowlist.
Coolify Base Directory /services/frontend, Compose Location
/docker-compose.coolify.yaml; Watch Paths services/frontend/** and
shared/contracts/frontend/** (if consumed).

## Operator contract and pending decisions

**Approved for the bundled PostgreSQL deployment only:**

| Operator key | Owner | Desired Coolify Required | Default | Value |
| --- | --- | --- | --- | --- |
| WAYLANE_POSTGRES_USERNAME | bundled PostgreSQL + backend | yes | waylane | editable user name |
| WAYLANE_POSTGRES_PASSWORD | bundled PostgreSQL + backend | yes | unset | manually supplied secret |
| WAYLANE_POSTGRES_DATABASE | bundled PostgreSQL + backend | no | waylane | editable DB name |

The operator names are distinct from the official image environment inputs.
The username Required+default combination is the **approved operator UX**,
but Compose `:-waylane` alone does not guarantee a Coolify Required flag:
verify the exact generated parser state before deploying, and record a
version-specific incompatibility if Coolify cannot express both simultaneously.
Secret-manager scope and reference syntax have **not** been approved.

**Not yet approved:** the separate backend operator name
`WAYLANE_POSTGRES_URL` is approved, but its Required/default/secret-scope
contract and Compose implementation are not. The current split backend
continues using the internal `SQLALCHEMY_DATABASE_URL` input. Likewise
`VITE_BASE_API` and the existing OTLP/OTEL runtime inputs remain as currently
implemented; proposals to rename/expose them are not approvals.
Do not invent extra operator aliases simply to enumerate every available
backend/node runtime setting. Their ownership and necessity require review.

When enabled, OTLP_ENDPOINT and OTLP_BEARER_TOKEN must be set together for
direct export. These Compose defaults are NOT proof of correct Coolify
Required/default flags, secret visibility, successful Shared resolution,
network membership, or generated domains. `x-watch-path-coolify` documents
intended repository-relative scope, but Coolify Watch Paths must be **saved
and verified on each actual deployment resource**.

No production or DEV runtime activation has been authorized. Git source,
Coolify parser, container runtime and consumer-facing HTTPS/auth/telemetry
acceptance are separate gates. Actual secrets must not be revealed to agents
merely to validate their presence.

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
