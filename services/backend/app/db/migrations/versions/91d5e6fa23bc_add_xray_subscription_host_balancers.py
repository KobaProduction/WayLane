"""Add client-side Xray host balancer groups.

Revision ID: 91d5e6fa23bc
Revises: 9266245ebd3d
"""

from alembic import op
import sqlalchemy as sa

revision = "91d5e6fa23bc"
down_revision = "9266245ebd3d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "host_balancers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("host_ids", sa.JSON(), nullable=False),
        sa.Column("strategy", sa.String(20), nullable=False, server_default="roundRobin"),
        sa.Column("probe_interval", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("hide_members", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_disabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_table("host_balancers")
