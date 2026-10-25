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
`21bec95874f4` revision now **continues that tip** instead of replacing the
entire migration graph with a second, incompatible baseline. Subsequent
WayLane migrations (`bd1c0d80d86f` and later) remain forward-only features.

Both an empty database and an existing database on a known PasarGuard Alembic
revision advance using the same `alembic upgrade head` history. An existing
installation keeps its users, subscription settings, roles, credentials, JWT
signing key, and node data; the WayLane bridge never bootstraps or overwrites
those rows. On PostgreSQL it converts three columns from JSON to JSONB to
align with current model types; the existing upstream server defaults are
intentionally retained. On SQLite/MySQL the bridge does not change column
storage types. The preserved `bootstrap_seed.json` is a read-only fixture for
verification; the historical migrations own actual fresh-database seeding.

**Early WayLane preview databases only:** revisions formerly named
`waylane_0001` through `waylane_0004` are no longer revision IDs in the
main Alembic graph. After a verified backup and before a normal
`alembic upgrade head`, reconcile one of these *already applied* previews
using `python -m scripts.reconcile_legacy_waylane_revision` (dry-run) and
the explicit `--apply` option. This script validates the expected
application table/column state, then updates only `alembic_version`; it
refuses all unknown revisions. It must never be used to skip migrations on
a PasarGuard database. Rehearse on a restored database first; do not apply
to production without authorization.

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


## TLS certificate presets (feature migration `bd1c0d80d86f`)

After the historical migration chain, `21bec95874f4` performs only the
compatibility bridge. Feature migrations are incremental; `bd1c0d80d86f`
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


## Client-side Xray host balancer groups

