"""Data-usage resets keep traffic recorded while they run, and the bulk reset costs the same few statements for any
number of users. Runs on SQLite by default and on PostgreSQL/MySQL/MariaDB with TEST_FROM=github."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import DateTime, event, func, literal, select, text

from app.db.compiles_types import DateDiff
from app.db.crud.subscription import (
    bulk_reset_subscription_data_usage,
    get_subscription_by_uuid,
    get_subscriptions_to_reset_data_usage,
    modify_subscription,
    refresh_and_load_subscription,
    reset_subscription_by_next,
    reset_subscription_data_usage,
)
from app.db.crud.wireguard import ensure_wireguard_subnet_pools
from app.db.models import (
    CoreConfig,
    CoreType,
    DataLimitResetStrategy,
    Group,
    NextPlan,
    NodeSubscriptionUsage,
    NotificationReminder,
    ProxyInbound,
    ReminderType,
    Subscription,
    SubscriptionStatus,
    SubscriptionTemplate,
    SubscriptionUsageResetLogs,
)
from app.models.proxy import ProxyTable
from app.models.settings import HWIDSettings
from app.models.subscription_models import BulkSubscriptionsApplyTemplate, SubscriptionModify
from app.operation import OperatorType, subscription_management as subscription_operation
from app.operation.subscription_management import SubscriptionManagementOperation
from tests.usage_fixtures import session_factory  # noqa: F401


def _user(label: str, **kwargs) -> Subscription:
    user = Subscription(uuid=uuid4(), proxy_settings=ProxyTable().dict(no_obj=True), **kwargs)
    user.created_at = datetime.now(UTC) - timedelta(days=40)
    return user


async def _lifetime(session_factory, subscription_uuid: int) -> tuple[int, int]:  # noqa: F811
    async with session_factory() as db:
        used = (
            await db.execute(select(Subscription.used_traffic).where(Subscription.uuid == subscription_uuid))
        ).scalar_one()
        logged = (
            await db.execute(
                select(func.coalesce(func.sum(SubscriptionUsageResetLogs.used_traffic_at_reset), 0)).where(
                    SubscriptionUsageResetLogs.subscription_uuid == subscription_uuid
                )
            )
        ).scalar_one()
    return int(used), int(logged)


@pytest.mark.asyncio
async def test_core_owned_subscriptions_never_auto_reset_data_usage(session_factory):  # noqa: F811
    """Local reset scheduler must not override VPN Core's traffic accounting."""
    async with session_factory() as db:
        subscription = _user(
            "daily",
            used_traffic=1_000,
            data_limit=10_000,
            data_limit_reset_strategy=DataLimitResetStrategy.day,
        )
        db.add(subscription)
        await db.commit()
        sid = subscription.uuid

    async with session_factory() as db:
        ready = await get_subscriptions_to_reset_data_usage(db)
        assert ready == []
        current = (await db.scalars(select(Subscription).where(Subscription.uuid == sid))).one()
        assert current.used_traffic == 1_000
        assert current.data_limit == 10_000


@pytest.mark.asyncio
async def test_single_reset_keeps_traffic_recorded_after_user_was_loaded(session_factory):  # noqa: F811
    async with session_factory() as db:
        db_user = _user("single", used_traffic=1_000, data_limit=10_000)
        db.add(db_user)
        await db.flush()
        db.add(NextPlan(subscription_uuid=db_user.uuid, subscription_template_id=None, data_limit=5_000))
        await db.commit()
        subscription_uuid = db_user.uuid

    async with session_factory() as db:
        db_user = await get_subscription_by_uuid(db, subscription_uuid)
        assert db_user.next_plan is not None
        # A separate committed WSS accounting transaction races with a reset
        # that has already loaded the subscription in this session.
        from sqlalchemy import update
        async with session_factory() as writer:
            await writer.execute(update(Subscription).where(Subscription.uuid == subscription_uuid).values(
                used_traffic=Subscription.used_traffic + 500))
            await writer.commit()
        db_user = await reset_subscription_data_usage(db, db_user)
        assert (db_user.used_traffic, db_user.next_plan) == (0, None)
        assert db_user.lifetime_used_traffic == 1_500

    assert await _lifetime(session_factory, subscription_uuid) == (0, 1_500)


