"""Slim GET /sub user load and inbounds() reuse of already-loaded groups."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import event, insert, inspect as sa_inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import StaticPool

from app.db import base
from app.db.crud.subscription import get_subscription_by_uuid
from app.db.models import (
    Group,
    ProxyInbound,
    Subscription,
    SubscriptionUsageResetLogs,
    subscriptions_groups_association,
)
from app.operation.subscription import SubscriptionOperation


@pytest.fixture
async def db_session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(base.Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        inbound = ProxyInbound(tag="vless-tcp")
        group = Group(name="g1", inbounds=[inbound])
        user = Subscription(uuid=uuid4(), used_traffic=1000)
        session.add_all([inbound, group, user])
        await session.flush()
        await session.execute(subscriptions_groups_association.insert().values(subscription_uuid=user.uuid, groups_id=group.id))
        session.add(SubscriptionUsageResetLogs(subscription_uuid=user.uuid, used_traffic_at_reset=234))
        await session.commit()
        yield session, user.uuid, engine

    await engine.dispose()


@pytest.mark.asyncio
async def test_slim_user_fetch_skips_heavy_relations(db_session):
    session, subscription_uuid, _engine = db_session
    user = await get_subscription_by_uuid(
        session,
        subscription_uuid,
        load_admin=True,
        load_admin_role=True,
        load_next_plan=False,
        load_usage_logs=False,
        load_groups=False,
        load_lifetime_used_traffic=True,
    )
    assert user is not None
    unloaded = sa_inspect(user).unloaded
    assert "usage_logs" in unloaded
    assert "next_plan" in unloaded
    assert "groups" in unloaded
    assert user.lifetime_used_traffic == 1234
    assert "vless-tcp" in await user.inbounds()
    assert "groups" in sa_inspect(user).unloaded


@pytest.mark.asyncio
async def test_inbounds_skips_sql_when_groups_and_inbounds_are_loaded(db_session):
    session, subscription_uuid, engine = db_session
    user = (
        await session.execute(
            select(Subscription)
            .options(selectinload(Subscription.groups).selectinload(Group.inbounds))
            .where(Subscription.uuid == subscription_uuid)
        )
    ).scalar_one()

    select_count = 0

    def _count(_conn, _cursor, statement, *_args):
        nonlocal select_count
        if str(statement).lstrip().lower().startswith("select"):
            select_count += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _count)
    try:
        tags = await user.inbounds()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _count)

    assert tags == ["vless-tcp"]
    assert select_count == 0


@pytest.mark.asyncio
async def test_validated_subscription_uses_lifetime_expression(db_session):
    session, subscription_uuid, _engine = db_session
    db_user = await get_subscription_by_uuid(
        session,
        subscription_uuid,
        load_next_plan=False,
        load_usage_logs=False,
        load_groups=False,
        load_lifetime_used_traffic=True,
    )
    user = await SubscriptionOperation.validated_subscription(db_user)
    assert user.lifetime_used_traffic == 1234
    assert user.inbounds == ["vless-tcp"]


@pytest.mark.asyncio
async def test_bulk_subscription_insert_assigns_independent_uuid4_and_access_nonces(db_session):
    """Bulk SQL writes must satisfy the same UUID/secret defaults as ORM instances."""
    session, _subscription_uuid, _engine = db_session
    # The bulk SQL INSERT path must allocate unique UUID defaults without an
    # explicit username, numeric key, or pre-chosen UUID in the fixture.
    existing = set((await session.scalars(select(Subscription.uuid))).all())
    await session.execute(
        insert(Subscription),
        [{"created_at": datetime.now(UTC), "proxy_settings": {}} for _ in range(2)],
    )
    await session.commit()
    subscriptions = (
        await session.scalars(select(Subscription).where(Subscription.uuid.not_in(existing)))
    ).all()

    assert len(subscriptions) == 2
    assert len({item.uuid for item in subscriptions}) == 2
    assert all(item.uuid.version == 4 for item in subscriptions)
    assert len({item.access_nonce for item in subscriptions}) == 2
    assert all(isinstance(item.access_nonce, bytes) and len(item.access_nonce) == 16 for item in subscriptions)
    assert all(item.entitlement == "payment_required" for item in subscriptions)
    assert all(item.policy_revision == 0 and item.entitlement_revision == 0 for item in subscriptions)