An administrator can manage named balancer groups under **Hosts → Xray
balancers** or via \`/api/host-balancers\`. A group contains **2–32 references
to existing individual hosts**, an Xray selection strategy (\`roundRobin\`,
\`random\`, \`leastPing\`, \`leastLoad\`), an optional 30–3600 second probe
interval, an enabled flag, and a flag that hides its member hosts from the
**Xray JSON** subscription only. These groups do not create listening
servers, forwarders or new node configuration.

Each eligible member is resolved through the existing user authorization,
host-status and per-user outbound generator. Its own TLS, transport, protocol
and credentials are retained. One additional Xray client profile is emitted
with unique outbound/dialer tags, a matching \`routing.balancers\` selector and
a routing rule. \`leastPing\` includes \`observatory\`, while \`leastLoad\`
includes \`burstObservatory\`, probing a fixed public 204 endpoint. Regular
hosts stay visible in non-Xray subscriptions, since those formats cannot
carry this Xray-specific JSON structure.

**Balancing versus failover:** `roundRobin` and `random` distribute new
connections but do **not** probe or automatically exclude dead hosts. When one
member fails, a portion of connections will fail until it comes back.
For automatic failover use `leastPing` (background `observatory`), which
switches on successful updated probe results rather than retrying a failed
connection mid-stream. `leastLoad` uses burst sampling and may require one
to two full probe cycles (each about `5 * probe_interval`) to detect a dead
member; it favors stability over quick failover. Probes go to the fixed
public 204 endpoint through each member, so client network restrictions on
that endpoint may prevent healthy status observations. The actual failover
rate and latency depend on the Xray client version and configured interval.

Local integration verification with Xray 26.9.30, two disposable VLESS
servers and real SOCKS/HTTP traffic confirmed healthy routing, that plain
round-robin continues selecting an offline host, `leastPing` switching from
its active offline member, and `leastLoad` switching after its probe cycle.
The local test endpoint used an explicit, narrow `freedom.finalRules` allow
because current Xray blocks access to loopback/private destinations from
VLESS by default; this test-only exception is **not** installed in the
WayLane server configuration.

**Limits:** nested balancers and cross-format balancers are deliberately not
supported. All group members must be real hosts, not other groups. Hosts with
no authorized inbound or no active user protocol are skipped; a group with
fewer than two valid members is not emitted and does not hide single-host
entries. Host deletion refuses to remove a referenced member until the
group is edited or removed. Background reachability probing runs on the
client under Xray, not in the WayLane backend. Client software importing
only subscription URLs or per-outbound links cannot express a balancer;
test the actual Xray JSON import pathway and client core support.

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

## Embedded Subscription Firewall and device webhooks

The old Intens Subscription Firewall proxied PasarGuard subscription responses,
converted certain errors to Base64-encoded informational Shadowsocks links and
verified client device access via a Telegram-bot check endpoint. WayLane embeds
this behavior in the panel without a reverse proxy or second device database.

The message designer lives under Settings > Subscriptions > Subscription
Firewall. Admins can edit the headings and text lines of seven failure
scenarios and preview both the encoded Base64 body and announce header.
Conversion is disabled by default; when enabled it is restricted to
Happ/v2RayTun/ClientHub link clients with X-HWID. HTML and other client
formats keep their original HTTP status codes. HWID count limits are
never bypassed. The optional legacy device-check URL receives
(subscription, ip, hwid) JSON and understands ACCESS_GRANTED, ACCESS_DENIED,
LIMIT_DEVICES and BOT_ERROR. On timeout, WayLane falls back to its
existing local HWID policy; explicit deny/limit decisions block access.

The device event outbox records device.added, device.updated, device.removed and device.reset
in the same database transaction as the HWID change, and then attempts
delivery without blocking subscription requests. Events survive delivery
failures and can be retried explicitly via the admin API. Recipient
idempotency uses the event ID.

To enable outgoing events set WAYLANE_DEVICE_WEBHOOK_SECRET to a protected,
random secret of at least 32 characters, then configure a HTTPS URL in the
designer. HTTP/local targets are only permitted during isolated local tests
with WAYLANE_WEBHOOK_ALLOW_LOCAL_TEST=1. For a legacy internal-only
service, operators may explicitly allowlist its exact host through
WAYLANE_WEBHOOK_ALLOWED_INTERNAL_HOSTS (comma separated), for example a
private bridge address. This is an explicit trust boundary, not a public
webhook default. Events are recorded only while webhook delivery is enabled.
The receiver verifies
X-WayLane-Signature (HMAC-SHA256 over the UTF-8 bytes of
X-WayLane-Timestamp + "." + request body); reject old timestamps and
deduplicate X-WayLane-Event-ID. No redirects are followed.

A repeatable integration test `tests/test_device_webhook_receiver_integration.py`
starts an isolated real Python HTTP server implemented in
`tests/support/device_webhook_receiver.py`, binds only to localhost and
generates an ephemeral signing secret. It verifies the real add/update/remove/
reset HWID path, HMAC-SHA256 signatures, 5-minute timestamp freshness, invalid
and tampered requests (401/400), explicit HTTP 503 recovery and idempotent
duplicate event IDs. The test server keeps deduplication IDs **only in memory**:
production webhook receivers must persist them across restarts. This is a test
receiver, never a production bot implementation. Its listener is shut down at
the end of the test; it does not keep running or perform background monitoring.

The forward-only Alembic revision is b9af0263d1e8, continuing the restored
PasarGuard history. Do not remove the separate legacy firewall or redirect
production subscribers until the actual receiver and external clients have
been validated in an authorized staging cutover.

### Automatic retry and visibility for device event webhooks

Device events are persisted in subscription_device_events before delivery.
The initial HTTP delivery is best-effort after the HWID transaction commits.
WayLane's existing scheduler-role APScheduler job (retry_device_webhooks)
runs every 30 seconds and processes at most 25 due events per pass,
with up to four concurrent HTTP requests.
API workers do not need another monitor or separate firewall service.

Failed deliveries retain status=failed, exact attempt count, sanitized
last_error and next_attempt_at with exponential backoff: 30, 60, 120,
240 seconds and so on, capped at one hour. When the recipient recovers,
the next due scheduler pass resends the original event ID. Receivers must
durably deduplicate IDs, since an HTTP acknowledgment can be lost.

A sending event carries an attempt-specific lease expiring in 15 seconds.
After a process crash, another scheduler pass can reclaim that lease.
Atomic DB fencing prevents the stale worker from overwriting the new
result. Delivery is at-least-once, not exactly-once.

After 10 attempts, an event receives status=exhausted and is not silently
discarded. Admins can inspect event status, sanitized failure and next
retry time under Settings > Subscriptions > Subscription Firewall, or
GET /api/subscription-firewall/device-events.
GET /api/subscription-firewall/device-events/summary provides counts.
POST /api/subscription-firewall/retry-device-events is the manual
one-shot replay for pending/failed events, bypassing the delay but never
the 10-attempt limit. Only an explicit admin action
POST /api/subscription-firewall/device-events/{event_id}/requeue
can reset an exhausted event after investigating the recipient.
Do not put webhook credentials or raw device payloads in logs.

Automated retry is inactive when webhook_enabled is false. Previously
queued events remain durable until delivery is enabled again.
Events that were never recorded because the panel itself was offline
cannot be recovered from the outbox; that requires device-state
reconciliation with the authoritative database/source.

The forward Alembic migration c7e90b84a126 adds next_attempt_at and
the due-events index without rewriting the PasarGuard migration chain.
Legacy failed rows with NULL timestamps become immediately due;
previously exhausted 10-attempt failures remain available for manual
requeue.

## Reusable Xray core presets: REALITY, VLESS Encryption and routing

The Cores page exposes a reusable preset editor for Xray core configs:

- REALITY preset: validated x25519 private key and derived public key, shortIds,
  optional ML-DSA65 seed/verify. The private key and seed are encrypted with
  the deployment's existing WAYLANE_TLS_SECRET_KEY (Fernet); API/list responses
  return only public metadata and IDs.
- VLESS Encryption preset: the paired Xray vlessenc server decryption and
  client encryption fields. Only the server decryption is encrypted at rest;
  the client encryption string is used for subscription generation. VMess
  does not have the same server/client vlessenc key-pair contract.
- Routing preset: JSON routing rules, balancers and strategy, separate from
  core-specific outbound definitions. A binding verifies that named outbound
  tags exist before saving the shared routing template.

Core bindings target one existing core/inbound tag (or __routing__ for
whole-core routing). Applying a binding replaces inline secret fields in
core_configs.config with a preset reference plus public metadata. Xray core
manager and public REST endpoints never require the managed REALITY private
key or server-side VLESS decryption value; they remain inside the encrypted
presets table. Only at the node Start RPC boundary are encrypted keys
materialized into an ephemeral Xray JSON payload. As with managed TLS
certificates, DO NOT log or retain that payload.

Preset rotation updates bound cores' public projections, but deliberately
does not restart live nodes. After reviewing the impact, use an explicit
core/node restart action to deploy changed server credentials. Key rotation
can break clients if they have not yet refreshed their subscriptions: schedule
a coordinated rollout. Referenced presets cannot be deleted while bound.
Changing a managed core to a different backend is blocked until bindings
are deliberately removed/migrated. The first version supports replacing
bindings, not removing the managed references independently of their cores.

Migration a4b9d8e31f20 extends the restored PasarGuard/WayLane Alembic
chain. No automatic changes are applied to existing core configurations:
they retain their previous inline settings until an administrator explicitly
binds a preset. New secrets require WAYLANE_TLS_SECRET_KEY on every WayLane
backend process that materializes Xray configs.

## Read-only business statistics (PasarGuard issue #582)

WayLane offers an owner-only business analytics dashboard with four primary
views: General, Nodes and speeds, User traffic, Devices. The specification
originates from https://github.com/PasarGuard/panel/issues/582, adapted to the
requirement that no existing database schema or telemetry collector may change.
CPU, RAM and disk panels are not duplicated: Grafana owns resource monitoring.
Existing operational metrics and exporters remain unchanged.

Backend routes under /api/business-analytics read existing tables only:
- /catalog — node and user names/status for filters.
- /matrix — per-user/per-node total traffic from node_user_usages; filter
  by user, node, status, time range or lifetime, and exclude bridge IDs.
  It supports node to users and user to nodes; limit 1,500 relationships.
- /traffic-series — when a user or node is selected, a time-series for the
  12 highest-traffic related nodes/users, using existing 10-minute aggregate
  user-node history samples (total bytes only).
- /nodes — hourly/daily directional node_usages samples. When bridge users
  are excluded, totals derive from node_user_usages and directional values
  are explicitly unavailable; limit 8,000 buckets.
- /devices — current HWID systems, models, OS versions and last-used time,
  plus User-Agent counts of subscription refreshes in a selected period.
- For current aggregate and per-node speeds, the UI calls the existing
  /api/nodes/realtime_stats endpoint and sums current inbound/outbound
  bytes/second into Mbps. The UI refreshes only while the page is open
  and visible, every 15 seconds. No new collectors or jobs are installed.

Period filters offer 24 hours, 7/30/90 days, custom timezone-aware bounds
and lifetime totals for the user/node matrix. Historical time series have
a maximum span of 366 days; the all-time selection on the graph falls back
to the most recent 90 days. Node colors and temporary bridge/node exclusions
live only in the administrator browser's local storage, never the database.
Access is owner-only because user names/HWID details span admin scopes.

Source limitations: node_user_usages retains only aggregate used_traffic per
user/node/time bucket and cannot yield accurate per-user upload/download.
These aggregates must not be confused with directional node_usages or live
network speed. No retention setting, migration, data write, polling job, or
telemetry collection change is involved. Deleted users/nodes may be missing
from the historical joins due to existing cascades. The issue's future
inbound-to-outbound path tracing is not implemented in this read-only scope.
