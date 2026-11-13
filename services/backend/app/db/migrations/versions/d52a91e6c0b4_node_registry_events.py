"""Store sanitized UUID-first Node control lifecycle events.

Revision ID: d52a91e6c0b4
Revises: c41a5e8dbe23
"""
import sqlalchemy as sa
from alembic import op

revision = "d52a91e6c0b4"
down_revision = "c41a5e8dbe23"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "node_agent_events",
        sa.Column("event_uuid", sa.String(36), primary_key=True),
        sa.Column("node_uuid", sa.String(36), sa.ForeignKey("node_agent_registrations.node_uuid", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("core_id", sa.String(32)),
        sa.Column("generation", sa.Integer()),
        sa.Column("status", sa.String(24)),
    )
    op.create_index("ix_node_agent_events_node_created", "node_agent_events", ["node_uuid", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_node_agent_events_node_created", table_name="node_agent_events")
    op.drop_table("node_agent_events")
