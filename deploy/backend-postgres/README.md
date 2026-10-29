# Backend with owned PostgreSQL and PgBouncer

A single Compose resource owns three steady-state services: backend,
PostgreSQL 17 and a private, non-root PgBouncer. No frontend and NO VPN node.
The existing backend with external PostgreSQL remains independently available
under `services/backend/docker-compose.yaml`.

Coolify resource: Base Directory `/deploy/backend-postgres`,
Compose Location `/docker-compose.coolify.yaml`. Persist repository-relative
Watch Paths `deploy/backend-postgres/**`, `deploy/compose/pgbouncer/**`,
`services/backend/**`; verify effective Compose after parser `extends`.
Approve and configure the existing three bundled PostgreSQL operator keys:
`POSTGRES_USERNAME` default `waylane`, `POSTGRES_PASSWORD`
required nonempty/secret, `POSTGRES_DATABASE` default `waylane`.
Required+default username UI semantics still require Coolify parser acceptance;
approval of secret storage scope is pending.

PgBouncer owns `database:5432` on the internal `database` network.
PostgreSQL owns `postgres:5432` on that same network. Only backend is attached
to application traffic, and its host listener is loopback-only for generic
Compose. Alembic migrations use `postgres:5432` directly, while application requests
use `database:5432` through PgBouncer. PgBouncer session pooling and
SCRAM auth use the existing PostgreSQL
login, no new key. No database or pooler host ports are exposed.

Persistent `postgres_data` and `backend_data` named volumes are tied to
the Compose project. Changing credentials/database name after initialization
does NOT mutate pre-existing PostgreSQL users, passwords or databases.
Back up durable data and test clean/repeat/upgrade/rollback separately before
production. Do not attach production volumes or run competing writers.

No Coolify app/domain/secret has been created or changed by this source.
Backend public ingress and frontend cross-resource networking require a
separate routing decision; do not assume destination selection creates network
membership.
