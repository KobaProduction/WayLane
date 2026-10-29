"""Bind existing encrypted TLS presets to autonomous Xray Agents.

Revision ID: e81b29c40d72
Revises: d4a83c29fb70
"""
from alembic import op
import sqlalchemy as sa

revision = "e81b29c40d72"
down_revision = "d4a83c29fb70"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "node_agent_tls_bindings",
        sa.Column("node_uuid", sa.String(36), sa.ForeignKey(
            "node_agent_registrations.node_uuid", ondelete="CASCADE"), primary_key=True),
        sa.Column("inbound_tag", sa.String(256), primary_key=True),
        sa.Column("preset_id", sa.String(36), sa.ForeignKey(
            "tls_presets.id", ondelete="RESTRICT"), nullable=False),
    )
    op.create_index("ix_agent_tls_bindings_preset", "node_agent_tls_bindings", ["preset_id"])

def downgrade() -> None:
    op.drop_index("ix_agent_tls_bindings_preset", table_name="node_agent_tls_bindings")
    op.drop_table("node_agent_tls_bindings")
