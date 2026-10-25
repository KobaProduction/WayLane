"""Add client notice templates and durable device webhook events.

Revision ID: b9af0263d1e8
Revises: 91d5e6fa23bc
"""

import sqlalchemy as sa
from alembic import op

revision = "b9af0263d1e8"
down_revision = "91d5e6fa23bc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "subscription_firewall_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("scenarios", sa.JSON(), nullable=False),
        sa.Column("webhook_url", sa.String(2048), nullable=True),
        sa.Column("webhook_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("device_check_url", sa.String(2048), nullable=True),
    )
    op.create_table(
        "subscription_device_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("hwid", sa.String(256), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_token", sa.String(36), nullable=True),
        sa.Column("last_error", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_subscription_device_events_pending",
        "subscription_device_events", ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_subscription_device_events_pending", table_name="subscription_device_events")
    op.drop_table("subscription_device_events")
    op.drop_table("subscription_firewall_config")
