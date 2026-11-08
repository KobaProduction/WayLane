"""Only UUIDv4 can identify a subscriber in the node stats and database pipeline."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Node, NodeSubscriptionUsage, Subscription
from app.jobs import record_usages


def test_node_stat_identity_is_canonical_uuid_not_numeric():
    canonical = uuid4()
    response = SimpleNamespace(stats=[
        SimpleNamespace(name=str(canonical), value=9),
        SimpleNamespace(name=str(canonical), value=3),
        SimpleNamespace(name="123", value=100),
        SimpleNamespace(name=str(uuid4()).upper(), value=5),
    ])
    rows, rejected = record_usages._process_users_stats_response(response)
    assert rows == [{"uid": canonical, "value": 12}]
    assert len(rejected) == 2


@pytest.mark.asyncio
async def test_sqlite_node_usage_upsert_and_counter_use_uuid():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    sessions = async_sessionmaker(eng, expire_on_commit=False)
    try:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sid = uuid4()
        at = datetime.now(UTC).replace(microsecond=0)
        async with sessions() as session:
            sub = Subscription(uuid=sid, proxy_settings={})
            node = Node(
                name="UUID-node", address="127.0.0.1", port=62050,
                api_port=62051, server_ca="stub", api_key="stub", core_config_id=None,
            )
            session.add_all([sub, node])
            await session.flush()
            for delta in (7, 5):
                stmt, params = record_usages.build_node_user_usage_upsert("sqlite", [{
                    "uid": sid, "node_id": node.id, "created_at": at, "value": delta,
                }])[0]
                await (await session.connection()).execute(stmt, params)
            stmt, values = record_usages.build_user_traffic_update("sqlite", [{"uid": sid, "value": 12}])
            await (await session.connection()).execute(stmt, values)
            await session.commit()
            usage = (await session.scalars(select(NodeSubscriptionUsage))).one()
            assert isinstance(usage.subscription_uuid, UUID)
            assert usage.subscription_uuid == sid
            assert usage.used_traffic == 12
            await session.refresh(sub)
            assert sub.uuid == sid
            assert sub.used_traffic == 12
    finally:
        await eng.dispose()
