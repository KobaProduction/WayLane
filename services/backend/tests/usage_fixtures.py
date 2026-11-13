"""Independent subscription reset test database, without retired Node RPC."""
from __future__ import annotations

import os

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.base import Base
from app.db.models import AdminRole
from app.operation import admin_sync
from config import database_settings


@pytest.fixture
async def session_factory(monkeypatch: pytest.MonkeyPatch, tmp_path):
    database_url = (f"sqlite+aiosqlite:///{tmp_path / 'subscription-reset.db'}"
                    if os.getenv("TEST_FROM", "local").lower() == "local"
                    else database_settings.url)
    engine = create_async_engine(database_url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add_all([
            AdminRole(name="owner", is_owner=True, permissions={}, limits={}, features={}, access={}),
            AdminRole(name="administrator", is_owner=False, permissions={}, limits={}, features={}, access={}),
            AdminRole(name="operator", is_owner=False, permissions={}, limits={}, features={}, access={}),
        ])
        await session.commit()

    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    class TestGetDB:
        async def __aenter__(self):
            self.db = factory()
            return self.db

        async def __aexit__(self, *args):
            await self.db.close()

    monkeypatch.setattr(admin_sync, "GetDB", TestGetDB)
    yield factory
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
