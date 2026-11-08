"""Group list/detail must not hydrate members; syncing a group's users must not query inbounds per user."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import event, func, insert, inspect as sa_inspect, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import base
from app.db.crud.bulk import (
    reset_all_subscriptions_data_usage,
    update_users_datalimit,
    update_users_expire,
    update_users_proxy_settings,
)
from app.db.crud.group import get_group, get_group_by_id, get_group_subscription_uuids, load_group_attrs, remove_group
from app.db.crud.subscription import get_subscriptions, get_subscriptions_by_uuid_batch, remove_subscriptions
from app.db.crud.wireguard import get_subscriptions_accessible_tags
from app.db.models import (
    Admin,
    Group,
    ProxyInbound,
    Subscription,
    SubscriptionStatus,
    SubscriptionTemplate,
    SubscriptionUsageResetLogs,
    inbounds_groups_association,
    subscriptions_groups_association,
    template_group_association,
)
from app.models.group import BulkGroupSelection, GroupListQuery, GroupResponse
from app.models.proxy import ShadowsocksMethods
from app.models.subscription_models import BulkSubscription, BulkSubscriptionsProxy, SubscriptionListQuery
from app.operation import OperatorType
from app.operation.admin import AdminOperation
from app.operation.group import GroupOperation
from app.operation.subscription_management import SubscriptionManagementOperation

USERS = 30


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
        inbounds = [ProxyInbound(tag="in-a"), ProxyInbound(tag="in-b")]
        big = Group(name="big", inbounds=[inbounds[0]])
        other = Group(name="other", inbounds=[inbounds[1]])
        empty = Group(name="empty", inbounds=[])
        session.add_all([*inbounds, big, other, empty])
        await session.flush()
        await session.execute(
            insert(Subscription),
            [{"uuid": uuid4(), "created_at": datetime.now(UTC), "proxy_settings": {}} for _ in range(USERS)],
        )
        subscription_uuids = (await session.execute(select(Subscription.uuid))).scalars().all()
        rows = [{"subscription_uuid": uid, "groups_id": big.id} for uid in subscription_uuids]
        rows += [{"subscription_uuid": uid, "groups_id": other.id} for uid in subscription_uuids[::2]]
        await session.execute(insert(subscriptions_groups_association), rows)
        await session.commit()
        session.expunge_all()  # force fresh loads, like a new request
        yield session

    await engine.dispose()


def _record_statements(session):
    statements: list[str] = []

    @event.listens_for(session.bind.sync_engine, "before_cursor_execute")
    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()))

    return statements


@pytest.mark.asyncio
async def test_get_group_counts_users_in_sql_without_loading_them(db_session):
    statements = _record_statements(db_session)

    groups, total = await get_group(db_session, GroupListQuery())

    assert total == 3
    assert {g.name: GroupResponse.model_validate(g).total_users for g in groups} == {
        "big": USERS,
        "other": (USERS + 1) // 2,
        "empty": 0,
    }
    assert all("subscriptions" in sa_inspect(g).unloaded for g in groups)
    assert not any("FROM users " in s or "JOIN users " in s for s in statements)


@pytest.mark.asyncio
async def test_get_group_by_id_and_load_group_attrs_do_not_load_users(db_session):
    group_id = (await db_session.execute(select(Group.id).where(Group.name == "big"))).scalar_one()

    group = await get_group_by_id(db_session, group_id)
    assert GroupResponse.model_validate(group).total_users == USERS
    assert "subscriptions" in sa_inspect(group).unloaded

    await load_group_attrs(db_session, group)
    assert group.total_users == USERS
    assert "subscriptions" in sa_inspect(group).unloaded


@pytest.mark.asyncio
async def test_get_users_load_group_inbounds_avoids_per_user_inbound_queries(db_session):
    statements = _record_statements(db_session)

    users = await get_subscriptions(
        db_session,
        SubscriptionListQuery(group_ids=[1]),
        load_usage_logs=False,
        load_group_inbounds=True,
    )
    assert len(users) == USERS

    statements.clear()
    tags = [await user.inbounds() for user in users]

    assert statements == []  # every group's inbounds were already loaded
    assert all("in-a" in user_tags for user_tags in tags)
    assert sum("in-b" in user_tags for user_tags in tags) == (USERS + 1) // 2


def _record_bind_counts(session):
    counts: list[int] = []

    @event.listens_for(session.bind.sync_engine, "before_cursor_execute")
    def _record(conn, cursor, statement, parameters, context, executemany):
        counts.append(len(parameters))

    return counts


@pytest.mark.asyncio
async def test_get_subscriptions_accessible_tags_chunks_large_uuid_lists(db_session):
    # UUID-only associations must chunk requests to stay below asyncpg's 32767 bind limit.
    existing_uuids = (await db_session.scalars(select(Subscription.uuid))).all()
    requested_uuids = existing_uuids + [uuid4() for _ in range(25_000 - len(existing_uuids))]
    counts = _record_bind_counts(db_session)

    tags = await get_subscriptions_accessible_tags(db_session, requested_uuids)

    assert max(counts) <= 10_000
    assert len(counts) == 3
    assert set(tags) == set(existing_uuids)
    assert all("in-a" in subscription_tags for subscription_tags in tags.values())
    assert any("in-b" in subscription_tags for subscription_tags in tags.values())


@pytest.mark.asyncio
async def test_get_subscriptions_for_sync_chunks_large_uuid_lists(db_session):
    counts = _record_bind_counts(db_session)

    existing_uuids = (await db_session.scalars(select(Subscription.uuid))).all()
    names = existing_uuids + [uuid4() for _ in range(25_000)]
    users = await GroupOperation._get_users_for_sync(db_session, names)

    assert len(users) == USERS
    assert max(counts) <= 10_000


async def _group_id(session, name):
    return (await session.execute(select(Group.id).where(Group.name == name))).scalar_one()


async def _count_members(session, group_id):
    return (
        await session.execute(
            select(func.count())
            .select_from(subscriptions_groups_association)
            .where(subscriptions_groups_association.c.groups_id == group_id)
        )
    ).scalar_one()


async def _add_big_group_members(session, count):
    """`count` extra users in the "big" group only."""
    big_id = await _group_id(session, "big")
    now = datetime.now(UTC)
    # UUIDv4 has no monotonic next ID. Keep the generated keys instead of
    # filtering by max(previous_uuid), which would silently omit half the rows.
    added_uuids = [uuid4() for _ in range(count)]
    await session.execute(
        insert(Subscription),
        [{"uuid": uid, "created_at": now, "proxy_settings": {}} for uid in added_uuids],
    )
    await session.execute(
        insert(subscriptions_groups_association),
        [{"subscription_uuid": uid, "groups_id": big_id} for uid in added_uuids],
    )
    await session.commit()
    session.expunge_all()


@pytest.mark.asyncio
async def test_remove_group_deletes_association_rows_in_bulk(db_session):
    big_id = await _group_id(db_session, "big")
    other_id = await _group_id(db_session, "other")
    db_session.add(SubscriptionTemplate(name="tpl", extra_settings=None, groups=[]))
    await db_session.flush()
    template_id = (await db_session.execute(select(SubscriptionTemplate.id))).scalar_one()
    await db_session.execute(
        insert(template_group_association), [{"subscription_template_id": template_id, "group_id": big_id}]
    )
    await db_session.commit()
    db_session.expunge_all()
    group = await get_group_by_id(db_session, big_id)
    executemany: list[str] = []
    statements = _record_statements(db_session)

    @event.listens_for(db_session.bind.sync_engine, "before_cursor_execute")
    def _record_executemany(conn, cursor, statement, parameters, context, many):
        if many:
            executemany.append(statement)

    expected_uuids = (await db_session.scalars(select(Subscription.uuid))).all()
    assert sorted(await get_group_subscription_uuids(db_session, [big_id])) == sorted(expected_uuids)

    await remove_group(db_session, group)

    assert executemany == []  # no per-member DELETE
    assert not any("FROM users " in s or "JOIN users " in s for s in statements[1:])  # members were never loaded
    assert group.id == big_id and group.name == "big"  # callers still log/notify with the deleted group
    assert (await db_session.execute(select(Group.id).where(Group.id == big_id))).first() is None
    assert await _count_members(db_session, big_id) == 0
    assert await _count_members(db_session, other_id) == (USERS + 1) // 2  # other groups keep their members
    for table, column in (
        (template_group_association, template_group_association.c.group_id),
        (inbounds_groups_association, inbounds_groups_association.c.group_id),
    ):
        assert (
            await db_session.execute(select(func.count()).select_from(table).where(column == big_id))
        ).scalar_one() == 0
    assert (await db_session.execute(select(func.count()).select_from(ProxyInbound))).scalar_one() == 2


@pytest.mark.asyncio
async def test_get_group_subscription_uuids_is_distinct_and_chunks_large_id_lists(db_session):
    counts = _record_bind_counts(db_session)
    big_id, other_id = await _group_id(db_session, "big"), await _group_id(db_session, "other")

    # every "other" member is also in "big": a user in several of the groups is returned once
    names = await get_group_subscription_uuids(db_session, [big_id, other_id] + list(range(1_000, 26_000)))

    expected_uuids = (await db_session.scalars(select(Subscription.uuid))).all()
    assert sorted(names) == sorted(expected_uuids)
    assert max(counts) <= 10_000


def _stub_node_sync(monkeypatch) -> list[Subscription]:
    synced: list[Subscription] = []

    async def fake_sync_users(users):
        synced.extend(users)

    async def fake_notify(*args):
        return None

    monkeypatch.setattr("app.operation.group.sync_subscriptions", fake_sync_users)
    monkeypatch.setattr("app.operation.group.notification", SimpleNamespace(remove_group=fake_notify))
    return synced


async def _remove_via_operation(session, how, names):
    ids = {await _group_id(session, name) for name in names}
    admin = SimpleNamespace(is_owner=True, role=None, username="tester")
    operation = GroupOperation(OperatorType.API)
    if how == "remove_group":
        (group_id,) = ids
        await operation.remove_group(session, group_id, admin)
    else:
        await operation.bulk_remove_groups_by_id(session, BulkGroupSelection(ids=ids), admin)


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["remove_group", "bulk_remove_groups_by_id"])
async def test_core_managed_group_removal_is_refused_without_node_changes(db_session, monkeypatch, how):
    synced = _stub_node_sync(monkeypatch)
    before = await _count_members(db_session, await _group_id(db_session, "big"))

    with pytest.raises(HTTPException) as exc:
        await _remove_via_operation(db_session, how, ["big"])

    assert exc.value.status_code == 409
    assert synced == []
    assert await _count_members(db_session, await _group_id(db_session, "big")) == before


@pytest.mark.asyncio
async def test_core_managed_bulk_group_removal_is_atomic(db_session, monkeypatch):
    synced = _stub_node_sync(monkeypatch)
    before = {name: await _count_members(db_session, await _group_id(db_session, name)) for name in ("big", "other")}

    with pytest.raises(HTTPException) as exc:
        await _remove_via_operation(db_session, "bulk_remove_groups_by_id", ["big", "other"])

    assert exc.value.status_code == 409
    assert synced == []
    assert {name: await _count_members(db_session, await _group_id(db_session, name)) for name in before} == before


async def _prepare_explicit_users(session):
    subscription_uuids = (await session.execute(select(Subscription.uuid).order_by(Subscription.uuid))).scalars().all()
    await session.execute(
        update(Subscription).values(
            expire=datetime.now(UTC) + timedelta(hours=1), data_limit=1000, used_traffic=500, proxy_settings={}
        )
    )
    await session.commit()
    session.expunge_all()
    return subscription_uuids[:5], set(subscription_uuids[:5]) | {
        uuid4() for _ in range(40_000)
    }  # Five real UUIDs and 40k UUIDs that do not match anything


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation,model",
    [
        (update_users_expire, lambda users: BulkSubscription(amount=-7200, users=users)),
        (update_users_datalimit, lambda users: BulkSubscription(amount=-600, users=users)),
        (
            update_users_proxy_settings,
            lambda users: BulkSubscriptionsProxy(method=ShadowsocksMethods.AES_256_GCM, users=users),
        ),
    ],
)
async def test_bulk_user_ops_with_40k_explicit_ids_and_preloaded_inbounds(db_session, operation, model):
    _, explicit = await _prepare_explicit_users(db_session)
    counts = _record_bind_counts(db_session)

    users, effective = await operation(db_session, model(explicit))

    # Deprecated provider-side bulk actions may not override Core-owned policy.
    assert effective == 0
    assert not users
    assert not counts or max(counts) <= 10_000
    assert (await db_session.scalar(select(func.count()).select_from(Subscription))) == USERS


@pytest.mark.asyncio
async def test_reset_all_subscriptions_data_usage_preserves_uuid_scoped_rows(db_session):
    await _add_big_group_members(db_session, 12_000)
    first_user = (await db_session.execute(select(Subscription.uuid).order_by(Subscription.uuid).limit(1))).scalar_one()
    db_session.add(SubscriptionUsageResetLogs(subscription_uuid=first_user, used_traffic_at_reset=7))
    await db_session.execute(update(Subscription).values(used_traffic=100, status=SubscriptionStatus.limited))
    scoped_uuids = (await db_session.scalars(select(Subscription.uuid).order_by(Subscription.uuid).limit(5))).all()
    await db_session.execute(update(Subscription).where(Subscription.uuid.in_(scoped_uuids)).values(admin_id=1))
    await db_session.commit()
    counts = _record_bind_counts(db_session)

    await reset_all_subscriptions_data_usage(db_session, SimpleNamespace(id=1))  # scoped to one admin's users

    statuses = lambda: select(Subscription.status, func.count()).group_by(Subscription.status)
    assert dict((await db_session.execute(statuses())).all()) == {
        SubscriptionStatus.active: 5,
        SubscriptionStatus.limited: 12_000 + USERS - 5,
    }
    assert (await db_session.execute(select(func.count()).select_from(SubscriptionUsageResetLogs))).scalar_one() == 0

    await reset_all_subscriptions_data_usage(db_session)

    assert dict((await db_session.execute(statuses())).all()) == {SubscriptionStatus.active: 12_000 + USERS}
    assert (await db_session.execute(select(func.sum(Subscription.used_traffic)))).scalar_one() == 0
    assert max(counts) <= 10_000


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["activate", "disable"])
async def test_admin_bulk_activate_and_disable_sync_users_without_usage_logs_or_inbound_queries(
    db_session, monkeypatch, how
):
    admin = Admin(username="adm", hashed_password="x")
    db_session.add(admin)
    await db_session.flush()
    before = SubscriptionStatus.disabled if how == "activate" else SubscriptionStatus.active
    await db_session.execute(update(Subscription).values(admin_id=admin.id, status=before))
    await db_session.commit()
    db_session.expunge_all()
    admin = await db_session.get(Admin, admin.id)
    synced: list[Subscription] = []

    async def fake_sync_users(users):
        synced.extend(users)

    monkeypatch.setattr("app.operation.admin.sync_subscriptions", fake_sync_users)
    operation = AdminOperation(OperatorType.API)
    actor = SimpleNamespace(username="tester")

    # Admin lifecycle operations must not bypass VPN Core's subscription policy.
    with pytest.raises(HTTPException) as exc:
        if how == "activate":
            await operation._activate_all_disabled_users_for_admin(db_session, admin, actor)
        else:
            await operation._disable_all_active_users_for_admin(db_session, admin, actor)

    assert exc.value.status_code == 409
    assert synced == []
    assert set((await db_session.scalars(select(Subscription.status))).all()) == {before}


@pytest.mark.asyncio
async def test_uuid_subscription_loading_with_40k_keys_avoids_large_parameter_lists(db_session):
    # The UUID bulk endpoints load subscriptions by the requested UUID keys with
    # `ids` taken from the request body; asyncpg rejects statements with more than 32767 bind parameters.
    existing_uuids = (await db_session.scalars(select(Subscription.uuid))).all()
    ids = existing_uuids + [uuid4() for _ in range(40_000 - len(existing_uuids))]
    counts = _record_bind_counts(db_session)

    by_query = await get_subscriptions(db_session, SubscriptionListQuery(uuids=ids, limit=len(ids)))
    by_ids = await get_subscriptions_by_uuid_batch(db_session, ids)

    assert len(by_query) == len(by_ids) == USERS
    assert max(counts) <= 10_000


@pytest.mark.asyncio
async def test_remove_users_binds_no_id_parameters(db_session):
    await _add_big_group_members(db_session, 12_000)
    users = await get_subscriptions_by_uuid_batch(
        db_session, (await db_session.execute(select(Subscription.uuid))).scalars().all()
    )
    counts = _record_bind_counts(db_session)

    await remove_subscriptions(db_session, users)

    assert max(counts) <= 10_000
    assert (await db_session.execute(select(func.count()).select_from(Subscription))).scalar_one() == 0
    assert (
        await db_session.execute(select(func.count()).select_from(subscriptions_groups_association))
    ).scalar_one() == 0


@pytest.mark.asyncio
async def test_by_id_bulk_sync_reload_preloads_group_inbounds(db_session):
    statements = _record_statements(db_session)
    ids = (await db_session.execute(select(Subscription.uuid))).scalars().all()

    users = await SubscriptionManagementOperation(OperatorType.API)._load_subscriptions_by_uuids(db_session, ids)

    assert len(users) == USERS
    statements.clear()
    assert all("in-a" in tags for tags in [await user.inbounds() for user in users])
    assert statements == []  # sync_subscriptions reads inbounds from preloaded groups
