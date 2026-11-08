"""Promote Subscription.uuid to the sole relational key, preserving dependent rows.

Revision ID: c6b4d2a908f1
Revises: a1c75e8db902

Every existing row and dependent reference is translated via the original
integer-to-UUID mapping before any integer key is dropped. Node telemetry,
HWIDs, groups, reminders and the durable device-event outbox are retained.
"""

import json
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision = "c6b4d2a908f1"
down_revision = "a1c75e8db902"
branch_labels = None
depends_on = None

# table: FK name, ON DELETE policy, nullable, indexes, unique constraints, composite PK
_SUBSCRIPTION_REFERENCES = {
    "next_plans": (
        "fk_next_plans_subscription_id_subscriptions", "CASCADE", False, (), (), False,
    ),
    "node_subscription_usages": (
        "fk_node_subscription_usages_subscription_id_subscriptions", "CASCADE", False,
        (("ix_node_subscription_usages_subscription_id_created_at", ("subscription_id", "created_at")),),
        (("uq_node_subscription_usages_created_at", ("created_at", "subscription_id", "node_id")),), False,
    ),
    "notification_reminders": (
        "fk_notification_reminders_subscription_id_subscriptions", "CASCADE", False, (), (), False,
    ),
    "subscription_hwids": (
        "fk_subscription_hwids_subscription_id_subscriptions", "CASCADE", False,
        (("ix_subscription_hwids_subscription_id", ("subscription_id",)),),
        (("uq_subscription_hwids_subscription_id", ("subscription_id", "hwid")),), False,
    ),
    "subscription_updates": (
        "fk_subscription_updates_subscription_id_subscriptions", "CASCADE", False,
        (("idx_subscription_updates_subscription_id", ("subscription_id",)),
         ("idx_subscription_updates_subscription_created", ("subscription_id", "created_at"))),
        (), False,
    ),
    "subscription_usage_logs": (
        "fk_subscription_usage_logs_subscription_id_subscriptions", "CASCADE", True,
        (("ix_subscription_usage_logs_subscription_id_reset_at", ("subscription_id", "reset_at")),),
        (), False,
    ),
    "subscriptions_groups_association": (
        "fk_subscriptions_groups_association_subscription_id_subscriptions", None, False,
        (("ix_subscriptions_groups_association_groups_id_subscription_id", ("groups_id", "subscription_id")),),
        (), True,
    ),
    "subscription_device_events": (
        None, None, False, (), (), False,
    ),
}


