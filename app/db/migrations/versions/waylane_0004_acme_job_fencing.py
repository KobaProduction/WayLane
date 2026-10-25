"""Add per-attempt fencing tokens to ACME orders.

Revision ID: waylane_0004
Revises: waylane_0003
"""

import sqlalchemy as sa
from alembic import op

revision = "waylane_0004"
down_revision = "waylane_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("acme_orders", sa.Column("lease_token", sa.String(36), nullable=True))


def downgrade() -> None:
    op.drop_column("acme_orders", "lease_token")
