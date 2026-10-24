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


## Database baseline (fresh databases only)

WayLane has a single initial Alembic migration,
`app/db/migrations/versions/waylane_0001_waylane_initial_schema.py`,
plus a frozen non-secret `bootstrap_seed.json` fixture. It creates the current
schema and seeds the initial roles, settings, core configuration, subscription
templates and a **new random** JWT signing key.

Validated using PostgreSQL 15 (`upgrade → downgrade → upgrade`, plus
`alembic check`) and a fresh SQLite database. **Breaking change:** existing
databases stamped with historic PasarGuard revisions cannot use a normal
`alembic upgrade head`; migrate or explicitly reset those databases under a
separate reviewed plan. Never reset Intens production to adopt this baseline.


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

The initial schema remains one `waylane_0001` baseline. New features have
incremental forward migrations; `waylane_0002` adds TLS presets and per-node
inbound bindings. Apply with `alembic upgrade head`.

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
