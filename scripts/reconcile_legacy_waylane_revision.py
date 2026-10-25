"""One-time revision-ID reconciliation for databases stamped by early WayLane.

The historical PasarGuard chain already uses standard 12-hex Alembic IDs.
Early WayLane preview builds incorrectly stamped waylane_0001..0004. Those
builds are not part of the new Alembic graph. This explicit, fail-closed tool
maps only a known preview stamp to the equivalent published schema version.

Dry run by default. This never creates/drops or changes application tables.
"""

import argparse
import asyncio
from dataclasses import dataclass

from sqlalchemy import inspect, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine

from config import database_settings

_REVISION_MAP = {
    "waylane_0001": ("21bec95874f4", 1),
    "waylane_0002": ("bd1c0d80d86f", 2),
    "waylane_0003": ("985f97eefe3d", 3),
    "waylane_0004": ("9266245ebd3d", 4),
}
_JSONB_COLUMNS = (
    ("admin_roles", "hwid"),
    ("admins", "custom_variables"),
    ("api_keys", "permissions"),
)


class RevisionReconciliationError(RuntimeError):
    """Preview WayLane revision cannot be safely mapped to a known schema."""


@dataclass(frozen=True)
class RevisionMapping:
    old_revision: str
    new_revision: str
    applied: bool


def _validate_schema(connection, stage: int) -> None:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    required = {"users", "settings", "jwt", "admin_roles", "admins", "api_keys"}
    if stage >= 2:
        required.update(("tls_presets", "tls_bindings"))
    if stage >= 3:
        required.update(("acme_orders", "acme_dns_accounts"))
    missing = required - tables
    if missing:
        raise RevisionReconciliationError(f"Missing expected tables: {', '.join(sorted(missing))}")
    if stage == 1 and ({"tls_presets", "tls_bindings", "acme_orders"} & tables):
        raise RevisionReconciliationError("Schema has later feature tables than the recorded revision")
    if stage == 2 and {"acme_dns_accounts", "acme_orders"} & tables:
        raise RevisionReconciliationError("Schema has later ACME tables than the recorded revision")
    if stage >= 3:
        columns = {column["name"] for column in inspector.get_columns("acme_orders")}
        if (stage >= 4) != ("lease_token" in columns):
            raise RevisionReconciliationError("ACME fencing-column shape does not match the recorded revision")
    if connection.dialect.name == "postgresql":
        for table, column in _JSONB_COLUMNS:
            columns = {item["name"]: item for item in inspector.get_columns(table)}
            if column not in columns or not isinstance(columns[column]["type"], JSONB):
                raise RevisionReconciliationError(f"Expected PostgreSQL JSONB column {table}.{column}")


async def reconcile_revision(database_url: str, *, apply: bool = False) -> RevisionMapping:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            # Row lock prevents two reconcilers racing on a PostgreSQL database.
            statement = "SELECT version_num FROM alembic_version"
            if connection.dialect.name == "postgresql":
                statement += " FOR UPDATE"
            revisions = (await connection.execute(text(statement))).scalars().all()
            if len(revisions) != 1 or revisions[0] not in _REVISION_MAP:
                raise RevisionReconciliationError(
                    "Expected exactly one known legacy WayLane revision; no database changes made"
                )
            old_revision = revisions[0]
            new_revision, stage = _REVISION_MAP[old_revision]
            await connection.run_sync(_validate_schema, stage)
            if apply:
                updated = await connection.execute(
                    text("UPDATE alembic_version SET version_num = :new WHERE version_num = :old"),
                    {"new": new_revision, "old": old_revision},
                )
                if updated.rowcount != 1:
                    raise RevisionReconciliationError("Alembic revision changed concurrently")
            return RevisionMapping(old_revision, new_revision, apply)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform the guarded revision-only update (default: dry-run)",
    )
    args = parser.parse_args()
    outcome = asyncio.run(reconcile_revision(database_settings.url, apply=args.apply))
    action = "updated" if outcome.applied else "would update"
    print(f"{action} Alembic revision {outcome.old_revision} -> {outcome.new_revision}; no schema/data changes")


if __name__ == "__main__":
    main()
