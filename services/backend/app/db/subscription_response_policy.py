"""Dedicated response-presentation policy, separate from webhook/outbox settings."""
import sqlalchemy as sa
from app.db.base import Base

subscription_response_policy = sa.Table(
    'subscription_response_policy', Base.metadata,
    sa.Column('id', sa.Integer, primary_key=True),
    sa.Column('revision', sa.Integer, nullable=False, server_default='1'),
    sa.Column('config', sa.JSON, nullable=False),
)
