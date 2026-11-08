"""Immutable pre-rename Alembic revision adapter, never loaded by the API.

Two historical revisions explicitly reference this private, isolated table
model while replaying the legacy schema. The runtime models module exposes no
User class or compatibility alias.
"""

from sqlalchemy import JSON, Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class _HistoricalBase(DeclarativeBase):
    pass


class HistoricalUserForAlembic(_HistoricalBase):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proxy_settings: Mapped[dict | None] = mapped_column(JSON)
