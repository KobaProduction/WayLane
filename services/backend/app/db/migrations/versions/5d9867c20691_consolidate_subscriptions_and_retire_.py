"""Replace the misleading users product table with subscriptions in place.

Revision ID: 5d9867c20691
Revises: f9d82a8c6e40

This revision was generated with Alembic autogenerate, then corrected to
RENAME rather than drop the existing record and its dependent HWID, node,
traffic and group references. Numeric row IDs remain an internal node-wire
compatibility detail; the public subscription identity is UUIDv4.
"""

from secrets import token_bytes
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import op

revision = "5d9867c20691"
down_revision = "f9d82a8c6e40"
branch_labels = None
depends_on = None

_OLD_INDEXES = (
    ("idx_users_created_at_id", "idx_subscriptions_created_at_id", ["created_at", "id"], False),
    ("idx_users_admin_online", "idx_subscriptions_admin_online", ["admin_id", "online_at"], False),
    ("idx_users_admin_status", "idx_subscriptions_admin_status", ["admin_id", "status"], False),
    ("idx_users_admin_created", "idx_subscriptions_admin_created", ["admin_id", "created_at"], False),
    ("ix_users_username", "ix_subscriptions_username", ["username"], True),
)


def _mapped_uuid(name: str) -> UUID:
    if name.startswith("sub_"):
        try:
            candidate = UUID(name[4:])
            if candidate.version == 4 and str(candidate) == name[4:]:
                return candidate
        except ValueError:
            pass
    return uuid4()


def _rename_stored_permission_resource(old_key: str, new_key: str) -> None:
    """Convert existing role/API-key grants in place, preserving scoped actions.

    Reflected JSON/JSONB columns keep dialect-specific bind processing; no
    stringified JSON SQL or role resets are needed on SQLite/PostgreSQL/MySQL.
    """
    bind = op.get_bind()
    for table_name in ("admin_roles", "api_keys"):
        table = sa.Table(table_name, sa.MetaData(), autoload_with=bind)
        for row in bind.execute(sa.select(table.c.id, table.c.permissions)).mappings():
            permissions = row["permissions"] or {}
            if old_key not in permissions:
                continue
            if new_key in permissions:
                raise ValueError(f"Conflicting {table_name} permission resources on row {row['id']}")
            updated = dict(permissions)
            updated[new_key] = updated.pop(old_key)
            bind.execute(sa.update(table).where(table.c.id == row["id"]).values(permissions=updated))


