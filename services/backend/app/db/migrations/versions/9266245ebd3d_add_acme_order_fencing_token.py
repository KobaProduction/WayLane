"""Add per-attempt fencing tokens to ACME orders.

Revision ID: 9266245ebd3d
Revises: 985f97eefe3d
"""

import sqlalchemy as sa
from alembic import op

revision = "9266245ebd3d"
down_revision = "985f97eefe3d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("acme_orders", sa.Column("lease_token", sa.String(36), nullable=True))


def downgrade() -> None:
    op.drop_column("acme_orders", "lease_token")
