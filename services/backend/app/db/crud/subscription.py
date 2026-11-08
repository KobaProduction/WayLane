from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import (
    Row,
    String,
    and_,
    bindparam,
    case,
    delete,
    desc,
    false,
    func,
    insert,
    literal,
    not_,
    or_,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, load_only, selectinload, with_expression
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.functions import coalesce

from app.db.compiles_types import DateDiff
from app.db.models import (
    Admin,
    AdminStatus,
    DataLimitResetStrategy,
    Group,
    NextPlan,
    Node,
    NodeSubscriptionUsage,
    NotificationReminder,
    ProxyInbound,
    ReminderType,
    Subscription,
    SubscriptionHWID,
    SubscriptionStatus,
    SubscriptionStatusCreate,
    SubscriptionUpdate,
    SubscriptionUsageResetLogs,
    inbounds_groups_association,
    subscriptions_groups_association,
)
from app.models.proxy import ProxyTable
from app.models.stats import (
    Period,
    SubscriptionCountMetric,
    SubscriptionCountMetricStat,
    SubscriptionCountMetricStatsList,
    SubscriptionUsageStat,
    SubscriptionUsageStatsList,
    validate_user_count_metric_scope,
)
from app.models.subscription_models import (
    ExpiredSubscriptionsQuery,
    SubscriptionCreate,
    SubscriptionListQuery,
    SubscriptionModify,
    SubscriptionNotificationResponse,
    SubscriptionSimpleListQuery,
    SubscriptionSimpleSortField,
    SubscriptionSimpleSortOption,
    SubscriptionSortField,
    SubscriptionSortOption,
)
from app.models.validators import MAX_ON_HOLD_EXPIRE_DURATION_SECONDS
from app.subscription.sub_update_buffer import flush_user_sub_updates, queue_user_sub_update
from config import user_cleanup_settings

from .general import (
    _build_trunc_expression,
    attach_timezone_to_period_start,
    build_json_proxy_settings_search_condition,
    get_complete_period_start_for_filter,
    to_utc_for_filter,
)
from .group import get_groups_by_ids
from .wireguard import (
    release_allocations_by_subscription_uuids,
    release_users_allocations,
    sync_user_allocations,
    tags_from_groups,
)

_ONLINE_USERS_WINDOW = timedelta(minutes=2)


def _review_subscription_select_stmt(*, load_groups: bool = True) -> Select:
    """Load relations needed for status review jobs without materializing usage logs."""
    return _build_subscription_select_stmt(
        load_admin=True,
        load_admin_role=True,
        load_next_plan=True,
        load_usage_logs=False,
        load_groups=load_groups,
        load_lifetime_used_traffic=True,
    )


def _subscription_reset_traffic_subquery():
    return (
        select(func.coalesce(func.sum(SubscriptionUsageResetLogs.used_traffic_at_reset), 0))
        .where(SubscriptionUsageResetLogs.subscription_uuid == Subscription.uuid)
        .correlate(Subscription)
        .scalar_subquery()
    )


def _snapshot_reseted_usage(users: list[Subscription]) -> dict[UUID, int]:
    """Keep the already-loaded lifetime aggregate across bulk UPDATEs."""
    return {user.uuid: int(user.reseted_usage) for user in users}


def _restore_reseted_usage(users: list[Subscription], reseted_usage: dict[UUID, int]) -> None:
    for user in users:
        if user.uuid in reseted_usage:
            user.__dict__["_reseted_usage_query"] = reseted_usage[user.uuid]


def _safe_on_hold_expire_duration(duration: int | None) -> int | None:
    if duration is None or duration <= 0:
        return None
    return min(duration, MAX_ON_HOLD_EXPIRE_DURATION_SECONDS)


def _resolve_enabled_subscription_status(user: Subscription) -> SubscriptionStatus:
    now = datetime.now(UTC)
    if user.expire is not None and user.expire.replace(tzinfo=UTC) <= now:
        return SubscriptionStatus.expired
    if user.data_limit is not None and user.data_limit > 0 and user.used_traffic >= user.data_limit:
        return SubscriptionStatus.limited
    if user.on_hold_expire_duration is not None:
        return SubscriptionStatus.on_hold
    return SubscriptionStatus.active


def _explicit_uuids(column, uuids: Sequence[UUID]):
    """Bound UUID filter that does not exhaust asyncpg's parameter limit."""
    return column.in_(bindparam(None, list(uuids), expanding=True, literal_execute=True))


def _build_subscription_select_stmt(
    *,
    load_admin: bool = True,
    load_admin_role: bool = False,
    load_next_plan: bool = True,
    load_usage_logs: bool = True,
    load_groups: bool = True,
    join_groups: bool = False,
    load_group_inbounds: bool = False,
    load_lifetime_used_traffic: bool = False,
) -> Select:
    """Build a subscription select statement with eager-load options."""
    stmt = select(Subscription)
    options = []
    if load_admin:
        admin_loader = joinedload(Subscription.admin)
        if load_admin_role:
            admin_loader = admin_loader.selectinload(Admin.role)
        options.append(admin_loader)
    if load_next_plan:
        options.append(joinedload(Subscription.next_plan))
    if load_usage_logs:
        options.append(selectinload(Subscription.usage_logs))
    if load_groups:
        if join_groups:
            groups_loader = joinedload(Subscription.groups)
        else:
            groups_loader = selectinload(Subscription.groups)
            if load_group_inbounds:
                groups_loader = groups_loader.selectinload(Group.inbounds)
        options.append(groups_loader)
    if options:
        stmt = stmt.options(*options)
    if load_lifetime_used_traffic:
        stmt = stmt.options(with_expression(Subscription._reseted_usage_query, _subscription_reset_traffic_subquery()))
    return stmt


async def load_subscription_attrs(
    user: Subscription,
    *,
    load_admin: bool = True,
    load_admin_role: bool = False,
    load_next_plan: bool = True,
    load_usage_logs: bool = True,
    load_groups: bool = True,
):
    if load_admin:
        await user.awaitable_attrs.admin
        if load_admin_role and user.admin is not None:
            await user.admin.awaitable_attrs.role
    if load_next_plan:
        await user.awaitable_attrs.next_plan
    if load_usage_logs:
        await user.awaitable_attrs.usage_logs
    if load_groups:
        await user.awaitable_attrs.groups


async def refresh_and_load_subscription(
    db: AsyncSession,
    user: Subscription,
    *,
    load_admin: bool = True,
    load_admin_role: bool = False,
    load_next_plan: bool = True,
    load_usage_logs: bool = True,
    load_groups: bool = True,
):
    await db.refresh(user)
    await load_subscription_attrs(
        user,
        load_admin=load_admin,
        load_admin_role=load_admin_role,
        load_next_plan=load_next_plan,
        load_usage_logs=load_usage_logs,
        load_groups=load_groups,
    )


async def get_subscription_by_uuid(
    db: AsyncSession,
    subscription_uuid: UUID,
    *,
    load_admin: bool = True,
    load_admin_role: bool = False,
    load_next_plan: bool = True,
    load_usage_logs: bool = True,
    load_groups: bool = True,
    join_groups: bool = False,
    load_lifetime_used_traffic: bool = False,
    admin_id: int | None = None,
) -> Subscription | None:
    """
    Retrieves a subscription by its canonical UUID.

    Args:
        db (AsyncSession): Database session.
        subscription_uuid (UUID): Canonical subscription UUIDv4.
        admin_id: If provided, only return the user if they belong to this admin.

    Returns:
        Optional[Subscription]: The user object if found, else None.
    """
    stmt = _build_subscription_select_stmt(
        load_admin=load_admin,
        load_admin_role=load_admin_role,
        load_next_plan=load_next_plan,
        load_usage_logs=load_usage_logs,
        load_groups=load_groups,
        join_groups=join_groups,
        load_lifetime_used_traffic=load_lifetime_used_traffic,
    ).where(Subscription.uuid == subscription_uuid)

    if admin_id is not None:
        stmt = stmt.where(Subscription.admin_id == admin_id)

    return (await db.execute(stmt)).unique().scalar_one_or_none()


async def get_wireguard_subscription_node_identity(
    db: AsyncSession,
    subscription_uuid: UUID,
    *,
    load_admin_role: bool = True,
    admin_id: int | None = None,
) -> Subscription | None:
    """Load only the ORM state required to render a WireGuard subscription."""
    stmt = _build_subscription_select_stmt(
        load_admin=True,
        load_admin_role=load_admin_role,
        load_next_plan=False,
        load_usage_logs=False,
        load_groups=False,
        load_lifetime_used_traffic=False,
    ).where(Subscription.uuid == subscription_uuid)
    if admin_id is not None:
        stmt = stmt.where(Subscription.admin_id == admin_id)

    user = (await db.execute(stmt)).unique().scalar_one_or_none()
    if user is None:
        return None

    tags_stmt = (
        select(ProxyInbound.tag)
        .select_from(subscriptions_groups_association)
        .join(Group, subscriptions_groups_association.c.groups_id == Group.id)
        .join(inbounds_groups_association, Group.id == inbounds_groups_association.c.group_id)
        .join(ProxyInbound, inbounds_groups_association.c.inbound_id == ProxyInbound.id)
        .where(subscriptions_groups_association.c.subscription_uuid == subscription_uuid, Group.is_disabled.is_(False))
        .distinct()
    )
    user.__dict__["_wireguard_inbounds"] = list((await db.execute(tags_stmt)).scalars().all())
    return user


async def get_subscription_lifetime_used_traffic(db: AsyncSession, subscription_uuid: UUID) -> int:
    stmt = (
        select(
            func.coalesce(func.sum(SubscriptionUsageResetLogs.used_traffic_at_reset), 0)
            + func.coalesce(Subscription.used_traffic, 0)
        )
        .select_from(Subscription)
        .outerjoin(SubscriptionUsageResetLogs, SubscriptionUsageResetLogs.subscription_uuid == Subscription.uuid)
        .where(Subscription.uuid == subscription_uuid)
        .group_by(Subscription.uuid)
    )
    result = await db.execute(stmt)
    value = result.scalar_one_or_none()
    return int(value or 0)


async def get_existing_subscription_uuids(db: AsyncSession, uuids: Sequence[UUID]) -> set[UUID]:
    """Read the canonical UUIDs already allocated to a subscription."""
    if not uuids:
        return set()
    return set((await db.scalars(select(Subscription.uuid).where(Subscription.uuid.in_(uuids)))).all())


async def get_subscriptions_with_proxy_settings(
    db: AsyncSession,
    *,
    exclude_subscription_uuid: UUID | None = None,
) -> list[Subscription]:
    """
    Retrieve users for proxy-settings related operations without eager-loading relations.
    """
    stmt = select(Subscription)
    if exclude_subscription_uuid is not None:
        stmt = stmt.where(Subscription.uuid != exclude_subscription_uuid)

    result = await db.execute(stmt)
    return list(result.scalars().all())


def _subscription_hwid_count_subquery():
    return (
        select(func.count(SubscriptionHWID.id))
        .where(SubscriptionHWID.subscription_uuid == Subscription.uuid)
        .correlate(Subscription)
        .scalar_subquery()
    )


async def get_admin_subscriptions_for_node_sync(
    db: AsyncSession,
    admin_id: int,
    *,
    statuses: Sequence[SubscriptionStatus] | None = None,
) -> list[Subscription]:
    """Load an admin's users for node dispatch, without response-only fields or history."""
    return await _get_subscriptions_for_node_sync(db, Subscription.admin_id == admin_id, statuses=statuses)


async def get_role_subscriptions_for_node_sync(
    db: AsyncSession,
    role_id: int,
    admin_statuses: Sequence[AdminStatus],
    *,
    after_subscription_uuid: UUID | None = None,
    limit: int = 500,
) -> list[Subscription]:
    """Load one UUID-ordered batch of Core-authorized subscriptions of affected admins."""
    admin_ids = select(Admin.id).where(Admin.role_id == role_id, Admin.status.in_(admin_statuses))
    return await _get_subscriptions_for_node_sync(
        db,
        Subscription.admin_id.in_(admin_ids),
        statuses=[SubscriptionStatus.active, SubscriptionStatus.on_hold],
        after_subscription_uuid=after_subscription_uuid,
        limit=limit,
    )


async def _get_subscriptions_for_node_sync(
    db: AsyncSession,
    admin_filter: ColumnElement[bool],
    *,
    statuses: Sequence[SubscriptionStatus] | None = None,
    after_subscription_uuid: UUID | None = None,
    limit: int | None = None,
) -> list[Subscription]:
    """Load node-dispatch state, optionally restricted to a bounded UUID-ordered page."""
    stmt = (
        select(Subscription)
        .where(admin_filter)
        .options(
            load_only(
                Subscription.uuid,
                Subscription.admin_id,
                Subscription.status,
                Subscription.entitlement,
                Subscription.proxy_settings,
            ),
            joinedload(Subscription.admin).joinedload(Admin.role),
            selectinload(Subscription.groups).selectinload(Group.inbounds),
        )
    )
    if statuses is not None:
        stmt = stmt.where(Subscription.status.in_(statuses))
    if limit is not None:
        # UUIDv4 keys are sortable, but cannot be compared with integer 0.
        # A null cursor starts from the first UUID on SQLite and PostgreSQL.
        if after_subscription_uuid is not None:
            stmt = stmt.where(Subscription.uuid > after_subscription_uuid)
        stmt = stmt.order_by(Subscription.uuid).limit(limit)
    return list((await db.scalars(stmt)).all())


def _build_subscription_sort_clause(sort_option: SubscriptionSortOption):
    if sort_option.field == SubscriptionSortField.hwid_count:
        count = _subscription_hwid_count_subquery()
        return count.desc() if sort_option.value.startswith("-") else count.asc()

    field_map = {
        SubscriptionSortField.uuid: Subscription.uuid,
        SubscriptionSortField.used_traffic: Subscription.used_traffic,
        SubscriptionSortField.data_limit: Subscription.data_limit,
        SubscriptionSortField.expire: Subscription.expire,
        SubscriptionSortField.created_at: Subscription.created_at,
    }
    nullable_field_map = {
        SubscriptionSortField.edit_at: Subscription.edit_at,
        SubscriptionSortField.online_at: Subscription.online_at,
    }

    if sort_option.field in field_map:
        column = field_map[sort_option.field]
        return column.desc() if sort_option.value.startswith("-") else column.asc()

    column = nullable_field_map[sort_option.field]
    return (
        case((column.is_(None), 1), else_=0).asc(),
        column.desc() if sort_option.value.startswith("-") else column.asc(),
    )


def _build_subscription_simple_sort_clause(sort_option: SubscriptionSimpleSortOption):
    field_map = {
        SubscriptionSimpleSortField.uuid: Subscription.uuid,
        SubscriptionSimpleSortField.uuid: Subscription.uuid,
    }
    column = field_map[sort_option.field]
    return column.desc() if sort_option.value.startswith("-") else column.asc()


def _build_subscription_sort_clauses(sort_options: list[SubscriptionSortOption]) -> list[ColumnElement]:
    clauses = []
    for sort_option in sort_options:
        clause = _build_subscription_sort_clause(sort_option)
        if isinstance(clause, tuple):
            clauses.extend(clause)
        else:
            clauses.append(clause)

    if sort_options:
        # Keep offset pagination deterministic when multiple users share the
        # same value for the requested sort field. Matching the last direction
        # also lets the default created_at sort use (created_at, id) directly.
        last_sort_descending = sort_options[-1].value.startswith("-")
        clauses.append(Subscription.uuid.desc() if last_sort_descending else Subscription.uuid.asc())

    return clauses


def _build_subscription_count_stmt(stmt: Select) -> Select:
    """Count filtered users without carrying page ordering or wide user rows."""
    return stmt.with_only_columns(func.count(Subscription.uuid)).order_by(None)


async def get_subscriptions(
    db: AsyncSession,
    query: SubscriptionListQuery,
    admin: Admin | None = None,
    return_with_count: bool = False,
    load_admin_role: bool = False,
    load_usage_logs: bool = True,
    load_lifetime_used_traffic: bool = False,
    load_group_inbounds: bool = False,
    load_hwid_count: bool = False,
) -> list[Subscription] | tuple[list[Subscription], int]:
    """
    Retrieves users based on various filters.

    Args:
        db: Database session.
        query: Structured user list query filters.
        admin: Admin filter.
        return_with_count: Whether to return total count.
        load_usage_logs: Whether to materialize reset-history rows.
        load_lifetime_used_traffic: Whether to calculate lifetime usage with an aggregate.
        load_group_inbounds: Whether to eager-load each user's groups' inbounds (avoids a query per user when
            serializing users for nodes).

    Returns:
        List of users or tuple with (users, count) if return_with_count is True.
    """
    admin_loader = selectinload(Subscription.admin)
    if load_admin_role:
        admin_loader = admin_loader.selectinload(Admin.role)

    groups_loader = selectinload(Subscription.groups)
    if load_group_inbounds:
        groups_loader = groups_loader.selectinload(Group.inbounds)

    options = [
        admin_loader,
        selectinload(Subscription.next_plan),
        groups_loader,
    ]
    if load_usage_logs:
        options.append(selectinload(Subscription.usage_logs))
    if load_lifetime_used_traffic:
        options.append(with_expression(Subscription._reseted_usage_query, _subscription_reset_traffic_subquery()))
    stmt = select(Subscription).options(*options)
    if load_lifetime_used_traffic:
        stmt = stmt.execution_options(populate_existing=True)
    if load_hwid_count:
        stmt = stmt.options(with_expression(Subscription.hwid_count, _subscription_hwid_count_subquery()))

    filters = []
    if query.uuids:
        filters.append(_explicit_uuids(Subscription.uuid, query.uuids))
    if query.search:
        filters.append(
            or_(Subscription.uuid.cast(String).ilike(f"%{query.search}%"), Subscription.note.ilike(f"%{query.search}%"))
        )

    if query.status:
        if isinstance(query.status, list):
            filters.append(Subscription.status.in_(query.status))
        else:
            filters.append(Subscription.status == query.status)
    if admin:
        filters.append(Subscription.admin_id == admin.id)
    if query.owner or query.admin_ids:
        stmt = stmt.join(Subscription.admin)
        if query.owner:
            filters.append(Admin.username.in_(query.owner))
        if query.admin_ids:
            filters.append(Admin.id.in_(query.admin_ids))
    if query.data_limit_reset_strategy:
        if isinstance(query.data_limit_reset_strategy, list):
            filters.append(Subscription.data_limit_reset_strategy.in_(query.data_limit_reset_strategy))
        else:
            filters.append(Subscription.data_limit_reset_strategy == query.data_limit_reset_strategy)
    if query.no_data_limit:
        filters.append(or_(Subscription.data_limit.is_(None), Subscription.data_limit == 0))
    else:
        if query.data_limit_min is not None:
            filters.append(
                and_(
                    Subscription.data_limit.is_not(None),
                    Subscription.data_limit > 0,
                    Subscription.data_limit >= query.data_limit_min,
                )
            )
        if query.data_limit_max is not None:
            filters.append(
                and_(
                    Subscription.data_limit.is_not(None),
                    Subscription.data_limit > 0,
                    Subscription.data_limit <= query.data_limit_max,
                )
            )
    if query.no_expire:
        filters.append(and_(Subscription.expire.is_(None), Subscription.status != SubscriptionStatus.on_hold))
    else:
        if query.expire_after is not None:
            filters.append(and_(Subscription.expire.is_not(None), Subscription.expire >= query.expire_after))
        if query.expire_before is not None:
            filters.append(and_(Subscription.expire.is_not(None), Subscription.expire <= query.expire_before))
    if query.online_after is not None:
        filters.append(and_(Subscription.online_at.is_not(None), Subscription.online_at >= query.online_after))
    if query.online_before is not None:
        filters.append(and_(Subscription.online_at.is_not(None), Subscription.online_at <= query.online_before))
    if query.online:
        filters.append(
            and_(
                Subscription.online_at.is_not(None), Subscription.online_at >= datetime.now(UTC) - _ONLINE_USERS_WINDOW
            )
        )

    if query.group_ids:
        filters.append(Subscription.groups.any(Group.id.in_(query.group_ids)))
    if query.no_group:
        filters.append(~Subscription.groups.any())
    if query.proxy_id:
        filters.append(build_json_proxy_settings_search_condition(db, Subscription.proxy_settings, query.proxy_id))

    if filters:
        stmt = stmt.where(and_(*filters))

    if query.sort:
        stmt = stmt.order_by(*_build_subscription_sort_clauses(query.sort))

    total = None
    if return_with_count:
        count_stmt = _build_subscription_count_stmt(stmt)
        result = await db.execute(count_stmt)
        total = result.scalar()

    if query.offset:
        stmt = stmt.offset(query.offset)
    if query.limit:
        stmt = stmt.limit(query.limit)

    result = await db.execute(stmt)
    users = list(result.unique().scalars().all())

    if return_with_count:
        return users, total
    return users


async def get_subscriptions_simple(
    db: AsyncSession,
    query: SubscriptionSimpleListQuery,
    admin: Admin | None = None,
) -> tuple[list[tuple[UUID]], int]:
    """
    Retrieves lightweight user data with only id and username.

    Args:
        db: Database session.
        query: Structured lightweight user list filters.
        admin: Admin filter (for scope-based authorization).

    Returns:
        Tuple of (list of (id, username) tuples, total_count).
    """
    stmt = select(Subscription.uuid)

    filters = []
    if query.uuids:
        filters.append(_explicit_uuids(Subscription.uuid, query.uuids))
    if query.search:
        filters.append(Subscription.uuid.cast(String).ilike(f"%{query.search}%"))
    if admin:
        filters.append(Subscription.admin_id == admin.id)

    if filters:
        stmt = stmt.where(and_(*filters))

    if query.sort:
        sort_list = []
        for sort_option in query.sort:
            clause = _build_subscription_simple_sort_clause(sort_option)
            if isinstance(clause, tuple):
                sort_list.extend(clause)
            else:
                sort_list.append(clause)
        stmt = stmt.order_by(*sort_list)

    # Get count BEFORE pagination (always)
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar()

    # Apply pagination or safety limit
    if not query.all:
        if query.offset:
            stmt = stmt.offset(query.offset)
        if query.limit:
            stmt = stmt.limit(query.limit)
    else:
        stmt = stmt.limit(10000)  # Safety limit when all=true

    # Execute and return
    result = await db.execute(stmt)
    rows = result.all()

    return rows, total


async def get_expired_subscriptions(
    db: AsyncSession,
    query: ExpiredSubscriptionsQuery,
    admin_id: int | None = None,
):
    conditions = _cleanup_target_subscription_conditions(query.expired_after, query.expired_before, admin_id, query.target)
    stmt = select(Subscription).where(*conditions)

    return (await db.execute(stmt)).unique().scalars().all()


def _cleanup_target_subscription_conditions(
    expired_after: datetime | None = None,
    expired_before: datetime | None = None,
    admin_id: int | None = None,
    target: Literal["expired", "limited", "on_hold", "disabled", "inactive"] = "expired",
):
    if target == "expired":
        # Time-expired users: date range filters on the expiration date.
        conditions = [Subscription.is_expired]
        if expired_after:
            conditions.append(Subscription.expire >= expired_after)
        if expired_before:
            conditions.append(Subscription.expire <= expired_before)
    elif target == "inactive":
        # Fall back to account creation for users that have never connected,
        # so newly created accounts are not treated as long-term inactive.
        last_activity = func.coalesce(Subscription.online_at, Subscription.created_at)
        conditions = []
        if expired_after:
            conditions.append(last_activity >= expired_after)
        if expired_before:
            conditions.append(last_activity <= expired_before)
    else:
        # For limited / on_hold / disabled: date range filters apply to
        # last_status_change (i.e. when the user entered that status).
        status_map = {
            "limited": SubscriptionStatus.limited,
            "on_hold": SubscriptionStatus.on_hold,
            "disabled": SubscriptionStatus.disabled,
        }
        conditions = [Subscription.status == status_map[target]]
        if expired_after:
            conditions.append(Subscription.last_status_change >= expired_after.replace(tzinfo=None))
        if expired_before:
            conditions.append(Subscription.last_status_change <= expired_before.replace(tzinfo=None))

    if admin_id is not None:
        conditions.append(Subscription.admin_id == admin_id)

    return conditions


async def remove_expired_subscriptions(
    db: AsyncSession,
    expired_after: datetime | None = None,
    expired_before: datetime | None = None,
    admin_id: int | None = None,
    target: Literal["expired", "limited", "on_hold", "disabled", "inactive"] = "expired",
    dry_run: bool = False,
) -> tuple[list[str], list[Row]]:
    """
    Delete subscriptions matching a cleanup target.

    Returns UUIDs and deleted subscriptions still served by nodes, with their
    UUID, status and proxy settings for reliable access revocation.
    """
    conditions = _cleanup_target_subscription_conditions(expired_after, expired_before, admin_id, target)

    rows = (
        await db.execute(
            select(Subscription.uuid, Subscription.status, Subscription.proxy_settings).where(*conditions)
        )
    ).all()
    if not rows:
        return [], []

    uuids = [row.uuid for row in rows]

    if dry_run:
        return uuids, []

    subscription_uuids = [row.uuid for row in rows]

    for start in range(0, len(subscription_uuids), 1000):
        chunk = subscription_uuids[start : start + 1000]
        await release_allocations_by_subscription_uuids(db, chunk)
        await _delete_subscription_dependencies(db, chunk)
        await db.execute(delete(Subscription).where(Subscription.uuid.in_(chunk)))
    await db.commit()

    return uuids, [row for row in rows if row.status in (SubscriptionStatus.active, SubscriptionStatus.on_hold)]


async def get_active_to_expire_subscriptions(db: AsyncSession) -> list[Subscription]:
    stmt = (
        _review_subscription_select_stmt()
        .where(Subscription.status == SubscriptionStatus.active)
        .where(Subscription.is_expired)
    )

    return list((await db.execute(stmt)).unique().scalars().all())


async def get_active_to_limited_subscriptions(db: AsyncSession) -> list[Subscription]:
    stmt = (
        _review_subscription_select_stmt()
        .where(Subscription.status == SubscriptionStatus.active)
        .where(Subscription.is_limited)
    )

    return list((await db.execute(stmt)).unique().scalars().all())


async def get_on_hold_to_active_subscriptions(db: AsyncSession) -> list[Subscription]:
    stmt = (
        _review_subscription_select_stmt()
        .where(Subscription.status == SubscriptionStatus.on_hold)
        .where(Subscription.become_online)
        # Core-managed status is revision-fenced. The provider cannot activate
        # a paid subscription on a local on-hold timer or device connection.
        .where(false())
    )

    return list((await db.execute(stmt)).unique().scalars().all())


async def reload_review_subscriptions(db: AsyncSession, subscription_uuids: list[UUID]) -> None:
    """Load users picked by a review query again in place, e.g. after a rollback expired them."""
    if not subscription_uuids:
        return
    stmt = _review_subscription_select_stmt().where(_explicit_uuids(Subscription.uuid, subscription_uuids))
    (await db.execute(stmt.execution_options(populate_existing=True))).unique().scalars().all()


async def get_subscriptions_to_reset_data_usage(db: AsyncSession) -> list[Subscription]:
    """
    Retrieves users whose data usage needs to be reset based on their reset strategy.
    """
    last_reset_subq = (
        select(
            SubscriptionUsageResetLogs.subscription_uuid,
            func.max(SubscriptionUsageResetLogs.reset_at).label("last_reset_at"),
        )
        .group_by(SubscriptionUsageResetLogs.subscription_uuid)
        .subquery()
    )

    last_reset_time = coalesce(last_reset_subq.c.last_reset_at, Subscription.created_at)

    reset_strategy_to_days = {
        DataLimitResetStrategy.day: 1,
        DataLimitResetStrategy.week: 7,
        DataLimitResetStrategy.month: 30,
        DataLimitResetStrategy.year: 365,
    }

    num_days_to_reset_case = case(
        *(
            (Subscription.data_limit_reset_strategy == strategy, days)
            for strategy, days in reset_strategy_to_days.items()
        ),
        else_=None,
    )

    stmt = (
        _review_subscription_select_stmt()
        .outerjoin(last_reset_subq, Subscription.uuid == last_reset_subq.c.subscription_uuid)
        .where(
            Subscription.status.in_([SubscriptionStatus.active, SubscriptionStatus.limited]),
            Subscription.data_limit_reset_strategy != DataLimitResetStrategy.no_reset,
            false(),
            DateDiff(func.now(), last_reset_time) >= num_days_to_reset_case,
        )
    )

    return list((await db.execute(stmt)).unique().scalars().all())


async def get_usage_percentage_reached_subscriptions(db: AsyncSession, percentage: int) -> list[Subscription]:
    """
    Get active users who have reached or exceeded the specified usage percentage threshold
    and don't have an existing notification reminder for this threshold.
    """
    # Subquery to check for existing notification reminders
    existing_reminder_subq = (
        select(NotificationReminder.subscription_uuid)
        .where(
            NotificationReminder.subscription_uuid == Subscription.uuid,
            NotificationReminder.type == ReminderType.data_usage,
            NotificationReminder.threshold == percentage,
        )
        .exists()
    )

    stmt = (
        _review_subscription_select_stmt()
        .options(joinedload(Subscription.notification_reminders))
        .where(Subscription.status == SubscriptionStatus.active)
        .where(Subscription.usage_percentage >= percentage)
        .where(not_(existing_reminder_subq))  # Only users without existing reminders
    )

    return list((await db.execute(stmt)).unique().scalars().all())


async def get_days_left_reached_subscriptions(db: AsyncSession, days: int) -> list[Subscription]:
    """
    Get active users who have reached or exceeded the specified days left threshold
    and don't have an existing notification reminder for this threshold.
    """
    # Subquery to check for existing notification reminders
    existing_reminder_subq = (
        select(NotificationReminder.subscription_uuid)
        .where(
            NotificationReminder.subscription_uuid == Subscription.uuid,
            NotificationReminder.type == ReminderType.expiration_date,
            NotificationReminder.threshold == days,
        )
        .exists()
    )

    stmt = (
        _review_subscription_select_stmt()
        .options(joinedload(Subscription.notification_reminders))
        .where(Subscription.status == SubscriptionStatus.active)
        .where(Subscription.expire.isnot(None))
        .where(Subscription.days_left == days)
        .where(not_(existing_reminder_subq))  # Only users without existing reminders
    )

    return list((await db.execute(stmt)).unique().scalars().all())


async def get_subscription_usages(
    db: AsyncSession,
    subscription_uuid: UUID,
    start: datetime,
    end: datetime,
    period: Period,
    node_id: int | None = None,
    group_by_node: bool = False,
) -> SubscriptionUsageStatsList:
    """
    Retrieves user usages within a specified date range.
    Groups data by periods in the timezone of the start/end parameters.
    """
    # Build the appropriate truncation expression
    trunc_expr = _build_trunc_expression(db, period, NodeSubscriptionUsage.created_at, start)

    # Filter using UTC timestamps (DB stores naive UTC) from first complete bucket
    start_utc = get_complete_period_start_for_filter(start, period)
    end_utc = to_utc_for_filter(end)
    conditions = [
        NodeSubscriptionUsage.created_at >= start_utc,
        NodeSubscriptionUsage.created_at < end_utc,
        NodeSubscriptionUsage.subscription_uuid == subscription_uuid,
    ]

    if node_id is not None:
        conditions.append(NodeSubscriptionUsage.node_id == node_id)
    else:
        node_id = -1

    dialect = db.bind.dialect.name
    if group_by_node:
        stmt = (
            select(
                trunc_expr.label("period_start"),
                func.coalesce(NodeSubscriptionUsage.node_id, 0).label("node_id"),
                func.sum(NodeSubscriptionUsage.used_traffic).label("total_traffic"),
            )
            .where(and_(*conditions))
            .group_by(trunc_expr, NodeSubscriptionUsage.node_id)
            .order_by(trunc_expr)
        )

    else:
        stmt = (
            select(
                trunc_expr.label("period_start"), func.sum(NodeSubscriptionUsage.used_traffic).label("total_traffic")
            )
            .where(and_(*conditions))
            .group_by(trunc_expr)
            .order_by(trunc_expr)
        )

    result = await db.execute(stmt)

    stats = {}
    for row in result.mappings():
        row_dict = dict(row)
        node_id_val = row_dict.pop("node_id", node_id)

        # Attach timezone info to period_start
        attach_timezone_to_period_start(row_dict, start.tzinfo, dialect)

        if node_id_val not in stats:
            stats[node_id_val] = []
        stats[node_id_val].append(SubscriptionUsageStat(**row_dict))

    return SubscriptionUsageStatsList(period=period, start=start, end=end, stats=stats)


async def get_subscriptions_count_by_admin(db: AsyncSession, admin_id: int | None) -> int:
    """
    Gets the total count of users belonging to a specific admin.

    Args:
        db (AsyncSession): Database session.
        admin_id (int | None): Admin ID to filter by. If None, counts all users.

    Returns:
        int: Total count of users for the given admin.
    """
    stmt = select(func.count(Subscription.uuid))
    if admin_id is not None:
        stmt = stmt.where(Subscription.admin_id == admin_id)
    return (await db.execute(stmt)).scalar_one() or 0


async def lock_admin_quota_row(db: AsyncSession, admin_id: int) -> None:
    """Lock an admin row before quota-sensitive user creation.

    Call it before the transaction writes anything: the user count that follows must see the users
    committed by the previous holder of the lock.
    """
    dialect = db.bind.dialect.name
    if dialect == "sqlite":
        await db.execute(update(Admin).where(Admin.id == admin_id).values(id=Admin.id))
        return

    if dialect in ("mysql", "mariadb") and db.in_transaction():
        # REPEATABLE READ keeps the snapshot of the transaction's first read (authentication,
        # validation), so a count after the lock would miss users committed while waiting for it.
        # Nothing is written yet, so end that read-only transaction and let the count take a new one.
        await db.commit()
    await db.execute(select(Admin.id).where(Admin.id == admin_id).with_for_update())


async def get_subscriptions_by_uuids(db: AsyncSession, uuids: Sequence[UUID], *, load_admin_role: bool = False) -> list[Subscription]:
    """Retrieve subscriptions in the requested UUID order."""
    if not uuids:
        return []
    rows = await db.execute(
        _build_subscription_select_stmt(load_admin_role=load_admin_role).where(Subscription.uuid.in_(uuids))
    )
    by_uuid = {subscription.uuid: subscription for subscription in rows.unique().scalars().all()}
    return [by_uuid[uid] for uid in uuids if uid in by_uuid]


async def get_subscriptions_by_uuid_batch(
    db: AsyncSession,
    subscription_uuids: Sequence[UUID],
    *,
    load_admin_role: bool = False,
    load_group_inbounds: bool = False,
    load_usage_logs: bool = True,
    load_lifetime_used_traffic: bool = False,
    populate_existing: bool = False,
) -> list[Subscription]:
    """Load users in request order with optional history, aggregates and refreshed state."""
    if not subscription_uuids:
        return []

    stmt = _build_subscription_select_stmt(
        load_admin_role=load_admin_role,
        load_group_inbounds=load_group_inbounds,
        load_usage_logs=load_usage_logs,
        load_lifetime_used_traffic=load_lifetime_used_traffic,
    ).execution_options(populate_existing=populate_existing)
    result = await db.execute(stmt.where(_explicit_uuids(Subscription.uuid, subscription_uuids)))
    users_by_id = {user.uuid: user for user in result.unique().scalars().all()}
    return [users_by_id[subscription_uuid] for subscription_uuid in subscription_uuids if subscription_uuid in users_by_id]


async def get_subscriptions_count(db: AsyncSession, status: SubscriptionStatus = None, admin_id: int | None = None) -> int:
    """
    Gets the total count of users with optional filters.

    Args:
        db (AsyncSession): Database session.
        status (SubscriptionStatus, optional): Filter by user status.
        admin_id (int, optional): Filter by admin.
    Returns:
        int: Total count of users.
    """
    stmt = select(func.count(Subscription.uuid))

    filters = []
    if status:
        filters.append(Subscription.status == status)
    if admin_id:
        filters.append(Subscription.admin_id == admin_id)

    if filters:
        stmt = stmt.where(and_(*filters))

    result = await db.execute(stmt)
    return result.scalar()


def _build_subscription_count_metrics_query(
    statuses: list[SubscriptionStatus], online_since: datetime, admin_id: int | None = None
) -> Select:
    """Build one index-aware query for dashboard user counts."""
    if admin_id is not None:
        status_columns = [
            select(func.count(Subscription.uuid))
            .where(Subscription.admin_id == admin_id, Subscription.status == status)
            .scalar_subquery()
            .label(status.value)
            for status in statuses
        ]
        online_column = (
            select(func.count(Subscription.uuid))
            .where(
                Subscription.admin_id == admin_id,
                Subscription.online_at.isnot(None),
                Subscription.online_at >= online_since,
            )
            .scalar_subquery()
            .label("online")
        )
        return select(*status_columns, online_column)

    status_columns = [
        func.count(case((Subscription.status == status, Subscription.uuid))).label(status.value) for status in statuses
    ]
    online_column = func.count(
        case((and_(Subscription.online_at.isnot(None), Subscription.online_at >= online_since), Subscription.uuid))
    ).label("online")
    return select(*status_columns, online_column)


async def get_subscriptions_count_metrics(
    db: AsyncSession,
    statuses: list[SubscriptionStatus],
    online_window: timedelta,
    admin_id: int | None = None,
) -> tuple[dict[str, int], int]:
    """Return per-status, total, and recent-online user counts in one SELECT.

    The ``total`` value includes only the requested statuses. Pass every
    ``SubscriptionStatus`` value when a complete user count is required.
    """
    stmt = _build_subscription_count_metrics_query(statuses, datetime.now(UTC) - online_window, admin_id)
    row = (await db.execute(stmt)).one()

    status_counts = {status.value: int(getattr(row, status.value) or 0) for status in statuses}
    status_counts["total"] = sum(status_counts.values())
    return status_counts, int(row.online or 0)


def _subscription_uuid_for_username(username: str) -> UUID:
    # Core's managed technical identity already contains the canonical subscription UUID.
    # Other WayLane subscriptions receive an independent random UUID.
    if username.startswith("sub_"):
        try:
            return UUID(username[4:])
        except ValueError:
            pass
    return uuid4()


async def create_subscription(
    db: AsyncSession, new_user: SubscriptionCreate, groups: list[Group], admin: Admin, *, commit: bool = True
) -> Subscription:
    """
    Creates a new user.

    Args:
        db (AsyncSession): Database session.
        new_user (SubscriptionCreate): Subscription creation data.
        groups (list[Group]): Groups to assign to user.
        admin (Admin): Admin creating the user.

    Returns:
        Subscription: Created user object.
    """
    db_user = Subscription(
        uuid=new_user.uuid,
        **new_user.model_dump(exclude={"uuid", "group_ids", "expire", "proxy_settings", "next_plan", "on_hold_timeout"}),
    )
    db_user.admin = admin
    db_user.groups = groups
    db_user.expire = new_user.expire or None
    db_user.on_hold_timeout = new_user.on_hold_timeout or None

    if new_user.hwid_limit is not None:
        db_user.hwid_limit = new_user.hwid_limit

    db_user.proxy_settings = new_user.proxy_settings.dict()

    db.add(db_user)
    await db.flush()
    await sync_user_allocations(db, db_user, accessible_tags=await tags_from_groups(groups))

    if new_user.next_plan:
        db_user.next_plan = NextPlan(subscription_uuid=db_user.uuid, **new_user.next_plan.model_dump())
        db.add(db_user.next_plan)
    if commit:
        await db.commit()
        await refresh_and_load_subscription(db, db_user)
    return db_user


async def _delete_subscription_dependencies(db: AsyncSession, subscription_uuids: list[UUID]):
    """Remove all dependent rows referencing the given subscription UUIDs."""
    if not subscription_uuids:
        return

    await db.execute(
        subscriptions_groups_association.delete().where(
            _explicit_uuids(subscriptions_groups_association.c.subscription_uuid, subscription_uuids)
        )
    )


async def remove_subscription(db: AsyncSession, db_user: Subscription) -> Subscription:
    """
    Removes a user from the database.

    Args:
        db (AsyncSession): Database session.
        db_user (Subscription): Subscription to remove.

    Returns:
        Subscription: Removed user object.
    """
    await release_users_allocations(db, [db_user])
    await _delete_subscription_dependencies(db, [db_user.uuid])
    await db.execute(delete(Subscription).where(Subscription.uuid == db_user.uuid))
    await db.commit()
    return db_user


async def remove_subscriptions(db: AsyncSession, db_users: list[Subscription]):
    """
    Removes multiple users from the database.

    Args:
        db (AsyncSession): Database session.
        db_users (list[Subscription]): List of user objects to be removed.
    """
    if not db_users:
        return

    subscription_uuids = list({user.uuid for user in db_users})

    await release_users_allocations(db, db_users)
    await _delete_subscription_dependencies(db, subscription_uuids)
    await db.execute(delete(Subscription).where(_explicit_uuids(Subscription.uuid, subscription_uuids)))
    await db.commit()


async def modify_subscription(
    db: AsyncSession,
    db_user: Subscription,
    modify: SubscriptionModify,
    *,
    groups: list[Group] | None = None,
    commit: bool = True,
) -> Subscription:
    """
    Modify a user's information.

    Args:
        db (AsyncSession): Database session.
        dbuser (Subscription): Subscription to update.
        modify (SubscriptionModify): Modified user data.

    Returns:
        Subscription: Updated user object.
    """
    remove_usage_reminder = False
    remove_expiration_reminder = False

    if modify.proxy_settings is not None:
        db_user.proxy_settings = modify.proxy_settings.dict()
    if modify.group_ids is not None:
        db_user.groups = groups or await get_groups_by_ids(db, modify.group_ids, load_users=False, load_inbounds=True)
        await sync_user_allocations(db, db_user, accessible_tags=await tags_from_groups(db_user.groups))

    if modify.status is not None:
        if modify.status == SubscriptionStatus.active and db_user.status == SubscriptionStatus.disabled:
            db_user.status = _resolve_enabled_subscription_status(db_user)
        else:
            db_user.status = modify.status

    if modify.status is SubscriptionStatus.on_hold:
        db_user.expire = None
        remove_expiration_reminder = True

    elif modify.expire == 0:
        db_user.expire = None
        remove_expiration_reminder = True
        if db_user.status is SubscriptionStatus.expired:
            db_user.status = SubscriptionStatus.active

    elif modify.expire is not None:
        db_user.expire = modify.expire
        if db_user.status in [SubscriptionStatus.active, SubscriptionStatus.expired]:
            if not db_user.expire or db_user.expire.replace(tzinfo=UTC) > datetime.now(UTC):
                db_user.status = SubscriptionStatus.active

                remove_expiration_reminder = True
            else:
                db_user.status = SubscriptionStatus.expired

    if modify.data_limit is not None:
        db_user.data_limit = modify.data_limit or None
        if db_user.status not in [SubscriptionStatus.expired, SubscriptionStatus.disabled]:
            if not db_user.data_limit or db_user.used_traffic < db_user.data_limit:
                if db_user.status != SubscriptionStatus.on_hold:
                    db_user.status = SubscriptionStatus.active

                remove_usage_reminder = True
            else:
                db_user.status = SubscriptionStatus.limited

    if modify.note is not None:
        db_user.note = modify.note or None

    if modify.data_limit_reset_strategy is not None:
        db_user.data_limit_reset_strategy = modify.data_limit_reset_strategy

    if modify.on_hold_timeout == 0:
        db_user.on_hold_timeout = None
    elif modify.on_hold_timeout is not None:
        db_user.on_hold_timeout = modify.on_hold_timeout

    if modify.on_hold_expire_duration is not None:
        db_user.on_hold_expire_duration = modify.on_hold_expire_duration

    if modify.hwid_limit is not None:
        db_user.hwid_limit = modify.hwid_limit

    if "next_plan" in modify.model_fields_set:
        if modify.next_plan is not None:
            db_user.next_plan = NextPlan(
                subscription_uuid=db_user.uuid,
                subscription_template_id=modify.next_plan.subscription_template_id,
                data_limit=modify.next_plan.data_limit,
                expire=modify.next_plan.expire,
                add_remaining_traffic=modify.next_plan.add_remaining_traffic,
            )
        elif db_user.next_plan is not None:
            await db.delete(db_user.next_plan)

    db_user.edit_at = datetime.now(UTC)

    if remove_usage_reminder or remove_expiration_reminder:
        id = db_user.uuid
        usage_percentage = db_user.usage_percentage
        days_left = db_user.days_left

    if remove_usage_reminder:
        await delete_subscription_passed_notification_reminders(db, id, ReminderType.data_usage, usage_percentage)
    if remove_expiration_reminder:
        await delete_subscription_passed_notification_reminders(db, id, ReminderType.expiration_date, days_left)

    if commit:
        await db.commit()
        await refresh_and_load_subscription(db, db_user)
    return db_user


async def _reset_subscriptions_traffic_and_log(
    db: AsyncSession, subscription_uuids: Sequence[UUID], *, clean_chart_data: bool = False
) -> None:
    """Log and zero the users' traffic, drop their next plans and passed data-usage reminders, and reactivate
    limited users, with the same few statements for any number of users.

    The traffic is logged from the row as the reset writes it, not from the copy loaded before: record_usages keeps
    adding to `used_traffic` meanwhile, and logging a stale value then writing 0 drops that traffic from both the
    new period and the lifetime total. The rows are locked first (PostgreSQL/MySQL); on SQLite the INSERT is the
    first write, so it takes the write lock before it reads them."""
    if not subscription_uuids:
        return
    selected = _explicit_uuids(Subscription.uuid, subscription_uuids)
    await db.execute(select(Subscription.uuid).where(selected).order_by(Subscription.uuid).with_for_update())

    reset_at = bindparam("reset_at", datetime.now(UTC), type_=SubscriptionUsageResetLogs.reset_at.type)
    await db.execute(
        insert(SubscriptionUsageResetLogs.__table__).from_select(
            ["subscription_uuid", "used_traffic_at_reset", "reset_at"],
            select(Subscription.uuid, Subscription.used_traffic, reset_at).where(selected),
        )
    )
    reset_status = case(
        (Subscription.status == SubscriptionStatus.limited, SubscriptionStatus.active), else_=Subscription.status
    )
    await db.execute(
        update(Subscription)
        .where(selected)
        .values(used_traffic=0, status=reset_status)
        .execution_options(synchronize_session=False)
    )
    await db.execute(delete(NextPlan).where(_explicit_uuids(NextPlan.subscription_uuid, subscription_uuids)))
    await db.execute(
        delete(NotificationReminder).where(
            _explicit_uuids(NotificationReminder.subscription_uuid, subscription_uuids),
            NotificationReminder.type == ReminderType.data_usage,
            NotificationReminder.threshold > 0,
        )
    )
    if clean_chart_data:
        await db.execute(
            delete(NodeSubscriptionUsage).where(_explicit_uuids(NodeSubscriptionUsage.subscription_uuid, subscription_uuids))
        )


async def clear_subscription_node_usages(db: AsyncSession, subscription_uuid: UUID, *, before: datetime | None = None) -> None:
    stmt = delete(NodeSubscriptionUsage).where(NodeSubscriptionUsage.subscription_uuid == subscription_uuid)
    if before is not None:
        stmt = stmt.where(NodeSubscriptionUsage.created_at <= before)
    await db.execute(stmt)


async def reset_subscription_data_usage(
    db: AsyncSession, db_user: Subscription, *, clean_chart_data: bool = False, commit: bool = True
) -> Subscription:
    """
    Resets the data usage of a user and logs the reset.

    Args:
        db (AsyncSession): Database session.
        dbuser (Subscription): The user object whose data usage is to be reset.

    Returns:
        Subscription: The updated user object.
    """
    await _reset_subscriptions_traffic_and_log(db, [db_user.uuid], clean_chart_data=clean_chart_data)

    if commit:
        await db.commit()
        await refresh_and_load_subscription(db, db_user)
    else:
        # The reset ran in SQL: reload what it changed so the caller keeps editing an up-to-date user
        await db.refresh(db_user, attribute_names=["used_traffic", "status", "next_plan"])
    return db_user


async def bulk_reset_subscription_data_usage(
    db: AsyncSession, users: list[Subscription], *, clean_chart_data: bool = False, commit: bool = True
) -> list[Subscription]:
    """
    Resets the data usage for a list of users and logs the reset.

    The users are reloaded with `populate_existing`: with `commit=False`, pending in-memory changes on them are
    discarded, so flush them first.

    Args:
        db (AsyncSession): Database session.
        users (list[Subscription]): The list of user objects whose data usage is to be reset.

    Returns:
        list[Subscription]: The updated list of user objects.
    """
    subscription_uuids = [db_user.uuid for db_user in users]
    await _reset_subscriptions_traffic_and_log(db, subscription_uuids, clean_chart_data=clean_chart_data)
    if commit:
        await db.commit()
    # One reload for all users (with the node-sync relations), without materializing their reset history
    return await get_subscriptions_by_uuid_batch(
        db,
        subscription_uuids,
        load_admin_role=True,
        load_group_inbounds=True,
        load_usage_logs=False,
        load_lifetime_used_traffic=True,
        populate_existing=True,
    )


def build_revoked_proxy_settings(db_user: Subscription) -> dict:
    proxy_settings = ProxyTable()
    proxy_settings.shadowsocks.method = db_user.proxy_settings.get("shadowsocks", {}).get(
        "method", "chacha20-ietf-poly1305"
    )
    proxy_settings.wireguard.peer_ips = db_user.proxy_settings.get("wireguard", {}).get("peer_ips", []) or []
    return proxy_settings.dict()


async def reset_subscription_by_next(
    db: AsyncSession, db_user: Subscription, *, clean_chart_data: bool = False
) -> Subscription:
    """
    Resets the data usage of a user based on next user.

    Args:
        db (AsyncSession): Database session.
        dbuser (Subscription): The user object whose data usage is to be reset.

    Returns:
        Subscription: The updated user object.
    """
    now = datetime.now(UTC)
    db_user.status = SubscriptionStatus.active
    db_user.on_hold_expire_duration = None
    db_user.on_hold_timeout = None
    db_user.edit_at = now
    remaining_traffic = (db_user.data_limit or 0) - db_user.used_traffic
    if db_user.next_plan.subscription_template_id is None:
        db_user.data_limit = db_user.next_plan.data_limit + (
            0 if not db_user.next_plan.add_remaining_traffic else remaining_traffic
        )
        db_user.expire = now + timedelta(seconds=db_user.next_plan.expire) if db_user.next_plan.expire else None
    else:
        await db_user.next_plan.awaitable_attrs.subscription_template
        await db_user.next_plan.subscription_template.awaitable_attrs.groups
        db_user.groups = db_user.next_plan.subscription_template.groups
        await sync_user_allocations(db, db_user, accessible_tags=await tags_from_groups(db_user.groups))
        db_user.data_limit = db_user.next_plan.subscription_template.data_limit + (
            0 if not db_user.next_plan.add_remaining_traffic else remaining_traffic
        )
        if db_user.next_plan.subscription_template.status is SubscriptionStatusCreate.on_hold:
            db_user.status = SubscriptionStatus.on_hold
            db_user.on_hold_expire_duration = db_user.next_plan.subscription_template.expire_duration
            db_user.on_hold_timeout = (
                now + timedelta(seconds=db_user.next_plan.subscription_template.on_hold_timeout)
                if db_user.next_plan.subscription_template.on_hold_timeout
                else None
            )
            # Only connections after renewal may start this plan.
            db_user.online_at = None
            db_user.expire = None
        else:
            db_user.expire = (
                now + timedelta(seconds=db_user.next_plan.subscription_template.expire_duration)
                if db_user.next_plan.subscription_template.expire_duration
                else None
            )

        if db_user.next_plan.subscription_template.extra_settings:
            proxy_settings = deepcopy(db_user.proxy_settings)
            proxy_settings["shadowsocks"]["method"] = (
                db_user.next_plan.subscription_template.extra_settings["method"]
                if db_user.next_plan.subscription_template.extra_settings["method"]
                else "chacha20-ietf-poly1305"
            )
            db_user.proxy_settings = proxy_settings
        db_user.data_limit_reset_strategy = db_user.next_plan.subscription_template.data_limit_reset_strategy

    await _reset_subscriptions_traffic_and_log(db, [db_user.uuid], clean_chart_data=clean_chart_data)
    await db.commit()
    await refresh_and_load_subscription(db, db_user)
    return db_user


async def revoke_subscription_sub(
    db: AsyncSession, db_user: Subscription, *, proxy_settings: dict | None = None
) -> Subscription:
    """
    Revokes the subscription of a user and updates proxies settings.

    Args:
        db (AsyncSession): Database session.
        db_user (Subscription): The user object whose subscription is to be revoked.

    Returns:
        Subscription: The updated user object.
    """
    db_user.sub_revoked_at = datetime.now(UTC)
    db_user.access_generation += 1
    db_user.proxy_settings = proxy_settings if proxy_settings is not None else build_revoked_proxy_settings(db_user)
    await db.commit()
    await refresh_and_load_subscription(db, db_user)
    return db_user


async def bulk_revoke_subscription_sub(
    db: AsyncSession, users: list[Subscription], *, proxy_settings_by_subscription_uuid: dict[UUID, dict] | None = None
) -> list[Subscription]:
    """
    Revoke subscriptions for multiple users in a single transaction.

    Args:
        db (AsyncSession): Database session.
        users (list[Subscription]): Users whose subscriptions should be revoked.

    Returns:
        list[Subscription]: The refreshed users.
    """
    revoked_at = datetime.now(UTC)
    for user in users:
        user.sub_revoked_at = revoked_at
        user.access_generation += 1
        user.proxy_settings = (
            proxy_settings_by_subscription_uuid.get(user.uuid)
            if proxy_settings_by_subscription_uuid is not None and user.uuid in proxy_settings_by_subscription_uuid
            else build_revoked_proxy_settings(user)
        )

    await db.commit()
    for user in users:
        await refresh_and_load_subscription(db, user, load_admin_role=True)
    return users


@asynccontextmanager
async def _subscription_update_read_session(db: AsyncSession) -> AsyncIterator[AsyncSession]:
    """Read committed updates without reusing the caller's pre-flush snapshot."""
    await flush_user_sub_updates()
    # MySQL/MariaDB REPEATABLE READ may already have a snapshot from the
    # authorization/user lookup. A separate session sees the flushed rows
    # without committing or rolling back any work owned by the caller.
    async with AsyncSession(bind=db.bind, expire_on_commit=False) as read_db:
        yield read_db


async def subscription_update(
    db: AsyncSession, subscription_uuid: UUID, user_agent: str, ip: str | None = None, hwid: str | None = None
) -> None:
    """
    Queue a subscription-update row. The request session is unused; writes are
    flushed in the background so the public /sub path stays read-mostly.

    Args:
        db (AsyncSession): Database session (unused; kept for call-site compatibility).
        subscription_uuid (UUID): Canonical subscription UUIDv4 whose update is queued.
        user_agent (str): The user agent string.
        ip (str | None): The client IP address.
        hwid (str | None): The hardware ID of the client.
    """
    _ = db
    await queue_user_sub_update(subscription_uuid, user_agent, ip=ip, hwid=hwid)


async def get_subscriptions_sub_update_list(
    db: AsyncSession, subscription_uuid: UUID, offset: int = 0, limit: int = 10
) -> tuple[Sequence[SubscriptionUpdate], int]:
    stmt = (
        select(SubscriptionUpdate)
        .where(SubscriptionUpdate.subscription_uuid == subscription_uuid)
        .order_by(desc(SubscriptionUpdate.created_at))
    )

    async with _subscription_update_read_session(db) as read_db:
        result = await read_db.execute(select(func.count()).select_from(stmt.subquery()))
        count = result.scalar() or 0

        if offset:
            stmt = stmt.offset(offset)
        if limit:
            stmt = stmt.limit(limit)

        result = (await read_db.execute(stmt)).unique().scalars().all()

    return result, count


def _subscription_update_from_clause(
    subscription_uuid: UUID | None = None,
    admin_id: int | None = None,
):
    conditions = []
    if subscription_uuid is not None:
        conditions.append(SubscriptionUpdate.subscription_uuid == subscription_uuid)
        from_clause = SubscriptionUpdate.__table__
    elif admin_id is not None:
        from_clause = SubscriptionUpdate.__table__.join(
            Subscription, SubscriptionUpdate.subscription_uuid == Subscription.uuid
        )
        conditions.append(Subscription.admin_id == admin_id)
    else:
        # The owner-wide chart does not read any users columns. Keeping this
        # history-only lets the created_at index drive the global time range.
        from_clause = SubscriptionUpdate.__table__
    return from_clause, conditions


async def get_subscriptions_agent_counts(
    db: AsyncSession,
    subscription_uuid: UUID | None = None,
    admin_id: int | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    period: Period | None = None,
) -> list[tuple[str, int]]:
    stmt = select(SubscriptionUpdate.user_agent, func.count().label("count"))
    from_clause, conditions = _subscription_update_from_clause(subscription_uuid=subscription_uuid, admin_id=admin_id)

    if start is not None:
        start_utc = (
            get_complete_period_start_for_filter(start, period) if period is not None else to_utc_for_filter(start)
        )
        conditions.append(SubscriptionUpdate.created_at >= start_utc)
    if end is not None:
        conditions.append(SubscriptionUpdate.created_at < to_utc_for_filter(end))

    stmt = stmt.select_from(from_clause)
    if conditions:
        stmt = stmt.where(and_(*conditions))
    stmt = stmt.group_by(SubscriptionUpdate.user_agent)

    async with _subscription_update_read_session(db) as read_db:
        result = await read_db.execute(stmt)
    return [(agent, count) for agent, count in result.all()]


async def get_subscriptions_agent_stats(
    db: AsyncSession,
    start: datetime,
    end: datetime,
    period: Period = Period.hour,
    subscription_uuid: UUID | None = None,
    admin_id: int | None = None,
) -> list[dict]:
    """Retrieve subscription update counts grouped by agent and period."""
    trunc_expr = _build_trunc_expression(db, period, SubscriptionUpdate.created_at, start)
    start_utc = get_complete_period_start_for_filter(start, period)
    end_utc = to_utc_for_filter(end)
    from_clause, conditions = _subscription_update_from_clause(subscription_uuid=subscription_uuid, admin_id=admin_id)
    conditions.extend(
        [
            SubscriptionUpdate.created_at >= start_utc,
            SubscriptionUpdate.created_at < end_utc,
        ]
    )

    stmt = (
        select(
            trunc_expr.label("period_start"),
            SubscriptionUpdate.user_agent.label("agent"),
            func.count().label("count"),
        )
        .select_from(from_clause)
        .where(and_(*conditions))
        .group_by(trunc_expr, SubscriptionUpdate.user_agent)
        .order_by(trunc_expr)
    )

    async with _subscription_update_read_session(db) as read_db:
        result = await read_db.execute(stmt)
    dialect = db.bind.dialect.name
    rows = []
    for row in result.mappings():
        row_dict = dict(row)
        attach_timezone_to_period_start(row_dict, start.tzinfo, dialect)
        rows.append(row_dict)
    return rows


async def autodelete_expired_subscriptions(
    db: AsyncSession, include_limited_users: bool = False
) -> list[SubscriptionNotificationResponse]:
    """
    Deletes expired (optionally also limited) users whose auto-delete time has passed.

    Args:
        db (AsyncSession): Database session
        include_limited_users (bool, optional): Whether to delete limited users as well.
            Defaults to False.

    Returns:
        list[SubscriptionNotificationResponse]: List of deleted users.
    """
    target_status = (
        [SubscriptionStatus.expired]
        if not include_limited_users
        else [SubscriptionStatus.expired, SubscriptionStatus.limited]
    )

    auto_delete = func.coalesce(Subscription.auto_delete_in_days, literal(user_cleanup_settings.autodelete_days))

    query = (
        select(
            Subscription,
            auto_delete,  # Use global auto-delete days as fallback
        )
        .where(
            auto_delete >= 0,  # Negative values prevent auto-deletion
            Subscription.status.in_(target_status),
        )
        .options(joinedload(Subscription.admin))
    )

    expired_users = [
        user
        for (user, auto_delete) in (await db.execute(query)).unique()
        if user.last_status_change.replace(tzinfo=UTC) + timedelta(days=auto_delete) <= datetime.now(UTC)
    ]

    result: list[SubscriptionNotificationResponse] = []
    if expired_users:
        for user in expired_users:
            await load_subscription_attrs(user)
            result.append(SubscriptionNotificationResponse.model_validate(user))

        await remove_subscriptions(db, expired_users)

    return result


async def get_all_subscriptions_usages(
    db: AsyncSession,
    admins: Sequence[str] | None,
    start: datetime,
    end: datetime,
    period: Period = Period.hour,
    node_id: int | None = None,
    group_by_node: bool = False,
    core_id: int | None = None,
    group_by_admin: bool = False,
) -> SubscriptionUsageStatsList:
    """
    Retrieves aggregated usage data for all users of an admin within a specified time range,
    grouped by the specified time period.
    Groups data by periods in the timezone of the start/end parameters.

    Args:
        db (AsyncSession): Database session for querying.
        admins (Sequence[str] | None): Admin usernames to filter users by. If None/empty, include all admins.
        start (datetime): Start of the period (with timezone).
        end (datetime): End of the period (with timezone).
        period (Period): Time period to group by ('minute', 'hour', 'day', 'month').
        node_id (Optional[int]): Filter results by specific node ID if provided
        group_by_node (bool): Whether to group results by node.
        core_id (Optional[int]): Filter results by the nodes of a specific core config if provided
        group_by_admin (bool): Whether to group results by the users' admin (0 for users without admin).

    Returns:
        SubscriptionUsageStatsList: Aggregated usage data for each period.
    """
    admins_filter = admins or None

    # Build the appropriate truncation expression
    trunc_expr = _build_trunc_expression(db, period, NodeSubscriptionUsage.created_at, start)

    # Filter using UTC timestamps (DB stores naive UTC) from first complete bucket
    start_utc = get_complete_period_start_for_filter(start, period)
    end_utc = to_utc_for_filter(end)
    conditions = [
        NodeSubscriptionUsage.created_at >= start_utc,
        NodeSubscriptionUsage.created_at < end_utc,
    ]
    if admins_filter:
        conditions.append(Admin.username.in_(admins_filter))

    if core_id is not None:
        conditions.append(NodeSubscriptionUsage.node_id.in_(select(Node.id).where(Node.core_config_id == core_id)))

    if node_id is not None:
        conditions.append(NodeSubscriptionUsage.node_id == node_id)
    else:
        node_id = -1

    dialect = db.bind.dialect.name
    from_clause = NodeSubscriptionUsage.__table__
    if _usage_history_needs_subscriptions_join(dialect, admins_filter, group_by_admin):
        from_clause = from_clause.join(Subscription, Subscription.uuid == NodeSubscriptionUsage.subscription_uuid)
    if admins_filter:
        from_clause = from_clause.join(Admin, Admin.id == Subscription.admin_id)

    if group_by_node:
        stmt = (
            select(
                trunc_expr.label("period_start"),
                func.coalesce(NodeSubscriptionUsage.node_id, 0).label("node_id"),
                func.sum(NodeSubscriptionUsage.used_traffic).label("total_traffic"),
            )
            .select_from(from_clause)
            .where(and_(*conditions))
            .group_by(trunc_expr, NodeSubscriptionUsage.node_id)
            .order_by(trunc_expr)
        )
    elif group_by_admin:
        stmt = (
            select(
                trunc_expr.label("period_start"),
                func.coalesce(Subscription.admin_id, 0).label("admin_id"),
                func.sum(NodeSubscriptionUsage.used_traffic).label("total_traffic"),
            )
            .select_from(from_clause)
            .where(and_(*conditions))
            .group_by(trunc_expr, Subscription.admin_id)
            .order_by(trunc_expr)
        )
    else:
        stmt = (
            select(
                trunc_expr.label("period_start"), func.sum(NodeSubscriptionUsage.used_traffic).label("total_traffic")
            )
            .select_from(from_clause)
            .where(and_(*conditions))
            .group_by(trunc_expr)
            .order_by(trunc_expr)
        )

    result = await db.execute(stmt)

    stats = {}
    for row in result.mappings():
        row_dict = dict(row)
        node_id_val = row_dict.pop("node_id", node_id)
        # Stats are keyed by admin id instead of node id when grouped by admin
        stats_key = row_dict.pop("admin_id", node_id_val)

        # Attach timezone info to period_start
        attach_timezone_to_period_start(row_dict, start.tzinfo, dialect)

        if stats_key not in stats:
            stats[stats_key] = []
        stats[stats_key].append(SubscriptionUsageStat(**row_dict))

    return SubscriptionUsageStatsList(period=period, start=start, end=end, stats=stats)


def _usage_history_needs_subscriptions_join(dialect: str, admins_filter: Sequence[str] | None, group_by_admin: bool) -> bool:
    # PostgreSQL, MySQL and MariaDB delete a user's history rows through ON DELETE CASCADE, so joining every row to
    # users only matters for admin scoping. SQLite does not enforce foreign keys and keeps orphan rows to filter out.
    return bool(admins_filter) or group_by_admin or dialect == "sqlite"


async def get_subscription_count_metric_stats(
    db: AsyncSession,
    admins: Sequence[str] | None,
    start: datetime,
    end: datetime,
    period: Period = Period.hour,
    metric: SubscriptionCountMetric = SubscriptionCountMetric.online,
    node_id: int | None = None,
    group_by_node: bool = False,
) -> SubscriptionCountMetricStatsList:
    """Retrieves one distinct user count metric from node_subscription_usages."""
    validate_user_count_metric_scope(metric, node_id=node_id, group_by_node=group_by_node)

    query_parts = _build_subscription_count_query_parts(db, admins, start, end, period, metric, node_id)
    count_expr = _build_subscription_count_metric_expression(metric).label("count")
    total_stmt = select(count_expr).select_from(query_parts["from_clause"]).where(and_(*query_parts["conditions"]))

    if _subscription_count_buckets_from_distinct_pairs(query_parts["dialect"]):
        stmt = _build_subscription_count_buckets_from_distinct_pairs(query_parts, metric, group_by_node)
    elif group_by_node:
        stmt = (
            select(
                query_parts["trunc_expr"].label("period_start"),
                func.coalesce(NodeSubscriptionUsage.node_id, 0).label("node_id"),
                count_expr,
            )
            .select_from(query_parts["from_clause"])
            .where(and_(*query_parts["conditions"]))
            .group_by(query_parts["trunc_expr"], NodeSubscriptionUsage.node_id)
            .order_by(query_parts["trunc_expr"], NodeSubscriptionUsage.node_id)
        )
    else:
        stmt = (
            select(query_parts["trunc_expr"].label("period_start"), count_expr)
            .select_from(query_parts["from_clause"])
            .where(and_(*query_parts["conditions"]))
            .group_by(query_parts["trunc_expr"])
            .order_by(query_parts["trunc_expr"])
        )

    total_result = await db.execute(total_stmt)
    result = await db.execute(stmt)
    count_during_period = total_result.scalar_one() or 0

    stats = {}
    for row in result.mappings():
        row_dict = dict(row)
        node_id_val = row_dict.pop("node_id", query_parts["stats_key"])

        attach_timezone_to_period_start(row_dict, start.tzinfo, query_parts["dialect"])

        if node_id_val not in stats:
            stats[node_id_val] = []
        stats[node_id_val].append(SubscriptionCountMetricStat(**row_dict))

    return SubscriptionCountMetricStatsList(
        metric=metric,
        period=period,
        start=start,
        end=end,
        count_during_period=count_during_period,
        stats=stats,
    )


def _build_subscription_count_query_parts(
    db: AsyncSession,
    admins: Sequence[str] | None,
    start: datetime,
    end: datetime,
    period: Period,
    metric: SubscriptionCountMetric,
    node_id: int | None,
) -> dict:
    admins_filter = admins or None
    trunc_expr = _build_trunc_expression(db, period, NodeSubscriptionUsage.created_at, start)
    start_utc = get_complete_period_start_for_filter(start, period)
    end_utc = to_utc_for_filter(end)
    conditions = [
        NodeSubscriptionUsage.created_at >= start_utc,
        NodeSubscriptionUsage.created_at < end_utc,
    ]

    if admins_filter:
        conditions.append(Admin.username.in_(admins_filter))

    stats_key = node_id
    if node_id is not None:
        conditions.append(NodeSubscriptionUsage.node_id == node_id)
    else:
        stats_key = -1

    # Online counts only read node_subscription_usages. Avoid joining every history row
    # to users unless a status metric or an admin scope actually needs it.
    from_clause = NodeSubscriptionUsage.__table__
    if metric != SubscriptionCountMetric.online or admins_filter:
        from_clause = from_clause.join(Subscription, Subscription.uuid == NodeSubscriptionUsage.subscription_uuid)
    if admins_filter:
        from_clause = from_clause.join(Admin, Admin.id == Subscription.admin_id)

    return {
        "conditions": conditions,
        "dialect": db.bind.dialect.name,
        "from_clause": from_clause,
        "stats_key": stats_key,
        "trunc_expr": trunc_expr,
    }


def _build_subscription_count_metric_expression(metric: SubscriptionCountMetric):
    return func.count(func.distinct(_subscription_count_metric_subscription_uuid(metric)))


def _subscription_count_metric_subscription_uuid(metric: SubscriptionCountMetric):
    """The subscription UUID counted by this metric, NULL for non-matching rows."""
    if metric == SubscriptionCountMetric.online:
        return NodeSubscriptionUsage.subscription_uuid
    if metric == SubscriptionCountMetric.expired:
        return case(
            (Subscription.status == SubscriptionStatus.expired, NodeSubscriptionUsage.subscription_uuid), else_=None
        )
    if metric == SubscriptionCountMetric.limited:
        return case(
            (Subscription.status == SubscriptionStatus.limited, NodeSubscriptionUsage.subscription_uuid), else_=None
        )
    raise ValueError(f"Unsupported user count metric: {metric}")


def _subscription_count_buckets_from_distinct_pairs(dialect: str) -> bool:
    # PostgreSQL cannot hash count(DISTINCT ...), so a per-bucket count sorts every history row in range.
    # MySQL and MariaDB are faster with plain count(DISTINCT ...).
    return dialect == "postgresql"


def _build_subscription_count_buckets_from_distinct_pairs(
    query_parts: dict, metric: SubscriptionCountMetric, group_by_node: bool
):
    """Per-bucket distinct user counts as a count over DISTINCT (bucket, [node,] user) rows.

    Same result as count(DISTINCT ...) per bucket: a NULL subscription UUID (a row the metric does not count) is kept as
    one pair, so its bucket still appears, and count() skips it.
    """
    pair_columns = [query_parts["trunc_expr"].label("period_start")]
    if group_by_node:
        pair_columns.append(NodeSubscriptionUsage.node_id.label("node_id"))
    pairs = (
        select(*pair_columns, _subscription_count_metric_subscription_uuid(metric).label("subscription_uuid"))
        .select_from(query_parts["from_clause"])
        .where(and_(*query_parts["conditions"]))
        .distinct()
        .subquery()
    )

    group_columns = [pairs.c.period_start]
    output_columns = [pairs.c.period_start]
    if group_by_node:
        group_columns.append(pairs.c.node_id)
        output_columns.append(func.coalesce(pairs.c.node_id, 0).label("node_id"))
    return (
        select(*output_columns, func.count(pairs.c.subscription_uuid).label("count"))
        .group_by(*group_columns)
        .order_by(*group_columns)
    )


async def update_subscriptions_status(
    db: AsyncSession, users: list[Subscription], status: SubscriptionStatus
) -> list[Subscription]:
    """
    Updates a users status and records the time of change.

    Args:
        db (AsyncSession): Database session.
        users list[Subscription]: The users list to update.
        status (SubscriptionStatus): The new status.

    Returns:
        list[Subscription]: The updated user objects.
    """
    if not users:
        return []

    subscription_uuids = [user.uuid for user in users]
    changed_at = datetime.now(UTC)
    reseted_usage = _snapshot_reseted_usage(users)
    stmt = (
        update(Subscription)
        .where(Subscription.uuid.in_(subscription_uuids))
        .values(status=status, last_status_change=changed_at)
        .execution_options(synchronize_session=False)
    )
    await db.execute(stmt)
    await db.commit()

    # These users were eager-loaded by the scheduled status queries. Keep their
    # relationships intact instead of refreshing every user after the bulk update.
    for user in users:
        user.status = status
        user.last_status_change = changed_at
    _restore_reseted_usage(users, reseted_usage)
    return users


async def set_owner(db: AsyncSession, db_user: Subscription, admin: Admin) -> Subscription:
    """
    Sets the owner (admin) of a user.

    Args:
        db (AsyncSession): Database session.
        db_user (Subscription): The user object whose owner is to be set.
        admin (Admin): The admin to set as owner.

    Returns:
        Subscription: The updated user object.
    """
    old_admin = db_user.admin
    db_user.admin = admin

    # Update admin traffic counters
    if old_admin and old_admin.id != admin.id:
        old_admin.used_traffic -= db_user.used_traffic
        admin.used_traffic += db_user.used_traffic

    await db.commit()
    await refresh_and_load_subscription(db, db_user)
    return db_user


async def bulk_set_owner(db: AsyncSession, users: list[Subscription], admin: Admin) -> list[Subscription]:
    """
    Set the same owner for multiple users in a single transaction.

    Args:
        db (AsyncSession): Database session.
        users (list[Subscription]): Users to update.
        admin (Admin): Admin that should become the owner.

    Returns:
        list[Subscription]: The refreshed users.
    """
    # Group users by old admin to update traffic counters
    admin_traffic_changes = {}
    total_traffic_to_add = 0

    for user in users:
        old_admin = user.admin
        # Only traffic that moves from another admin counts, like set_owner.
        if old_admin and old_admin.id != admin.id:
            if old_admin.id not in admin_traffic_changes:
                admin_traffic_changes[old_admin.id] = 0
            admin_traffic_changes[old_admin.id] -= user.used_traffic
            total_traffic_to_add += user.used_traffic
        user.admin = admin

    # Update old admins' traffic
    for admin_id, traffic_change in admin_traffic_changes.items():
        await db.execute(
            update(Admin).where(Admin.id == admin_id).values(used_traffic=Admin.used_traffic + traffic_change)
        )

    # Update new admin's traffic
    if total_traffic_to_add > 0:
        await db.execute(
            update(Admin).where(Admin.id == admin.id).values(used_traffic=Admin.used_traffic + total_traffic_to_add)
        )

    await db.commit()
    for user in users:
        await refresh_and_load_subscription(db, user)
    return users


async def start_subscriptions_expire(db: AsyncSession, users: list[Subscription]) -> list[Subscription]:
    """
    Starts the expiration timer for a user.

    Args:
        db (AsyncSession): Database session.
        users list[Subscription]: The users list whose expiration timer is to be started.

    Returns:
        list[Subscription]: The updated users list.
    """
    if not users:
        return []

    now = datetime.now(UTC)
    params = []
    # Core UPDATE expires mapped state, including query_expression. Keep the
    # already-loaded lifetime aggregate so later pydantic validation stays async-safe.
    reseted_usage = _snapshot_reseted_usage(users)
    for user in users:
        duration = _safe_on_hold_expire_duration(user.on_hold_expire_duration)
        expire_time = now + timedelta(seconds=duration) if duration is not None else None
        user.expire = expire_time
        user.on_hold_expire_duration = None
        user.on_hold_timeout = None
        user.status = SubscriptionStatus.active
        params.append({"u_id": user.uuid, "u_expire": expire_time})

    await db.execute(
        update(Subscription.__table__)
        .where(Subscription.__table__.c.uuid == bindparam("u_id"))
        .values(
            expire=bindparam("u_expire"),
            on_hold_expire_duration=None,
            on_hold_timeout=None,
            status=SubscriptionStatus.active,
        )
        .execution_options(synchronize_session=False),
        params,
    )
    await db.commit()
    _restore_reseted_usage(users, reseted_usage)
    return users


async def create_notification_reminder(
    db: AsyncSession,
    reminder_type: ReminderType,
    expires_at: datetime,
    subscription_uuid: UUID,
    threshold: int | None = None,
) -> NotificationReminder:
    """
    Creates a new notification reminder.

    Args:
        db (AsyncSession): The database session.
        reminder_type (ReminderType): The type of reminder.
        expires_at (datetime): The expiration time of the reminder.
        subscription_uuid (UUID): Canonical subscription UUIDv4.
        threshold (Optional[int]): The threshold value to check for (e.g., days left or usage percent).

    Returns:
        NotificationReminder: The newly created NotificationReminder object.
    """
    reminder = NotificationReminder(type=reminder_type, expires_at=expires_at, subscription_uuid=subscription_uuid)
    if threshold is not None:
        reminder.threshold = threshold
    db.add(reminder)
    await db.commit()
    return reminder


async def bulk_create_notification_reminders(db: AsyncSession, reminder_data: list[dict]) -> None:
    """
    Bulk creates notification reminders.

    Args:
        db (AsyncSession): The database session.
        reminder_data (List[dict]): List of reminder dictionaries with keys: subscription_uuid, type, threshold, expires_at
    """
    if not reminder_data:
        return

    reminders = []
    for data in reminder_data:
        reminder = NotificationReminder(
            type=data["type"],
            expires_at=data["expires_at"],
            subscription_uuid=data["subscription_uuid"],
            threshold=data.get("threshold"),
        )
        reminders.append(reminder)

    db.add_all(reminders)
    await db.commit()


async def delete_subscription_passed_notification_reminders(
    db: AsyncSession, subscription_uuid: UUID, type: ReminderType, threshold: int
) -> None:
    """
    Deletes user reminders passed.

    Args:
        db (AsyncSession): The database session.
        subscription_uuid (UUID): Canonical subscription UUIDv4.
        reminder_type (ReminderType): The type of reminder to delete.
        threshold (int): The threshold to delete (e.g., days left or usage percent).
    """
    conditions = [NotificationReminder.subscription_uuid == subscription_uuid, NotificationReminder.type == type]

    if type == ReminderType.data_usage:
        conditions.append(NotificationReminder.threshold > threshold)
    if type == ReminderType.expiration_date:
        conditions.append(NotificationReminder.threshold < threshold)

    stmt = delete(NotificationReminder).where(and_(*conditions))
    await db.execute(stmt)
