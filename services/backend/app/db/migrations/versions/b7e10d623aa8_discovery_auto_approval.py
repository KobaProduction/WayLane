"""Allow invitation-scoped automatic agent connection without core permissions.

Revision ID: b7e10d623aa8
Revises: f90a3b6ca1d2
"""
from alembic import op
import sqlalchemy as sa

revision = "b7e10d623aa8"
down_revision = "f90a3b6ca1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("node_discovery_invitations", sa.Column(
        "auto_approve", sa.String(1), nullable=False, server_default="0"
    ))


def downgrade() -> None:
    op.drop_column("node_discovery_invitations", "auto_approve")