@pytest.mark.asyncio
async def test_next_plan_resolves_subscription_template_relationship(session_factory, monkeypatch):  # noqa: F811
    """Historical user_template must not be needed to activate a template-backed next plan."""
    async with session_factory() as db:
        template = SubscriptionTemplate(
            name="next_plan_subscription_template",
            extra_settings=None,
            groups=[],
            data_limit=4_096,
            expire_duration=3_600,
        )
        db.add(template)
        await db.flush()
        subscription = _user("next_plan_template_case", used_traffic=200, data_limit=1_000)
        db.add(subscription)
        await db.flush()
        db.add(NextPlan(subscription_uuid=subscription.uuid, subscription_template_id=template.id, data_limit=0))
        await db.commit()
        subscription_uuid = subscription.uuid

    monkeypatch.setattr("app.db.crud.subscription.sync_user_allocations", AsyncMock())
    async with session_factory() as db:
        subscription = await get_subscription_by_uuid(db, subscription_uuid)
        subscription = await reset_subscription_by_next(db, subscription)
        assert subscription.data_limit == 4_096
        assert subscription.status == SubscriptionStatus.active
        assert subscription.data_limit_reset_strategy == DataLimitResetStrategy.no_reset


@pytest.mark.asyncio
async def test_uncommitted_single_reset_leaves_the_loaded_user_consistent(session_factory):  # noqa: F811
    async with session_factory() as db:
        db_user = _user("template", used_traffic=2_000, data_limit=1_000, status=SubscriptionStatus.limited)
        db.add(db_user)
        await db.flush()
        db.add(NextPlan(subscription_uuid=db_user.uuid, subscription_template_id=None, data_limit=5_000))
        await db.commit()
        subscription_uuid = db_user.uuid

    async with session_factory() as db:
        db_user = await get_subscription_by_uuid(db, subscription_uuid)
        assert db_user.next_plan is not None
        # Template flows reset first and modify the same object before one commit
        await reset_subscription_data_usage(db, db_user, commit=False)
        assert (db_user.used_traffic, db_user.status, db_user.next_plan) == (0, SubscriptionStatus.active, None)
        db_user.next_plan = NextPlan(
            subscription_uuid=subscription_uuid, subscription_template_id=None, data_limit=7_000
        )
        await db.commit()

    async with session_factory() as db:
        plans = (
            (await db.execute(select(NextPlan.data_limit).where(NextPlan.subscription_uuid == subscription_uuid)))
            .scalars()
            .all()
        )
    assert plans == [7_000]
    assert await _lifetime(session_factory, subscription_uuid) == (0, 2_000)


async def _seed_reset_cases(db, prefix: str) -> list[int]:
    """One user per reset case; the same cases are seeded under two prefixes to compare two implementations."""
    cases = [
        {"used_traffic": 0, "status": SubscriptionStatus.active},
        {"used_traffic": 300, "data_limit": 1_000, "status": SubscriptionStatus.active},
        {"used_traffic": 1_500, "data_limit": 1_000, "status": SubscriptionStatus.limited},
        {"used_traffic": 700, "data_limit": 1_000, "status": SubscriptionStatus.disabled},
        {"used_traffic": 900, "data_limit": 1_000, "status": SubscriptionStatus.expired},
        {"used_traffic": 800, "status": SubscriptionStatus.on_hold},
        {"used_traffic": 2_000, "data_limit": 2_000, "status": SubscriptionStatus.limited},
    ]
    users = [_user(f"{prefix}{index}", **case) for index, case in enumerate(cases)]
    db.add_all(users)
    await db.flush()
    now = datetime.now(UTC).replace(microsecond=0)
    for index, user in enumerate(users):
        if index % 2 == 0:
            db.add(
                NextPlan(
                    subscription_uuid=user.uuid,
                    subscription_template_id=None,
                    data_limit=10_000 + index,
                    add_remaining_traffic=bool(index % 4),
                )
            )
        if index % 3 == 0:
            db.add(SubscriptionUsageResetLogs(subscription_uuid=user.uuid, used_traffic_at_reset=111 * (index + 1)))
        for threshold in (None, 0, 50, 80):
            db.add(NotificationReminder(subscription_uuid=user.uuid, type=ReminderType.data_usage, threshold=threshold))
        db.add(NotificationReminder(subscription_uuid=user.uuid, type=ReminderType.expiration_date, threshold=3))
        for bucket in range(index % 3):
            db.add(
                NodeSubscriptionUsage(
                    created_at=now - timedelta(minutes=10 * bucket),
                    subscription_uuid=user.uuid,
                    node_id=None,
                    used_traffic=5,
                )
            )
    await db.commit()
    return [user.uuid for user in users]


