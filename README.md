# WayLane Backend

Repository owner: **Koba Production**; original integration: Intens. Python FastAPI control-plane backend derived from PasarGuard Panel.

This project is **not** production-ready as WayLane; its current behavior remains PasarGuard-compatible.
The existing API, node orchestration and integrated HTML subscription page are preserved.

The dashboard frontend source is deliberately split into **waylane-frontend**.
Until the Vue rewrite, build its existing React UI as a temporary compatibility
artifact and copy the compiled build into `dashboard/build/` using
`./scripts/install_frontend.sh <path-to-frontend-build>`.
After obtaining a verified `waylane-frontend` build artifact, use `./scripts/install_frontend.sh /path/to/frontend/build`. A second source checkout is not required.
The `dashboard/__init__.py` integration remains to serve the compiled files.
The API Dockerfile **must not** attempt to compile the frontend during container startup.
The new Vue admin dashboard and subscription view will replace the temporary
React UI and built-in HTML page in separate migration steps.

The real public Intens Subscription Gateway remains in vpn-core.
Do not migrate tokens, clients or production nodes as part of this bootstrap.
Build and deployment are not validated until runtime/container tests are performed.
See UPSTREAM.md for copyright and AGPL-3.0 obligations.


## AI workflow and verification

Read `AGENTS.md` and the task-specific rules from
[AI Agent Workflow](https://github.com/ArthurKoba/ai-agent-workflow).
On a new checkout enable the tracked hooks with
`git config --local core.hooksPath .githooks`; run `./scripts/verify.sh`
before attempting a push. CI runs the same repository-specific gate.

**Bootstrap limitation:** Passing these checks does not prove runtime
deployment compatibility, or complete the eventual Vue/HA migration.

Licensing note: GitHub root MIT `LICENSE` does not replace the upstream copyleft license applicable to derived code. See `LICENSING.md` and `LICENSE.UPSTREAM`.


## Database migrations — existing PasarGuard databases are supported

The original **160 Alembic revisions** from PasarGuard Panel (pinned in
`UPSTREAM.md`) are retained unchanged. The historical root is
`94a5cc12c0d6` and the historical tip is `c8d2e4f6a1b3`. The WayLane
`waylane_0001` revision now **continues that tip** instead of replacing the
entire migration graph with a second, incompatible baseline. Subsequent
WayLane migrations (`waylane_0002` and later) remain forward-only features.

Both an empty database and an existing database on a known PasarGuard Alembic
revision advance using the same `alembic upgrade head` history. An existing
installation keeps its users, subscription settings, roles, credentials, JWT
signing key, and node data; the WayLane bridge never bootstraps or overwrites
those rows. On PostgreSQL it converts three columns from JSON to JSONB to
align with current model types; the existing upstream server defaults are
intentionally retained. On SQLite/MySQL the bridge does not change column
storage types. The preserved `bootstrap_seed.json` is a read-only fixture for
verification; the historical migrations own actual fresh-database seeding.

**Before migrating a production PasarGuard database**, capture a consistent
backup, verify its current `alembic_version` is on this exact ancestry, and
rehearse a full upgrade on a restored nonproduction clone. JSON-to-JSONB
conversion obtains PostgreSQL DDL locks and normalizes JSON representation;
validate stored settings, device keys, nodes, users, custom extensions, and
application compatibility before planning a maintenance window. Never stamp
an unknown revision or run migrations against Intens production without
explicit deployment authorization. The restored historical files are
protected by `app/db/migrations/legacy_history_sha256.json` and the verify
script, so future modifications cannot silently rewrite migration history.


## First local development owner (CLI)

After starting the isolated development PostgreSQL on `127.0.0.1:15432`
and migrating `waylane_dev`, initialize the **first** administrator using
`python waylane-cli.py init-owner` with the same local environment as the API.
The command reuses the application's regular admin validation and password
hashing, requires an empty admin table and the seeded owner role, and refuses
non-local or non-development databases. It creates a high-entropy password
and writes it to `.runtime/dev-owner-credentials.json` with mode 0600,
without printing credentials. The entire `.runtime/` directory is ignored
by Git. It refuses repeated
bootstrap or overwriting existing credentials. Sign in through
`POST /api/admin/token` with the locally stored username and password.
For normal managed environments use the existing one-time setup-key flow.


## TLS certificate presets (feature migration `waylane_0002`)

After the historical migration chain, `waylane_0001` performs only the
compatibility bridge. Feature migrations are incremental; `waylane_0002`
adds TLS presets and per-node inbound bindings. Apply with `alembic upgrade head`.

Configure a stable **Fernet encryption key** as `WAYLANE_TLS_SECRET_KEY`
before adding presets. Protect and back up this secret separately from the DB:
losing it makes stored TLS private keys undecryptable. Only the public
certificate and metadata are returned by the admin APIs. A webhook token can
be generated per preset (shown once); its SHA-256 digest is stored in DB, and
the token is accepted solely for that preset's certificate rotation endpoint.

API: `/api/tls-presets` and `/api/tls-presets/targets`. Presets bind to an
existing TLS-enabled Xray inbound on an explicitly selected node. The private
key is decrypted into the ephemeral node-facing `Start` request, never the
persistent core JSON. Certificate rotation force-applies only to bound nodes;
per-node outcomes are reported. External ACME/ACM provisioning is not yet part
of this feature.


## Automatic ACME issuance — OpenFlare-inspired DNS-01

The WayLane control plane follows the design of
[OpenFlare](https://github.com/Rain-kl/OpenFlare) (Apache-2.0):
encrypted DNS credentials, a managed ACME account/order lifecycle,
Cloudflare DNS-01, renewal due seven days before expiry and certificate
deployment to bound nodes. No OpenFlare implementation source was copied.

Install **lego v5** (https://go-acme.github.io/lego/) on the control-plane
host and set `WAYLANE_LEGO_BIN` to the trusted absolute executable path,
`WAYLANE_ACME_WORK_DIR` to a private 0700 filesystem directory, and
`WAYLANE_TLS_SECRET_KEY` to the same backed-up Fernet key used by TLS
presets. Only explicitly selected CA endpoints are accepted:
`letsencrypt-staging` (default) or `letsencrypt` (production).
A Cloudflare token needs Zone/DNS:Edit and Zone/Zone:Read, ideally scoped
to one DNS zone. The token is Fernet-encrypted in PostgreSQL.
Lego account and certificate files exist in private temporary directories
only while issuing; their persisted archive is encrypted.

In the TLS Presets admin section create a DNS account, issue an ACME
certificate for one or more DNS names (wildcards require DNS-01), then
attach the resulting TLS preset to chosen Xray node inbounds.
The issuance endpoint enqueues an application background task and returns
immediately; check `/api/acme/orders` to see persistent state.
An error never overwrites a previously valid TLS certificate.

For automatic renewal **schedule** `python waylane-cli.py acme-renew-due`
as a one-shot task (e.g. daily) in the deployment's authorized scheduler;
it does not create a process monitor or install a system service.
Manual admin alternative: `POST /api/acme/renew-due`.
An order with auto-renew enabled becomes due seven days before its expiry.
The same one-shot command recovers abandoned initial `pending` orders (after a
one-minute grace period) and `running` attempts whose lease has expired, even
if future automatic renewal was disabled. Failed auto-renew orders retry after
one hour. Each issuance attempt has a unique persisted fencing token: an old
worker cannot overwrite a certificate after another worker takes over its
expired lease. The scanner processes at most 100 orders per invocation;
subsequent invocations handle remaining eligible orders. For reliable recovery,
configure the external scheduler deliberately; no scheduler is installed by
the application itself.

The control plane only deploys successfully validated certificates to the
already explicitly bound nodes. The current order status indicates issuance;
node-level apply failures must be checked separately using TLS bindings and
node status. Staging certificates are **not publicly trusted**; obtain a
production certificate before real client traffic.

Limits: initial implementation supports Cloudflare DNS-01 only and lego v5,
not HTTP-01, EAB, DNS providers beyond Cloudflare, or an always-on scheduler.