def upgrade() -> None:
    bind = op.get_bind()
    for table, (foreign_key, ondelete, nullable, indexes, uniques, composite_pk) in _SUBSCRIPTION_REFERENCES.items():
        old_foreign_key = foreign_key
        if table == "subscriptions_groups_association" and bind.dialect.name == "postgresql":
            # PostgreSQL truncated the original naming-convention identifier.
            # Introspect its real name rather than trying to DROP a 64-byte
            # identifier (which SQLAlchemy correctly refuses to render).
            old_constraints = [
                constraint
                for constraint in sa.inspect(bind).get_foreign_keys(table)
                if constraint["referred_table"] == "subscriptions"
                and constraint["constrained_columns"] == ["subscription_id"]
            ]
            if len(old_constraints) != 1 or not old_constraints[0].get("name"):
                raise RuntimeError("Expected exactly one existing subscription/group FK")
            old_foreign_key = old_constraints[0]["name"]

        # A temporary typed column makes the conversion portable between
        # PostgreSQL UUID and SQLite's CHAR(32) UUID representation.
        op.add_column(table, sa.Column("_new_subscription_uuid", sa.Uuid(as_uuid=True), nullable=True))
        bind.execute(sa.text(
            f"UPDATE {table} SET _new_subscription_uuid = "
            f"(SELECT uuid FROM subscriptions WHERE subscriptions.id = {table}.subscription_id) "
            "WHERE subscription_id IS NOT NULL"
        ))
        missing = bind.scalar(sa.text(
            f"SELECT COUNT(*) FROM {table} "
            "WHERE subscription_id IS NOT NULL AND _new_subscription_uuid IS NULL"
        ))
        if missing:
            raise RuntimeError(f"Cannot migrate {table}: {missing} dangling subscription references")

        with op.batch_alter_table(table) as batch:
            for index_name, _ in indexes:
                batch.drop_index(index_name)
            for name, _ in uniques:
                batch.drop_constraint(name, type_="unique")
            if foreign_key:
                batch.drop_constraint(old_foreign_key, type_="foreignkey")
            if composite_pk and bind.dialect.name != "sqlite":
                # Rename of the old `users` table did not necessarily rename
                # the PK constraint in PostgreSQL. Drop its real catalog name.
                old_primary_key = sa.inspect(bind).get_pk_constraint(table)["name"]
                if not old_primary_key:
                    raise RuntimeError(f"Missing pre-UUID primary key on {table}")
                batch.drop_constraint(old_primary_key, type_="primary")
            batch.drop_column("subscription_id")
            batch.alter_column(
                "_new_subscription_uuid",
                new_column_name="subscription_id",
                existing_type=sa.Uuid(as_uuid=True),
                nullable=nullable,
            )
        # A second batch re-reflects the renamed column. Alembic's first
        # SQLite batch retains its former column *key* during index creation.
        # Creating indexes/constraints in that batch yields KeyError.
        with op.batch_alter_table(table) as batch:
            if composite_pk:
                batch.create_primary_key("pk_subscriptions_groups_association", ["subscription_id", "groups_id"])
            if foreign_key:
                # PostgreSQL limits explicitly named identifiers to 63 bytes.
                # The old FK name stays untouched for DROP, while the long
                # subscriptions/groups association gets a shorter new name.
                new_foreign_key = (
                    "fk_subscription_group_subscription_uuid"
                    if table == "subscriptions_groups_association"
                    else foreign_key
                )
                batch.create_foreign_key(
                    new_foreign_key, "subscriptions", ["subscription_id"], ["uuid"], ondelete=ondelete
                )
            for name, columns in uniques:
                batch.create_unique_constraint(name, list(columns))
            for name, columns in indexes:
                batch.create_index(name, list(columns))

    # Durable webhook events can outlive the deployment that originally queued
    # them. Rewrite *stored* payloads as well as relational keys, otherwise a
    # retry could leak the retired numeric user ID or username to a receiver.
    event_table = sa.table(
        "subscription_device_events",
        sa.column("id", sa.String()),
        sa.column("subscription_id", sa.Uuid(as_uuid=True)),
        sa.column("payload", sa.JSON()),
    )
    pending = bind.execute(
        sa.select(event_table.c.id, event_table.c.subscription_id, event_table.c.payload)
    ).mappings().all()
    for item in pending:
        payload = item["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise TypeError(f"Invalid stored device-event payload: event={item['id']}")
        sanitized = {
            key: value
            for key, value in payload.items()
            if key not in {"user_id", "username", "hwid", "subscription_id", "subscription_uuid"}
        }
        sid = item["subscription_id"]
        sanitized["subscription_uuid"] = str(sid if isinstance(sid, UUID) else UUID(str(sid)))
        bind.execute(
            sa.update(event_table).where(event_table.c.id == item["id"]).values(payload=sanitized)
        )

    # No external FK references subscriptions.id by this point. UUID is already
    # unique and non-null. Demote its redundant surrogate and promote UUID.
    old_subscription_primary_key = None
    if bind.dialect.name != "sqlite":
        old_subscription_primary_key = sa.inspect(bind).get_pk_constraint("subscriptions")["name"]
        if not old_subscription_primary_key:
            raise RuntimeError("Missing pre-UUID subscriptions primary key")

    with op.batch_alter_table("subscriptions") as batch:
        batch.drop_index("idx_subscriptions_created_at_id")
        if old_subscription_primary_key:
            batch.drop_constraint(old_subscription_primary_key, type_="primary")
        batch.drop_column("id")
        batch.create_primary_key("pk_subscriptions", ["uuid"])
        batch.create_index("idx_subscriptions_created_at_uuid", ["created_at", "uuid"])


def downgrade() -> None:
    raise RuntimeError("The UUID-only subscription identity migration cannot recreate numeric user IDs")
