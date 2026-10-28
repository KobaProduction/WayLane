"""Persist machine data from the WayLane Agent, never from child cores.

Revision ID: f9d82a8c6e40
Revises: e81b29c40d72
"""
from alembic import op
import sqlalchemy as sa

revision = "f9d82a8c6e40"
down_revision = "e81b29c40d72"
branch_labels = None
depends_on = None

def upgrade() -> None:
    with op.batch_alter_table("node_agent_registrations") as batch:
        batch.add_column(sa.Column("machine_metrics", sa.JSON(), nullable=True))

def downgrade() -> None:
    with op.batch_alter_table("node_agent_registrations") as batch:
        batch.drop_column("machine_metrics")
