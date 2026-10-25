"""ACME DNS provider accounts and managed certificate lifecycle.

Revision ID: waylane_0003
Revises: waylane_0002
"""

import sqlalchemy as sa
from alembic import op

revision = "waylane_0003"
down_revision = "waylane_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "acme_dns_accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("encrypted_dns_token", sa.Text(), nullable=False),
        sa.Column("encrypted_zone_token", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "acme_orders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("preset_id", sa.String(36), sa.ForeignKey("tls_presets.id", ondelete="SET NULL"), unique=True),
        sa.Column("dns_account_id", sa.String(36), sa.ForeignKey("acme_dns_accounts.id"), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("domains", sa.JSON(), nullable=False),
        sa.Column("environment", sa.String(16), nullable=False, server_default="staging"),
        sa.Column("auto_renew", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("last_error", sa.String(256), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_renewal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("encrypted_lego_state", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_acme_orders_due", "acme_orders", ["auto_renew", "next_renewal_at"])


def downgrade() -> None:
    op.drop_index("ix_acme_orders_due", table_name="acme_orders")
    op.drop_table("acme_orders")
    op.drop_table("acme_dns_accounts")
