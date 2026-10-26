"""Reusable server-side secret and routing presets for Xray cores."""

import sqlalchemy as sa

from app.db.base import Base

core_presets = sa.Table(
    "core_presets", Base.metadata,
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("name", sa.String(120), nullable=False),
    sa.Column("kind", sa.String(20), nullable=False),
    sa.Column("public_data", sa.JSON, nullable=False),
    sa.Column("encrypted_secret", sa.Text, nullable=True),
    sa.Column("revision", sa.Integer, nullable=False, server_default="1"),
    sa.UniqueConstraint("kind", "name", name="uq_core_presets_kind_name"),
)

core_preset_bindings = sa.Table(
    "core_preset_bindings", Base.metadata,
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("preset_id", sa.String(36), sa.ForeignKey("core_presets.id", ondelete="RESTRICT"), nullable=False),
    sa.Column("core_id", sa.BigInteger, sa.ForeignKey("core_configs.id", ondelete="CASCADE"), nullable=False),
    sa.Column("kind", sa.String(20), nullable=False),
    sa.Column("inbound_tag", sa.String(256), nullable=False),
    sa.UniqueConstraint("core_id", "kind", "inbound_tag", name="uq_core_preset_core_kind_tag"),
)