def upgrade() -> None:
    # The old `users` table already *is* the product subscription row. Never
    # create a shadow table, copy-and-drop, or reset its node/HWID FKs.
    op.rename_table("users", "subscriptions")
    # Rename dependent physical tables in place. SQLite/PostgreSQL maintain
    # their foreign keys, including node usage, group memberships and HWIDs.
    renamed = (
        ("users_groups_association", "subscriptions_groups_association"),
        ("user_subscription_updates", "subscription_updates"),
        ("user_hwids", "subscription_hwids"),
        ("user_templates", "subscription_templates"),
        ("user_usage_logs", "subscription_usage_logs"),
        ("node_user_usages", "node_subscription_usages"),
    )
    for old, new in renamed:
        op.rename_table(old, new)

    # PostgreSQL keeps enum and SERIAL sequence names when a table is renamed.
    # Rename the *existing* objects rather than creating additional enum types
    # or replacing integer keys used by VPN node protocol foreign keys.
    if op.get_bind().dialect.name == "postgresql":
        for original, renamed in (
            ("userstatus", "subscriptionstatus"),
            ("userstatuscreate", "subscriptionstatuscreate"),
        ):
            op.execute(sa.text(f"ALTER TYPE {original} RENAME TO {renamed}"))
        for original, renamed in (
            ("users_id_seq", "subscriptions_id_seq"),
            ("user_hwids_id_seq", "subscription_hwids_id_seq"),
            ("user_templates_id_seq", "subscription_templates_id_seq"),
            ("user_usage_logs_id_seq", "subscription_usage_logs_id_seq"),
            ("user_subscription_updates_id_seq", "subscription_updates_id_seq"),
            ("node_user_usages_id_seq", "node_subscription_usages_id_seq"),
        ):
            op.execute(sa.text(f"ALTER SEQUENCE {original} RENAME TO {renamed}"))

    # Restore schema naming without dropping any user data.
    for table, old_prefix, new_prefix, definitions in (
        (
            "subscriptions_groups_association",
            "ix_users_groups_association",
            "ix_subscriptions_groups_association",
            (("groups_id", "user_id"),),
        ),
        (
            "subscription_updates",
            "idx_user_subscription_updates",
            "idx_subscription_updates",
            (("user_id", "created_at"), ("user_id",), ("created_at",)),
        ),
        (
            "subscription_hwids",
            "ix_user_hwids",
            "ix_subscription_hwids",
            (("last_used_at",), ("hwid",), ("created_at",), ("user_id",)),
        ),
        ("subscription_usage_logs", "ix_user_usage_logs", "ix_subscription_usage_logs", (("user_id", "reset_at"),)),
        (
            "node_subscription_usages",
            "ix_node_user_usages",
            "ix_node_subscription_usages",
            (("created_at",), ("node_id", "created_at"), ("user_id", "created_at")),
        ),
    ):
        for fields in definitions:
            suffix = (
                "user_created"
                if table == "subscription_updates" and fields == ("user_id", "created_at")
                else "_".join(fields)
            )
            op.drop_index(f"{old_prefix}_{suffix}", table_name=table)
            op.create_index(f"{new_prefix}_{suffix}", table, list(fields))
    # Rename unique constraints as the table names change. Do not remove the
    # underlying uniqueness checks: devices, templates and node usage retain
    # their exact previous deduplication guarantees.
    for table, old, new, columns in (
        ("subscription_hwids", "uq_user_hwids_user_id", "uq_subscription_hwids_user_id", ("user_id", "hwid")),
        ("subscription_templates", "uq_user_templates_name", "uq_subscription_templates_name", ("name",)),
        (
            "node_subscription_usages",
            "uq_node_user_usages_created_at",
            "uq_node_subscription_usages_created_at",
            ("created_at", "user_id", "node_id"),
        ),
    ):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(old, type_="unique")
            batch.create_unique_constraint(new, list(columns))

    op.add_column("subscriptions", sa.Column("uuid", sa.Uuid(), nullable=True))
    op.add_column("subscriptions", sa.Column("entitlement", sa.String(24), nullable=True))
    op.add_column("subscriptions", sa.Column("entitlement_revision", sa.BigInteger(), nullable=True))
    op.add_column("subscriptions", sa.Column("policy_revision", sa.BigInteger(), nullable=True))
    op.add_column("subscriptions", sa.Column("access_generation", sa.BigInteger(), nullable=True))
    op.add_column("subscriptions", sa.Column("access_nonce", sa.LargeBinary(16), nullable=True))

    table = sa.table(
        "subscriptions",
        sa.column("id", sa.BigInteger()),
        sa.column("uuid", sa.Uuid()),
        sa.column("entitlement", sa.String()),
        sa.column("entitlement_revision", sa.BigInteger()),
        sa.column("policy_revision", sa.BigInteger()),
        sa.column("access_generation", sa.BigInteger()),
        sa.column("access_nonce", sa.LargeBinary()),
    )
    bind = op.get_bind()
    seen: set[UUID] = set()
    rows = bind.execute(sa.text("SELECT id, username, status FROM subscriptions ORDER BY id")).mappings()
    for row in rows:
        sid = _mapped_uuid(row["username"])
        if sid in seen:
            raise ValueError("Existing subscriptions contain duplicate canonical UUIDs")
        seen.add(sid)
        entitlement = "active"
        if row["status"] == "disabled":
            entitlement = "suspended"
        elif row["status"] == "expired":
            entitlement = "expired"
        bind.execute(
            sa.update(table)
            .where(table.c.id == row["id"])
            .values(
                uuid=sid,
                entitlement=entitlement,
                entitlement_revision=0,
                policy_revision=0,
                access_generation=1,
                access_nonce=token_bytes(16),
            )
        )

    # Nullable columns were needed for data backfill, not for the final domain.
    with op.batch_alter_table("subscriptions") as batch:
        for column in (
            "uuid",
            "entitlement",
            "entitlement_revision",
            "policy_revision",
            "access_generation",
            "access_nonce",
        ):
            batch.alter_column(column, nullable=False)

    for old, new, columns, unique in _OLD_INDEXES:
        op.drop_index(old, table_name="subscriptions")
        op.create_index(new, "subscriptions", columns, unique=unique)
    op.create_index("ix_subscriptions_uuid", "subscriptions", ["uuid"], unique=True)
    _rename_stored_permission_resource("users", "subscriptions")


