"""Separate desired Xray runtime state for autonomous Agent.

Revision ID: d4a83c29fb70
Revises: c2e6f4a19b30
"""
from alembic import op
import sqlalchemy as sa

revision = "d4a83c29fb70"
down_revision = "c2e6f4a19b30"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "node_agent_xray_desired",
        sa.Column("node_uuid", sa.String(36), sa.ForeignKey(
            "node_agent_registrations.node_uuid", ondelete="CASCADE"), primary_key=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("image_version", sa.String(32), nullable=False),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("region", sa.String(64), nullable=True),
        sa.Column("applied_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("runtime_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("last_error", sa.String(300), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
    )

def downgrade() -> None:
    op.drop_table("node_agent_xray_desired")
