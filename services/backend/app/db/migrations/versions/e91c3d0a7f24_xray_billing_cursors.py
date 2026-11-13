"""Optional exactly-once commercial cursor for verified autonomous Xray traffic.

Revision ID: e91c3d0a7f24
Revises: d52a91e6c0b4
"""
import sqlalchemy as sa
from alembic import op

revision = "e91c3d0a7f24"
down_revision = "d52a91e6c0b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("node_agent_xray_usage_cursors") as batch:
        batch.add_column(sa.Column("billing_ready", sa.Boolean(), nullable=False,
                                   server_default=sa.text("false")))
        batch.add_column(sa.Column("accounted_uplink", sa.BigInteger(), nullable=False,
                                   server_default=sa.text("0")))
        batch.add_column(sa.Column("accounted_downlink", sa.BigInteger(), nullable=False,
                                   server_default=sa.text("0")))


def downgrade() -> None:
    with op.batch_alter_table("node_agent_xray_usage_cursors") as batch:
        batch.drop_column("accounted_downlink")
        batch.drop_column("accounted_uplink")
        batch.drop_column("billing_ready")
