"""Opt-in UUID subscription accounts for autonomous Xray Core inbounds.

Revision ID: b31d7a92c060
Revises: e62b47a9d5c1
"""
from alembic import op
import sqlalchemy as sa

revision = "b31d7a92c060"
down_revision = "e62b47a9d5c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "node_agent_xray_managed_inbounds",
        sa.Column("node_uuid", sa.String(36),
                  sa.ForeignKey("node_agent_registrations.node_uuid", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("inbound_tag", sa.String(128), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_node_agent_xray_managed_inbounds_tag",
                    "node_agent_xray_managed_inbounds", ["inbound_tag"])


def downgrade() -> None:
    op.drop_index("ix_node_agent_xray_managed_inbounds_tag", table_name="node_agent_xray_managed_inbounds")
    op.drop_table("node_agent_xray_managed_inbounds")
