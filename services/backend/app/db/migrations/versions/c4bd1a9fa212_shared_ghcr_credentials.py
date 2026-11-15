"""Encrypted global GHCR read credential for the operator Tokens page.

Revision ID: c4bd1a9fa212
Revises: b92c56e21a05
"""
from alembic import op
import sqlalchemy as sa

revision = "c4bd1a9fa212"
down_revision = "b92c56e21a05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "node_shared_registry_credentials",
        sa.Column("registry", sa.String(24), primary_key=True),
        sa.Column("username", sa.String(128), nullable=False),
        sa.Column("encrypted_token", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("node_shared_registry_credentials")
