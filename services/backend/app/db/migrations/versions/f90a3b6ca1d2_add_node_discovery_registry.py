"""Add durable discovery registry without modifying existing nodes.

Revision ID: f90a3b6ca1d2
Revises: a4b9d8e31f20
"""
from alembic import op
import sqlalchemy as sa
from app.db.compiles_types import SqliteCompatibleBigInteger

revision = "f90a3b6ca1d2"
down_revision = "a4b9d8e31f20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "node_discovery_invitations",
        sa.Column("id", SqliteCompatibleBigInteger, primary_key=True, autoincrement=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked", sa.String(1), nullable=False, server_default="0"),
    )
    op.create_table(
        "node_agent_registrations",
        sa.Column("node_uuid", sa.String(36), primary_key=True),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("invitation_id", SqliteCompatibleBigInteger,
                  sa.ForeignKey("node_discovery_invitations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("legacy_node_id", SqliteCompatibleBigInteger,
                  sa.ForeignKey("nodes.id", ondelete="SET NULL"), unique=True),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("agent_version", sa.String(32), nullable=False),
        sa.Column("observed_ip", sa.String(45)),
        sa.Column("reported_ipv4", sa.String(45)),
        sa.Column("reported_ipv6", sa.String(45)),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_node_agent_registrations_status", "node_agent_registrations", ["status"])


def downgrade() -> None:
    op.drop_index("ix_node_agent_registrations_status", table_name="node_agent_registrations")
    op.drop_table("node_agent_registrations")
    op.drop_table("node_discovery_invitations")
