"""Expire/limit review jobs sync users in bulk instead of per row."""

from __future__ import annotations

from datetime import UTC, datetime as dt, timedelta as td
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import event, inspect as sa_inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import base
from app.db.crud.subscription import (
    get_active_to_expire_subscriptions,
    get_active_to_limited_subscriptions,
    start_subscriptions_expire,
    update_subscriptions_status,
)
from app.db.models import (
    Admin,
    AdminRole,
    Group,
    Subscription,
    SubscriptionStatus,
    SubscriptionUsageResetLogs,
    subscriptions_groups_association,
)
from app.jobs import review_subscriptions
from app.models.settings import HWIDSettings
from app.models.subscription_models import SubscriptionNotificationResponse
from app.operation import OperatorType
from app.operation.subscription import SubscriptionOperation


def _subscription_stub(*, next_plan=None) -> SimpleNamespace:
    sid = uuid4()
    return SimpleNamespace(id=sid, uuid=sid, next_plan=next_plan, status=SubscriptionStatus.active)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [SubscriptionStatus.expired, SubscriptionStatus.limited])
async def test_core_managed_status_change_syncs_once_and_never_activates_next_plan(status):
    """A local expiry/limit revokes access, but Core exclusively owns renewal."""
    plain = _subscription_stub()
    planned = _subscription_stub(next_plan=SimpleNamespace(data_limit=4096))
    subscriptions = [plain, planned]

    async def validate(sub, include_subscription_url=True):
        return SimpleNamespace(uuid=sub.uuid, status=status)

    with (
        patch("app.jobs.review_subscriptions.update_subscriptions_status", new_callable=AsyncMock) as update_status,
        patch("app.jobs.review_subscriptions.sync_subscriptions", new_callable=AsyncMock) as sync,
        patch.object(review_subscriptions.user_operator, "validate_subscription", side_effect=validate),
        patch(
            "app.jobs.review_subscriptions.notification.subscription_status_change", new_callable=AsyncMock
        ) as notify,
    ):
        await review_subscriptions.apply_status_changes(AsyncMock(), subscriptions, status)
        await __import__("asyncio").sleep(0)

    update_status.assert_awaited_once()
    assert update_status.await_args.args[1] == subscriptions
    sync.assert_awaited_once_with(subscriptions)
    assert notify.call_count == 2
    assert planned.next_plan.data_limit == 4096


@pytest.mark.asyncio
async def test_core_managed_active_projection_does_not_rewrite_status():
    subscription = _subscription_stub()

    async def validate(sub, include_subscription_url=True):
        return SimpleNamespace(uuid=sub.uuid, status=sub.status)

    with (
        patch("app.jobs.review_subscriptions.update_subscriptions_status", new_callable=AsyncMock) as update_status,
        patch("app.jobs.review_subscriptions.sync_subscriptions", new_callable=AsyncMock) as sync,
        patch.object(review_subscriptions.user_operator, "validate_subscription", side_effect=validate),
        patch("app.jobs.review_subscriptions.notification.subscription_status_change", new_callable=AsyncMock),
    ):
        await review_subscriptions.apply_status_changes(AsyncMock(), [subscription], SubscriptionStatus.active)

    update_status.assert_not_called()
    sync.assert_awaited_once_with([subscription])


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
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_expire_query_skips_usage_logs(db_session):
    user = Subscription(uuid=uuid4(), status=SubscriptionStatus.active)
    user.expire = dt.now(UTC) - td(hours=1)
    db_session.add(user)
    await db_session.flush()
    db_session.add(SubscriptionUsageResetLogs(subscription_uuid=user.uuid, used_traffic_at_reset=9))
    await db_session.commit()

    expired = await get_active_to_expire_subscriptions(db_session)
    assert len(expired) == 1
    assert "usage_logs" in sa_inspect(expired[0]).unloaded
    assert expired[0].lifetime_used_traffic == 9


@pytest.mark.asyncio
async def test_start_users_expire_updates_all_rows_in_one_statement(db_session):
    users = [
        Subscription(uuid=uuid4(), status=SubscriptionStatus.on_hold, on_hold_expire_duration=3600),
        Subscription(uuid=uuid4(), status=SubscriptionStatus.on_hold, on_hold_expire_duration=7200),
    ]
    db_session.add_all(users)
    await db_session.commit()

    updated = await start_subscriptions_expire(db_session, users)
    assert all(user.status == SubscriptionStatus.active for user in updated)
    assert all(user.on_hold_expire_duration is None for user in updated)
    assert updated[0].expire is not None
    assert updated[1].expire is not None


