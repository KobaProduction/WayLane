"""Persistent client subscription notices and durable device webhook events."""

import sqlalchemy as sa

from app.db.base import Base

subscription_firewall_config = sa.Table(
    "subscription_firewall_config", Base.metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("scenarios", sa.JSON, nullable=False),
    sa.Column("webhook_url", sa.String(2048), nullable=True),
    sa.Column("webhook_enabled", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("device_check_url", sa.String(2048), nullable=True),
)

subscription_device_events = sa.Table(
    "subscription_device_events", Base.metadata,
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("event_type", sa.String(64), nullable=False),
    sa.Column("user_id", sa.BigInteger, nullable=False),
    sa.Column("hwid", sa.String(256), nullable=True),
    sa.Column("payload", sa.JSON, nullable=False),
    sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
    sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
    sa.Column("delivery_token", sa.String(36), nullable=True),
    sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_error", sa.String(256), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    sa.Index("ix_subscription_device_events_pending", "status", "created_at"),
    sa.Index("ix_subscription_device_events_retry_due", "status", "next_attempt_at"),
)