async def _reset_state(db, subscription_uuids: list[int]) -> list[tuple]:
    """Everything a reset touches, keyed by the user's case index instead of its id."""
    index_of = {subscription_uuid: index for index, subscription_uuid in enumerate(subscription_uuids)}
    users = (
        await db.execute(
            select(Subscription.uuid, Subscription.used_traffic, Subscription.status, Subscription.data_limit).where(
                Subscription.uuid.in_(subscription_uuids)
            )
        )
    ).all()
    logs = (
        await db.execute(
            select(
                SubscriptionUsageResetLogs.subscription_uuid, SubscriptionUsageResetLogs.used_traffic_at_reset
            ).where(SubscriptionUsageResetLogs.subscription_uuid.in_(subscription_uuids))
        )
    ).all()
    plans = (
        await db.execute(select(NextPlan.subscription_uuid).where(NextPlan.subscription_uuid.in_(subscription_uuids)))
    ).all()
    reminders = (
        await db.execute(
            select(
                NotificationReminder.subscription_uuid, NotificationReminder.type, NotificationReminder.threshold
            ).where(NotificationReminder.subscription_uuid.in_(subscription_uuids))
        )
    ).all()
    usages = (
        await db.execute(
            select(NodeSubscriptionUsage.subscription_uuid).where(
                NodeSubscriptionUsage.subscription_uuid.in_(subscription_uuids)
            )
        )
    ).all()
    return [
        sorted((index_of[row.uuid], row.used_traffic, row.status, row.data_limit) for row in users),
        sorted((index_of[row.subscription_uuid], row.used_traffic_at_reset) for row in logs),
        sorted(index_of[row.subscription_uuid] for row in plans),
        sorted(
            (index_of[row.subscription_uuid], row.type, -1 if row.threshold is None else row.threshold)
            for row in reminders
        ),
        sorted(index_of[row.subscription_uuid] for row in usages),
    ]


async def _per_user_reset(db, users: list[Subscription], *, clean_chart_data: bool) -> list[Subscription]:
    """The per-user reset that bulk_reset_subscription_data_usage ran before it became set-based (reference only)."""
    for db_user in users:
        await db_user.awaitable_attrs.next_plan
        db.add(SubscriptionUsageResetLogs(subscription_uuid=db_user.uuid, used_traffic_at_reset=db_user.used_traffic))
        if db_user.next_plan:
            await db.delete(db_user.next_plan)
            db_user.next_plan = None
        db_user.used_traffic = 0
        await db.execute(
            NotificationReminder.__table__.delete().where(
                NotificationReminder.subscription_uuid == db_user.uuid,
                NotificationReminder.type == ReminderType.data_usage,
                NotificationReminder.threshold > 0,
            )
        )
        if clean_chart_data:
            await db.execute(
                NodeSubscriptionUsage.__table__.delete().where(NodeSubscriptionUsage.subscription_uuid == db_user.uuid)
            )
        if db_user.status == SubscriptionStatus.limited:
            db_user.status = SubscriptionStatus.active
    await db.commit()
    for db_user in users:
        await refresh_and_load_subscription(db, db_user, load_admin_role=True)
    return users


