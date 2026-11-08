"""The subscription-update cleanup keeps each user's newest updates with one statement of fixed size. Runs on SQLite
by default and on PostgreSQL/MySQL/MariaDB with TEST_FROM=github."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, insert, select

from app.db.models import Subscription, SubscriptionUpdate
from app.jobs import cleanup_subscription_updates as job
from app.models.proxy import ProxyTable
from tests.test_record_usages import reset_user_usage_history_buffer, session_factory  # noqa: F401

LIMIT = 3


@pytest.fixture
def cleanup_db(session_factory, monkeypatch: pytest.MonkeyPatch):  # noqa: F811
    class _GetDB:
        def __init__(self):
            self.db = session_factory()

        async def __aenter__(self):
            return self.db

        async def __aexit__(self, *_exc):
            await self.db.close()

    monkeypatch.setattr(job, "GetDB", _GetDB)
    monkeypatch.setattr(job, "flush_user_sub_updates", AsyncMock())
    monkeypatch.setattr(job.subscription_env_settings, "clients_limit", LIMIT)
    return session_factory


async def _seed(session_factory, rows_per_user: list[int], prefix: str) -> dict[UUID, list[int]]:  # noqa: F811
    """Give each user its number of updates, one second apart; return their ids newest first."""
    now = datetime.now(UTC).replace(microsecond=0)
    async with session_factory() as db:
        users = [
            Subscription(uuid=uuid4(), proxy_settings=ProxyTable().dict(no_obj=True))
            for index in range(len(rows_per_user))
        ]
        db.add_all(users)
        await db.flush()
        rows = [
            {"subscription_uuid": user.uuid, "user_agent": "v2rayNG/1.8", "created_at": now - timedelta(seconds=age)}
            for user, count in zip(users, rows_per_user)
            for age in range(count)
        ]
        if rows:
            await db.execute(insert(SubscriptionUpdate), rows)
        await db.commit()
        subscription_uuids = [user.uuid for user in users]
    return await _updates_by_user(session_factory, subscription_uuids)


async def _updates_by_user(session_factory, subscription_uuids: list[UUID]) -> dict[UUID, list[int]]:  # noqa: F811
    async with session_factory() as db:
        rows = (
            await db.execute(
                select(SubscriptionUpdate.subscription_uuid, SubscriptionUpdate.id)
                .where(SubscriptionUpdate.subscription_uuid.in_(subscription_uuids))
                .order_by(SubscriptionUpdate.created_at.desc())
            )
        ).all()
    updates = {subscription_uuid: [] for subscription_uuid in subscription_uuids}
    for row in rows:
        updates[row.subscription_uuid].append(row.id)
    return updates


@pytest.mark.asyncio
async def test_cleanup_keeps_the_newest_updates_of_each_user(cleanup_db):
    before = await _seed(cleanup_db, [LIMIT + 4, LIMIT + 1, LIMIT, 1, 0], "keep")

    await job.cleanup_subscription_updates()

    after = await _updates_by_user(cleanup_db, list(before))
    assert after == {subscription_uuid: ids[:LIMIT] for subscription_uuid, ids in before.items()}


@pytest.mark.asyncio
async def test_cleanup_statement_does_not_grow_with_users(cleanup_db):
    async def cost_for(users: int, prefix: str) -> tuple[int, int]:
        await _seed(cleanup_db, [LIMIT + 2] * users, prefix)
        statements: list[int] = []
        async with cleanup_db() as db:
            sync_engine = db.bind.sync_engine

        def count(_conn, _cursor, _statement, parameters, *_args):
            statements.append(len(parameters or ()))

        event.listen(sync_engine, "before_cursor_execute", count)
        try:
            await job.cleanup_subscription_updates()
        finally:
            event.remove(sync_engine, "before_cursor_execute", count)
        # (statements, most bind parameters in one statement): asyncpg rejects more than 32767 parameters
        return len(statements), max(statements, default=0)

    few = await cost_for(3, "few")
    many = await cost_for(30, "many")
    assert many == few, f"(statements, max parameters): {few} for 3 users, {many} for 30"
