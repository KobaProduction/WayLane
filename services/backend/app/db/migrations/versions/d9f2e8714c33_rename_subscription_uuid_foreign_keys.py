"""Make subscription UUID explicit in every relational reference and device-event outbox.

Revision ID: d9f2e8714c33
Revises: c6b4d2a908f1

No ID translation or regeneration happens here: all affected columns already
contain canonical UUIDv4 values. Rename in place while retaining the foreign
keys, unique constraints, indexes, and the NATS-independent durable outbox.
"""

import sqlalchemy as sa
from alembic import op

revision = "d9f2e8714c33"
down_revision = "c6b4d2a908f1"
branch_labels = None
depends_on = None

# Explicit names respect PostgreSQL's 63-byte limit. None means the device
# outbox deliberately keeps events after their subscription has been removed.
_REFERENCE_FKS = {
    "next_plans": ("fk_next_plans_subscription_uuid_subscriptions", "CASCADE"),
    "node_subscription_usages": ("fk_node_subscription_usages_uuid_subscriptions", "CASCADE"),
    "notification_reminders": ("fk_notification_reminders_subscription_uuid_subscriptions", "CASCADE"),
    "subscription_hwids": ("fk_subscription_hwids_subscription_uuid_subscriptions", "CASCADE"),
    "subscription_updates": ("fk_subscription_updates_subscription_uuid_subscriptions", "CASCADE"),
    "subscription_usage_logs": ("fk_subscription_usage_logs_subscription_uuid_subscriptions", "CASCADE"),
    "subscriptions_groups_association": ("fk_subscription_group_uuid", None),
    "subscription_device_events": (None, None),
}

_INDEX_NAMES = {
    "node_subscription_usages": [
        ("ix_node_subscription_usages_subscription_id_created_at", "ix_node_subscription_usages_subscription_uuid_created_at", ["subscription_uuid", "created_at"]),
    ],
    "subscription_hwids": [
        ("ix_subscription_hwids_subscription_id", "ix_subscription_hwids_subscription_uuid", ["subscription_uuid"]),
    ],
    "subscription_updates": [
        ("idx_subscription_updates_subscription_id", "idx_subscription_updates_subscription_uuid", ["subscription_uuid"]),
    ],
    "subscription_usage_logs": [
        ("ix_subscription_usage_logs_subscription_id_reset_at", "ix_subscription_usage_logs_subscription_uuid_reset_at", ["subscription_uuid", "reset_at"]),
    ],
    "subscriptions_groups_association": [
        ("ix_subscriptions_groups_association_groups_id_subscription_id", "ix_subscriptions_groups_association_groups_id_subscription_uuid", ["groups_id", "subscription_uuid"]),
    ],
}


def upgrade() -> None:
    bind = op.get_bind()
    for table, (fk_name, ondelete) in _REFERENCE_FKS.items():
        # Column rename is transactional and leaves the UUID bytes unchanged.
        # With SQLite batch mode, the table is rebuilt together with its PK,
        # foreign keys and indexes, so constraints are re-reflected afterward.
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                "subscription_id", new_column_name="subscription_uuid",
                existing_type=sa.Uuid(as_uuid=True),
                existing_nullable=table == "subscription_usage_logs",
            )

        if fk_name:
            candidates = [
                fk for fk in sa.inspect(bind).get_foreign_keys(table)
                if fk["referred_table"] == "subscriptions"
                and fk["constrained_columns"] == ["subscription_uuid"]
            ]
            if len(candidates) != 1 or not candidates[0]["name"]:
                raise RuntimeError(f"Expected exactly one UUID subscription FK on {table}")
            with op.batch_alter_table(table) as batch:
                batch.drop_constraint(candidates[0]["name"], type_="foreignkey")
                batch.create_foreign_key(
                    fk_name, "subscriptions", ["subscription_uuid"], ["uuid"], ondelete=ondelete
                )

        for old_name, new_name, columns in _INDEX_NAMES.get(table, ()):
            # Never regenerate a UUID. Rename the index only; this must work
            # both on SQLite and PostgreSQL and preserve all existing rows.
            with op.batch_alter_table(table) as batch:
                batch.drop_index(old_name)
                batch.create_index(new_name, columns)

    with op.batch_alter_table("subscription_hwids") as batch:
        batch.drop_constraint("uq_subscription_hwids_subscription_id", type_="unique")
        batch.create_unique_constraint("uq_subscription_hwids_subscription_uuid", ["subscription_uuid", "hwid"])


def downgrade() -> None:
    raise RuntimeError("UUID-only subscription naming migration is intentionally irreversible")
