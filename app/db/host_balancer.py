"""Xray client subscription balancers composed from existing proxy hosts."""

import sqlalchemy as sa

from app.db.base import Base

host_balancers = sa.Table(
    "host_balancers",
    Base.metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("name", sa.String(120), nullable=False, unique=True),
    sa.Column("host_ids", sa.JSON, nullable=False),
    sa.Column("strategy", sa.String(20), nullable=False, server_default="roundRobin"),
    sa.Column("probe_interval", sa.Integer, nullable=False, server_default="60"),
    sa.Column("hide_members", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("is_disabled", sa.Boolean, nullable=False, server_default=sa.false()),
)
