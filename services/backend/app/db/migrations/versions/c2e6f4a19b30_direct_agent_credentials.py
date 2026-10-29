"""Allow direct per-agent tokens without discovery invitations.

Revision ID: c2e6f4a19b30
Revises: b7e10d623aa8
"""
from alembic import op
import sqlalchemy as sa

revision = "c2e6f4a19b30"
down_revision = "b7e10d623aa8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("node_agent_registrations") as batch:
        batch.alter_column("invitation_id", existing_type=sa.BigInteger(),
                           existing_nullable=False, nullable=True)
        batch.alter_column("last_seen_at", existing_type=sa.DateTime(timezone=True),
                           existing_nullable=False, nullable=True)


def downgrade() -> None:
    # SQLite and PostgreSQL both reject this on existing direct-agent rows.
    with op.batch_alter_table("node_agent_registrations") as batch:
        batch.alter_column("invitation_id", existing_type=sa.BigInteger(),
                           existing_nullable=True, nullable=False)
        batch.alter_column("last_seen_at", existing_type=sa.DateTime(timezone=True),
                           existing_nullable=True, nullable=False)
