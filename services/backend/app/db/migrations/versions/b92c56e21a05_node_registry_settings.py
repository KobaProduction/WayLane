"""Node-specific encrypted Registry credentials and trusted image sources.

Revision ID: b92c56e21a05
Revises: a17d6c2e51f0
"""
from alembic import op
import sqlalchemy as sa

revision = "b92c56e21a05"
down_revision = "a17d6c2e51f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("node_agent_registrations", sa.Column("preferred_registry_repository", sa.String(55)))
    op.create_table(
        "node_agent_registry_settings",
        sa.Column("node_uuid", sa.String(36), sa.ForeignKey("node_agent_registrations.node_uuid", ondelete="CASCADE"), primary_key=True),
        sa.Column("repository", sa.String(55), primary_key=True),
        sa.Column("username", sa.String(128)),
        sa.Column("encrypted_password", sa.Text()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("node_agent_registry_settings")
    op.drop_column("node_agent_registrations", "preferred_registry_repository")
