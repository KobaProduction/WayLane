"""Read-only, epoch-aware autonomous Xray usage cursors.

Revision ID: c41a5e8dbe23
Revises: b31d7a92c060
"""
from alembic import op
import sqlalchemy as sa

revision = "c41a5e8dbe23"
down_revision = "b31d7a92c060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "node_agent_xray_usage_cursors",
        sa.Column("node_uuid", sa.String(36),
                  sa.ForeignKey("node_agent_registrations.node_uuid", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("subscription_uuid", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("epoch_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uplink", sa.BigInteger(), nullable=False),
        sa.Column("downlink", sa.BigInteger(), nullable=False),
        sa.Column("total_uplink", sa.BigInteger(), nullable=False),
        sa.Column("total_downlink", sa.BigInteger(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_node_agent_xray_usage_cursors_subscription_uuid",
                    "node_agent_xray_usage_cursors", ["subscription_uuid"])


def downgrade() -> None:
    op.drop_index("ix_node_agent_xray_usage_cursors_subscription_uuid",
                  table_name="node_agent_xray_usage_cursors")
    op.drop_table("node_agent_xray_usage_cursors")