async def _seed_review_user(
    db_session,
    *,
    label: str,
    status: SubscriptionStatus,
    used_traffic: int = 100,
    reseted: int = 9,
    data_limit: int | None = None,
    expire=None,
    on_hold_expire_duration: int | None = None,
    on_hold_timeout=None,
):
    role = AdminRole(
        name=f"role-{label}",
        is_owner=False,
        permissions={},
        limits={},
        features={},
        access={},
    )
    db_session.add(role)
    await db_session.flush()
    admin = Admin(username=f"admin-{label}", hashed_password="secret", role_id=role.id)
    group = Group(name=f"group-{label}", inbounds=[])
    user = Subscription(
        uuid=uuid4(),
        status=status,
        used_traffic=used_traffic,
        data_limit=data_limit,
        on_hold_expire_duration=on_hold_expire_duration,
        on_hold_timeout=on_hold_timeout,
    )
    if expire is not None:
        user.expire = expire
    db_session.add_all([admin, group, user])
    await db_session.flush()
    user.admin_id = admin.id
    await db_session.execute(
        subscriptions_groups_association.insert().values(subscription_uuid=user.uuid, groups_id=group.id)
    )
    if reseted:
        db_session.add(SubscriptionUsageResetLogs(subscription_uuid=user.uuid, used_traffic_at_reset=reseted))
    await db_session.commit()
    return user.uuid, admin.username, group.name, used_traffic + reseted


def _count_selects(engine):
    select_count = {"n": 0}

    def _count(_conn, _cursor, statement, *_args):
        if str(statement).lstrip().lower().startswith("select"):
            select_count["n"] += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _count)
    return select_count, lambda: event.remove(engine.sync_engine, "before_cursor_execute", _count)


async def _assert_status_job_validation(db_session, users, status: SubscriptionStatus, lifetime: int):
    updated = await update_subscriptions_status(db_session, users, status)
    select_count, stop = _count_selects(db_session.bind)
    try:
        validated = [SubscriptionNotificationResponse.model_validate(user) for user in updated]
    finally:
        stop()
    assert select_count["n"] == 0
    assert {user.lifetime_used_traffic for user in validated} == {lifetime}
    assert all(user.status == status for user in validated)
    return validated


@pytest.mark.asyncio
async def test_expire_notification_validation_stays_async_safe(db_session):
    await _seed_review_user(
        db_session,
        label="expiring-full",
        status=SubscriptionStatus.active,
        expire=dt.now(UTC) - td(hours=1),
    )
    expired = await get_active_to_expire_subscriptions(db_session)
    assert len(expired) == 1
    await _assert_status_job_validation(db_session, expired, SubscriptionStatus.expired, 109)


@pytest.mark.asyncio
async def test_limit_notification_validation_stays_async_safe(db_session):
    await _seed_review_user(
        db_session,
        label="limited-full",
        status=SubscriptionStatus.active,
        used_traffic=50,
        reseted=10,
        data_limit=50,
    )
    limited = await get_active_to_limited_subscriptions(db_session)
    assert len(limited) == 1
    await _assert_status_job_validation(db_session, limited, SubscriptionStatus.limited, 60)


@pytest.mark.asyncio
async def test_existing_hwid_skips_register_when_recently_used():
    op = SubscriptionOperation(OperatorType.API)
    existing = SimpleNamespace(
        last_used_at=dt.now(UTC) - td(seconds=30), device_os=None, os_version=None, device_model=None
    )
    settings = HWIDSettings(enabled=True, forced=False, fallback_limit=3)

    with (
        patch("app.operation.subscription.hwid_settings", new_callable=AsyncMock, return_value=settings),
        patch(
            "app.operation.subscription.get_subscription_hwid_by_value", new_callable=AsyncMock, return_value=existing
        ),
        patch("app.operation.subscription.register_subscription_hwid", new_callable=AsyncMock) as register,
    ):
        await op.validate_and_register_hwid(AsyncMock(), 1, None, None, "device-1", None, None, None)

    register.assert_not_called()


