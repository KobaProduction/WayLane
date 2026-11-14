"""Explicit autonomous Node update and reconnect operations.

Revision ID: a17d6c2e51f0
Revises: f31d9a0b4c25
"""
from alembic import op
import sqlalchemy as sa

revision = "a17d6c2e51f0"
down_revision = "f31d9a0b4c25"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("node_agent_registrations", sa.Column("node_image", sa.String(128)))
    op.create_table(
        "node_agent_operations",
        sa.Column("node_uuid", sa.String(36),
                  sa.ForeignKey("node_agent_registrations.node_uuid", ondelete="CASCADE"), primary_key=True),
        sa.Column("operation_id", sa.String(36), nullable=False, unique=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("image", sa.String(128)),
        sa.Column("previous_image", sa.String(128)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True)),
    )

def downgrade() -> None:
    op.drop_table("node_agent_operations")
    op.drop_column("node_agent_registrations", "node_image")