def _returned_state(users: list[Subscription], subscription_uuids: list[int]) -> list[tuple]:
    index_of = {subscription_uuid: index for index, subscription_uuid in enumerate(subscription_uuids)}
    return sorted(
        (index_of[user.uuid], user.used_traffic, user.status, user.next_plan, user.lifetime_used_traffic)
        for user in users
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("clean_chart_data", [False, True])
async def test_bulk_reset_matches_the_per_user_reset(session_factory, clean_chart_data):  # noqa: F811
    async with session_factory() as db:
        reference_ids = await _seed_reset_cases(db, "reference")
        bulk_ids = await _seed_reset_cases(db, "bulk")

    async with session_factory() as db:
        reference_users = [await get_subscription_by_uuid(db, subscription_uuid) for subscription_uuid in reference_ids]
        reference_returned = _returned_state(
            await _per_user_reset(db, reference_users, clean_chart_data=clean_chart_data), reference_ids
        )
    async with session_factory() as db:
        bulk_users = [
            await get_subscription_by_uuid(db, subscription_uuid, load_usage_logs=False)
            for subscription_uuid in bulk_ids
        ]
        bulk_returned = _returned_state(
            await bulk_reset_subscription_data_usage(db, bulk_users, clean_chart_data=clean_chart_data), bulk_ids
        )

    async with session_factory() as db:
        reference_state = await _reset_state(db, reference_ids)
        bulk_state = await _reset_state(db, bulk_ids)

    assert bulk_state == reference_state
    assert bulk_returned == reference_returned
    # Sanity: the cases cover status flips, plan removal and reminder cleanup
    assert [status for _, _, status, _ in bulk_state[0]].count(SubscriptionStatus.active) == 4
    assert bulk_state[2] == []
    assert {threshold for *_, threshold in bulk_state[3]} == {-1, 0, 3}


@pytest.mark.asyncio
async def test_bulk_reset_statement_count_does_not_grow_with_users(session_factory):  # noqa: F811
    async def statements_for(count: int, prefix: str) -> int:
        async with session_factory() as db:
            users = [
                _user(f"{prefix}{index}", used_traffic=10, data_limit=5, status=SubscriptionStatus.limited)
                for index in range(count)
            ]
            db.add_all(users)
            await db.flush()
            for user in users:
                db.add(NextPlan(subscription_uuid=user.uuid, subscription_template_id=None, data_limit=100))
                db.add(SubscriptionUsageResetLogs(subscription_uuid=user.uuid, used_traffic_at_reset=1))
                db.add(NotificationReminder(subscription_uuid=user.uuid, type=ReminderType.data_usage, threshold=80))
            await db.commit()
            subscription_uuids = [user.uuid for user in users]

        async with session_factory() as db:
            users = [
                await get_subscription_by_uuid(db, subscription_uuid, load_usage_logs=False)
                for subscription_uuid in subscription_uuids
            ]
            statements = []
            sync_engine = db.bind.sync_engine

            def count(_conn, _cursor, statement, *_args):
                statements.append(statement)

            event.listen(sync_engine, "before_cursor_execute", count)
            try:
                reset_users = await bulk_reset_subscription_data_usage(db, users, clean_chart_data=True)
            finally:
                event.remove(sync_engine, "before_cursor_execute", count)
            assert {user.status for user in reset_users} == {SubscriptionStatus.active}
            assert {user.lifetime_used_traffic for user in reset_users} == {11}
        return len(statements)

    few = await statements_for(3, "few")
    many = await statements_for(40, "many")
    assert many == few, f"{few} statements for 3 users, {many} for 40"


@pytest.mark.asyncio
async def test_reset_cycle_length_is_elapsed_time_on_every_database(session_factory):  # noqa: F811
    def at(*args):
        return literal(datetime(*args), DateTime())

    async with session_factory() as db:
        across_midnight, within_one_date = (
            await db.execute(
                select(
                    DateDiff(at(2026, 10, 22, 0, 0, 5), at(2026, 10, 21, 23, 59, 55)),
                    DateDiff(at(2026, 10, 22, 23, 59, 0), at(2026, 10, 22, 0, 1, 0)),
                )
            )
        ).one()
    # A daily cycle is due after 1 day: 10 seconds across midnight are not a day, 23h58m on one date almost are
    assert float(across_midnight) < 0.001
    assert 0.99 < float(within_one_date) < 1


async def _wait_for_a_lock_wait(session_factory, writer: asyncio.Task) -> bool:  # noqa: F811
    async with session_factory() as db:
        if db.bind.dialect.name == "postgresql":
            waits = text("SELECT count(*) FROM pg_locks WHERE NOT granted")
        else:
            waits = text("SELECT count(*) FROM information_schema.innodb_trx WHERE trx_state = 'LOCK WAIT'")
        # InnoDB refreshes innodb_trx only when it was not read for 0.1 s, so poll slower than that
        for _ in range(100):
            if writer.done():
                return False
            if (await db.execute(waits)).scalar_one():
                return True
            await asyncio.sleep(0.2)
    raise AssertionError("the other writer neither finished nor waited for a lock")


@pytest.mark.asyncio
async def test_apply_template_reset_locks_wireguard_pools_before_user_rows(session_factory, monkeypatch):  # noqa: F811
    # Every writer that allocates WireGuard IPs locks the subnet pool rows before it writes user rows. Applying a
    # template resets the users first and allocates afterwards, in one transaction, so it must take the pool locks
    # before the reset locks the users, or it deadlocks with a concurrent modify of one of its users.
    if session_factory.kw["bind"].dialect.name == "sqlite":
        pytest.skip("SQLite runs one writer at a time, there are no row locks to order")

    async with session_factory() as db:
        inbound = ProxyInbound(tag="wg0")
        template_group = Group(name="template", inbounds=[inbound])
        other_group = Group(name="other", inbounds=[inbound])
        template = SubscriptionTemplate(
            name="reset",
            extra_settings=None,
            groups=[template_group],
            data_limit=5_000,
            reset_usages=True,
        )
        core = CoreConfig(name="wg", config={"interface_name": "wg0", "address": ["10.77.0.1/24"]}, type=CoreType.wg)
        db_user = _user("applied", used_traffic=3_000, data_limit=1_000, status=SubscriptionStatus.limited)
        db.add_all([inbound, template_group, other_group, template, core, db_user])
        await db.flush()
        await ensure_wireguard_subnet_pools(db)
        await db.commit()
        subscription_uuid, template_id, other_group_id = db_user.uuid, template.id, other_group.id

    async def modify_groups_in_another_session():
        # An admin moves the user to another WireGuard group meanwhile (a single modify: pool rows, then the user)
        async with session_factory() as other:
            other_user = await get_subscription_by_uuid(other, subscription_uuid)
            await modify_subscription(other, other_user, SubscriptionModify(group_ids=[other_group_id]))

    writers: list[asyncio.Task] = []
    writer_waited: list[bool] = []
    bulk_reset = subscription_operation.bulk_reset_subscription_data_usage

    async def reset_then_let_another_writer_in(db, users, **kwargs):
        reset_users = await bulk_reset(db, users, **kwargs)
        writers.append(asyncio.create_task(modify_groups_in_another_session()))
        writer_waited.append(await _wait_for_a_lock_wait(session_factory, writers[0]))
        return reset_users

    async def no_op(*args, **kwargs):
        return None

    async def fake_url(user, db_user):
        assert user.uuid == db_user.uuid
        return "https://sub.example/sub/token"

    monkeypatch.setattr(subscription_operation, "bulk_reset_subscription_data_usage", reset_then_let_another_writer_in)
    monkeypatch.setattr(subscription_operation, "hwid_settings", AsyncMock(return_value=HWIDSettings()))
    monkeypatch.setattr(subscription_operation, "sync_subscriptions", no_op)
    monkeypatch.setattr(
        subscription_operation,
        "notification",
        SimpleNamespace(
            modify_subscription=no_op, reset_subscription_data_usage=no_op, subscription_status_change=no_op
        ),
    )
    monkeypatch.setattr(SubscriptionManagementOperation, "generate_subscription_url", staticmethod(fake_url))

    async def apply_template():
        async with session_factory() as db:
            await SubscriptionManagementOperation(OperatorType.API).bulk_apply_template_to_subscriptions(
                db,
                BulkSubscriptionsApplyTemplate(uuids={subscription_uuid}, subscription_template_id=template_id),
                SimpleNamespace(is_owner=True, role=None, username="tester"),
            )

    applied = await asyncio.wait_for(asyncio.gather(apply_template(), return_exceptions=True), 30)
    modified = await asyncio.wait_for(asyncio.gather(*writers, return_exceptions=True), 30)
    assert writer_waited == [True]
    assert [result for result in applied + modified if isinstance(result, BaseException)] == []
    assert await _lifetime(session_factory, subscription_uuid) == (0, 3_000)