def downgrade() -> None:
    _rename_stored_permission_resource("subscriptions", "users")
    op.drop_index("ix_subscriptions_uuid", table_name="subscriptions")
    for old, new, columns, unique in _OLD_INDEXES:
        op.drop_index(new, table_name="subscriptions")
        op.create_index(old, "subscriptions", columns, unique=unique)
    with op.batch_alter_table("subscriptions") as batch:
        for column in (
            "access_nonce",
            "access_generation",
            "policy_revision",
            "entitlement_revision",
            "entitlement",
            "uuid",
        ):
            batch.drop_column(column)
    # Restore the historical physical type/sequence names on downgrade.
    if op.get_bind().dialect.name == "postgresql":
        for current, historical in (
            ("subscriptionstatus", "userstatus"),
            ("subscriptionstatuscreate", "userstatuscreate"),
        ):
            op.execute(sa.text(f"ALTER TYPE {current} RENAME TO {historical}"))
        for current, historical in (
            ("subscriptions_id_seq", "users_id_seq"),
            ("subscription_hwids_id_seq", "user_hwids_id_seq"),
            ("subscription_templates_id_seq", "user_templates_id_seq"),
            ("subscription_usage_logs_id_seq", "user_usage_logs_id_seq"),
            ("subscription_updates_id_seq", "user_subscription_updates_id_seq"),
            ("node_subscription_usages_id_seq", "node_user_usages_id_seq"),
        ):
            op.execute(sa.text(f"ALTER SEQUENCE {current} RENAME TO {historical}"))
    op.rename_table("subscriptions", "users")
    for table, old_prefix, new_prefix, definitions in (
        (
            "subscriptions_groups_association",
            "ix_users_groups_association",
            "ix_subscriptions_groups_association",
            (("groups_id", "user_id"),),
        ),
        (
            "subscription_updates",
            "idx_user_subscription_updates",
            "idx_subscription_updates",
            (("user_id", "created_at"), ("user_id",), ("created_at",)),
        ),
        (
            "subscription_hwids",
            "ix_user_hwids",
            "ix_subscription_hwids",
            (("last_used_at",), ("hwid",), ("created_at",), ("user_id",)),
        ),
        ("subscription_usage_logs", "ix_user_usage_logs", "ix_subscription_usage_logs", (("user_id", "reset_at"),)),
        (
            "node_subscription_usages",
            "ix_node_user_usages",
            "ix_node_subscription_usages",
            (("created_at",), ("node_id", "created_at"), ("user_id", "created_at")),
        ),
    ):
        for fields in definitions:
            suffix = (
                "user_created"
                if table == "subscription_updates" and fields == ("user_id", "created_at")
                else "_".join(fields)
            )
            op.drop_index(f"{new_prefix}_{suffix}", table_name=table)
            op.create_index(f"{old_prefix}_{suffix}", table, list(fields))
    for table, old, new, columns in (
        ("subscription_hwids", "uq_user_hwids_user_id", "uq_subscription_hwids_user_id", ("user_id", "hwid")),
        ("subscription_templates", "uq_user_templates_name", "uq_subscription_templates_name", ("name",)),
        (
            "node_subscription_usages",
            "uq_node_user_usages_created_at",
            "uq_node_subscription_usages_created_at",
            ("created_at", "user_id", "node_id"),
        ),
    ):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(new, type_="unique")
            batch.create_unique_constraint(old, list(columns))

    for new, old in (
        ("subscriptions_groups_association", "users_groups_association"),
        ("subscription_updates", "user_subscription_updates"),
        ("subscription_hwids", "user_hwids"),
        ("subscription_templates", "user_templates"),
        ("subscription_usage_logs", "user_usage_logs"),
        ("node_subscription_usages", "node_user_usages"),
    ):
        op.rename_table(new, old)
