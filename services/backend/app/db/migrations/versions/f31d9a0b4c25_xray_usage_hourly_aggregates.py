"""Per-hour UUID-first Xray usage aggregates derived from monotonic cursors.

Revision ID: f31d9a0b4c25
Revises: e91c3d0a7f24
"""
import sqlalchemy as sa
from alembic import op

revision = "f31d9a0b4c25"
down_revision = "e91c3d0a7f24"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "node_agent_xray_usage_hours",
        sa.Column("node_uuid", sa.String(36),
                  sa.ForeignKey("node_agent_registrations.node_uuid", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("subscription_uuid", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("bucket_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("uplink", sa.BigInteger(), nullable=False),
        sa.Column("downlink", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_node_agent_xray_usage_hours_bucket",
                    "node_agent_xray_usage_hours", ["bucket_start"])


def downgrade() -> None:
    op.drop_index("ix_node_agent_xray_usage_hours_bucket",
                  table_name="node_agent_xray_usage_hours")
    op.drop_table("node_agent_xray_usage_hours")
