# WayLane Backend — agent map

Source: https://github.com/KobaProduction/WayLane/tree/main/services/backend

Reusable engineering guidance: https://github.com/ArthurKoba/ai-agent-workflow
Read the upstream workflow AGENTS.md and load only task-relevant skills.

## Code and contracts
- `app/`: FastAPI API, persistence and node orchestration.
- `app/templates/subscription/`: current subscription HTML.
- `dashboard/__init__.py`: adapter for the separately built frontend.
- `scripts/verify.sh`: backend checks, also used by GitHub Actions.
- `UPSTREAM.md` and `LICENSING.md`: source provenance and license scope.
- Intens `vpn-core` owns commercial subscriptions and the current public gateway.
- Shared Intens contracts: `intens-cloud/infrastructure`.

Preserve API and migration compatibility by default; do not change production without authorization.
**Intens DEV breaking-contract decision:** [infrastructure #22](https://gitlab.com/intens-cloud/infrastructure/-/work_items/22)
authorizes replacing the former technical `User` with UUID-first `Subscription`.
Remove public `/api/user*`/numeric-ID routes **with their consumers and tests**, not as aliases.
**Current UUID-only identity decision:** `Subscription.uuid` is the sole persisted
subscription primary key (UUIDv4). All subscription foreign keys (`subscription_uuid`), node telemetry
and durable device events refer to UUIDs. WayLane's own Node Protobuf uses
`Subscription.subscription_uuid`; only the Xray adapter translates it to the third-party
`Account.email` technical field. Do not invent a contact email or `User` ORM model.
There is no `Subscription.id` synonym or other alternate identity: application code uses
`Subscription.uuid` and explicit `subscription_uuid` names. Admin and Node IDs remain
their own entities. Preserve data through versioned Alembic migrations; keep DEV
rollout separate from production.
VPN Core owns commercial entitlement and tariff policy; WayLane must reject
administrator or legacy changes to Core-managed group/traffic/expiry/admission
state, including indirect bulk and administrative operations.

WayLane Node Agent discovery API, migrations and IP observation registry belong to
`app/routers/node_discovery.py`. Registration must not silently mutate
legacy `nodes`, hosts, certificates or subscription endpoints.