@pytest.mark.asyncio
async def test_existing_hwid_registers_when_stale():
    op = SubscriptionOperation(OperatorType.API)
    existing = SimpleNamespace(last_used_at=dt.now(UTC) - td(minutes=10))
    settings = HWIDSettings(enabled=True, forced=False, fallback_limit=3)

    with (
        patch("app.operation.subscription.hwid_settings", new_callable=AsyncMock, return_value=settings),
        patch(
            "app.operation.subscription.get_subscription_hwid_by_value", new_callable=AsyncMock, return_value=existing
        ),
        patch("app.operation.subscription.register_subscription_hwid", new_callable=AsyncMock) as register,
    ):
        await op.validate_and_register_hwid(AsyncMock(), 1, None, None, "device-1", "iOS", "16", "iPhone")

    register.assert_awaited_once()


@pytest.mark.asyncio
async def test_expired_subscription_batch_preserves_preloaded_notification_metrics(db_session):
    """Fail-closed expiry still preserves lifetime traffic in 25 UUID event snapshots."""
    now = dt.now(UTC)
    for i in range(25):
        await _seed_review_user(
            db_session,
            label=f"expire-batch-{i}",
            status=SubscriptionStatus.active,
            used_traffic=100 + i,
            reseted=7,
            expire=now - td(minutes=5),
        )
    expired = await get_active_to_expire_subscriptions(db_session)
    assert len(expired) == 25
    updated = await update_subscriptions_status(db_session, expired, SubscriptionStatus.expired)
    count, stop = _count_selects(db_session.bind)
    try:
        responses = [SubscriptionNotificationResponse.model_validate(user) for user in updated]
    finally:
        stop()
    assert count["n"] == 0
    assert {res.lifetime_used_traffic for res in responses} == {107 + n for n in range(25)}
    assert all(res.status == SubscriptionStatus.expired and res.uuid.version == 4 for res in responses)


@pytest.mark.asyncio
async def test_core_managed_next_plan_never_activates_from_local_limit_job(db_session):
    """Malformed plans cannot be applied by the local scheduler, even for limited accounts."""
    from contextlib import asynccontextmanager

    from app.db.models import NextPlan

    uuids = []
    for label, expire in (("before", 86_400), ("bad", 10**13), ("after", 86_400)):
        sid, *_ = await _seed_review_user(
            db_session,
            label=f"plan-{label}",
            status=SubscriptionStatus.active,
            used_traffic=200,
            reseted=0,
            data_limit=100,
        )
        db_session.add(NextPlan(subscription_uuid=sid, subscription_template_id=None, data_limit=1000, expire=expire))
        uuids.append(sid)
    await db_session.commit()

    @asynccontextmanager
    async def get_db():
        yield db_session

    async def validate(subscription, include_subscription_url=True):
        return SimpleNamespace(uuid=subscription.uuid)

    with (
        patch.object(review_subscriptions, "GetDB", get_db),
        patch.object(review_subscriptions, "sync_subscriptions", new_callable=AsyncMock) as sync,
        patch.object(review_subscriptions.user_operator, "validate_subscription", side_effect=validate),
        patch("app.jobs.review_subscriptions.notification.subscription_status_change", new_callable=AsyncMock),
    ):
        await review_subscriptions.limit_users_job()
        await __import__("asyncio").sleep(0)

    sync.assert_awaited_once()
    assert {user.uuid for user in sync.await_args.args[0]} == set(uuids)
    db_session.expire_all()
    stored = (await db_session.execute(select(Subscription).where(Subscription.uuid.in_(uuids)))).scalars().all()
    assert len(stored) == 3
    assert all(user.status == SubscriptionStatus.limited and user.data_limit == 100 for user in stored)
    plans = (await db_session.execute(select(NextPlan.subscription_uuid, NextPlan.data_limit))).all()
    assert {row.subscription_uuid: row.data_limit for row in plans} == {sid: 1000 for sid in uuids}


def test_review_scheduler_does_not_own_on_hold_renewal():
    """Core is the only authority that can resume or expire an on-hold subscription."""
    assert not hasattr(review_subscriptions, "on_hold_to_active_users_job")
