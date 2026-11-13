"""Track the saved Core Editor configuration applied to an autonomous Xray Node.

Revision ID: e62b47a9d5c1
Revises: f0556e841d8b
"""
from alembic import op
import sqlalchemy as sa

revision = "e62b47a9d5c1"
down_revision = "f0556e841d8b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("node_agent_xray_desired") as batch:
        batch.add_column(sa.Column("source_core_id", sa.BigInteger(), nullable=True))
        batch.create_foreign_key(
            "fk_node_agent_xray_desired_source_core_id",
            "core_configs", ["source_core_id"], ["id"], ondelete="RESTRICT",
        )


def downgrade() -> None:
    with op.batch_alter_table("node_agent_xray_desired") as batch:
        batch.drop_constraint("fk_node_agent_xray_desired_source_core_id", type_="foreignkey")
        batch.drop_column("source_core_id")
