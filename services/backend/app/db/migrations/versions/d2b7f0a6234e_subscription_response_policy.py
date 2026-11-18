"""Independent, default-disabled subscription response policies.

Revision ID: d2b7f0a6234e
Revises: c4bd1a9fa212
"""
from alembic import op
import sqlalchemy as sa

revision = 'd2b7f0a6234e'
down_revision = 'c4bd1a9fa212'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'subscription_response_policy',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('revision', sa.Integer, nullable=False, server_default='1'),
        sa.Column('config', sa.JSON, nullable=False),
    )


def downgrade() -> None:
    op.drop_table('subscription_response_policy')
