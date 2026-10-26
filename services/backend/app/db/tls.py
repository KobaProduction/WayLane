"""Encrypted TLS preset registry; keys never belong in core configuration rows."""

import sqlalchemy as sa

from app.db.base import Base

tls_presets = sa.Table(
    "tls_presets",
    Base.metadata,
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("name", sa.String(120), nullable=False, unique=True),
    sa.Column("certificate_pem", sa.Text(), nullable=False),
    sa.Column("encrypted_private_key", sa.Text(), nullable=False),
    sa.Column("fingerprint_sha256", sa.String(64), nullable=False),
    sa.Column("subject", sa.String(512), nullable=False),
    sa.Column("not_after", sa.DateTime(timezone=True), nullable=False),
    sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    sa.Column("webhook_token_hash", sa.String(64), nullable=True),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
)

tls_bindings = sa.Table(
    "tls_bindings",
    Base.metadata,
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("preset_id", sa.String(36), sa.ForeignKey("tls_presets.id", ondelete="CASCADE"), nullable=False),
    sa.Column("node_id", sa.BigInteger(), sa.ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False),
    sa.Column("core_id", sa.BigInteger(), sa.ForeignKey("core_configs.id", ondelete="CASCADE"), nullable=False),
    sa.Column("inbound_tag", sa.String(256), nullable=False),
    sa.UniqueConstraint("node_id", "inbound_tag", name="uq_tls_bindings_node_inbound"),
)
