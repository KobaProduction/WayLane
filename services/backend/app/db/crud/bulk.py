from collections.abc import Iterator, Sequence
from datetime import UTC, datetime as dt
from typing import Literal
from uuid import UUID

from sqlalchemy import and_, case, cast, delete, false, func, or_, select, text, true, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.db.models import (
    Admin,
    Group,
    NextPlan,
    NodeSubscriptionUsage,
    Subscription,
    SubscriptionStatus,
    SubscriptionUsageResetLogs,
)
from app.models.subscription_models import BulkSubscription, BulkSubscriptionFilter, BulkSubscriptionsProxy

from .general import get_datetime_add_expression
from .subscription import _build_subscription_select_stmt, _explicit_uuids


class BulkSubscriptionUpdateConflict(ValueError):
    """A checked target changed before the conditional bulk update completed."""


def _bulk_user_target_filter(
    bulk_model: BulkSubscription, field: Literal["expire", "data_limit"], target_ids: Sequence[UUID] | None = None
):
    """Select exactly the finite values that the requested bulk adjustment can change."""
    selection = _create_final_filter(bulk_model) if target_ids is None else _explicit_uuids(Subscription.uuid, target_ids)
    if field == "expire":
        return and_(selection, Subscription.expire.isnot(None))
    conditions = [selection, Subscription.data_limit.isnot(None), Subscription.data_limit != 0]
    if bulk_model.amount < 0:
        conditions.append(Subscription.data_limit > -bulk_model.amount)
    return and_(*conditions)


async def get_bulk_user_limit_targets(
    db: AsyncSession, bulk_model: BulkSubscription, field: Literal["expire", "data_limit"], *, lock: bool = True
):
    """Read only IDs and values for limit validation, locking targets before a write."""
    column = Subscription.expire if field == "expire" else Subscription.data_limit
    stmt = select(Subscription.uuid, column).where(_bulk_user_target_filter(bulk_model, field))
    if lock:
        stmt = stmt.with_for_update(of=Subscription)
    result = await db.execute(stmt)
    return result.all()


def _bulk_final_value_bounds(value, minimum, maximum):
    """Constrain final values again in the write to protect against concurrent changes."""
    conditions = []
    if minimum is not None:
        conditions.append(value >= minimum)
    if maximum is not None:
        conditions.append(value <= maximum)
    return and_(*conditions) if conditions else true()


async def _check_bulk_update_count(db: AsyncSession, result, target_ids: Sequence[UUID] | None) -> None:
    """Roll back the whole batch if any validated target no longer meets the write guards."""
    if target_ids is not None and result.rowcount != len(target_ids):
        await db.rollback()
        raise BulkSubscriptionUpdateConflict("Selected users changed during the bulk update; retry the request")


def _bulk_user_reload_stmt(*, load_group_inbounds: bool = False) -> Select:
    """Reload bulk results with batched relationships and aggregate lifetime usage."""
    # Bulk SQL bypasses relationship collections; replace any cached memberships.
    return _build_subscription_select_stmt(
        load_admin_role=True,
        load_usage_logs=False,
        load_group_inbounds=load_group_inbounds,
        load_lifetime_used_traffic=True,
    ).execution_options(populate_existing=True)


def _chunked(ids: Sequence[UUID]) -> Iterator[Sequence[UUID]]:
    """Yield ids 5k at a time. asyncpg rejects statements with more than 32767 bind parameters, and these
    statements bind group IDs next to subscription UUIDs, so stay well below 10k."""
    for start in range(0, len(ids), 5_000):
        yield ids[start : start + 5_000]


async def _reload_subscriptions_with_inbounds(db: AsyncSession, subscription_uuids: Sequence[UUID]) -> list[Subscription]:
    """Reload technical subscriptions after bulk changes, eager-loading node inbounds."""
    subscriptions = []
    for chunk in _chunked(subscription_uuids):
        result = await db.execute(_bulk_user_reload_stmt(load_group_inbounds=True).where(Subscription.uuid.in_(chunk)))
        subscriptions += result.scalars().all()
    return subscriptions


async def reset_all_subscriptions_data_usage(
    db: AsyncSession,
    admin: Admin | None = None,
    *,
    clean_chart_data: bool = False,
):
    """
    Efficiently resets data usage for all users, or users under a specific admin if provided.

    This function performs a high-performance reset by executing bulk database operations
    rather than iterating over ORM-mapped objects, improving speed and reducing memory usage.

    Operations performed:
        - Sets `used_traffic` to 0 for all target users.
        - Sets `status` to `active` for all users, unless filtered by admin.
        - Deletes all related `SubscriptionUsageResetLogs` and `NextPlan` entries.
        - Deletes `NodeSubscriptionUsage` chart rows when `clean_chart_data` is enabled.

    Args:
        db (AsyncSession): The SQLAlchemy async session used for database operations.
        admin (Optional[Admin]): If provided, only resets data usage for users belonging to this admin.
                                 If None, resets usage for all users in the system.

    Notes:
        - All operations are executed in bulk for performance.
        - This function assumes proper foreign key constraints and cascading rules are in place.
        - The function commits changes at the end of the operation.
    """
    subscription_uuids = (
        select(Subscription.uuid).where(Subscription.admin_id == admin.id) if admin else select(Subscription.uuid)
    )

    reset_status = case(
        (Subscription.status == SubscriptionStatus.limited, SubscriptionStatus.active), else_=Subscription.status
    )
    reset_users = update(Subscription).values(used_traffic=0, status=reset_status)
    if admin:
        reset_users = reset_users.where(Subscription.admin_id == admin.id)
    await db.execute(reset_users)

    await db.execute(
        delete(SubscriptionUsageResetLogs).where(SubscriptionUsageResetLogs.subscription_uuid.in_(subscription_uuids))
    )
    if clean_chart_data:
        await db.execute(
            delete(NodeSubscriptionUsage).where(NodeSubscriptionUsage.subscription_uuid.in_(subscription_uuids))
        )
    await db.execute(delete(NextPlan).where(NextPlan.subscription_uuid.in_(subscription_uuids)))

    await db.commit()


async def disable_all_active_users(db: AsyncSession, admin: Admin | None = None):
    """
    Disable all active users or users under a specific admin.

    Args:
        db (AsyncSession): Database session.
        admin (Optional[Admin]): Admin to filter users by, if any.
    """
    query = update(Subscription).where(
        Subscription.status.in_((SubscriptionStatus.active, SubscriptionStatus.on_hold)),
        false(),
    )
    if admin:
        query = query.filter(Subscription.admin_id == admin.id)

    await db.execute(
        query.values(
            {Subscription.status: SubscriptionStatus.disabled, Subscription.last_status_change: dt.now(UTC)},
        )
    )

    await db.commit()
    await db.refresh(admin)


async def activate_all_disabled_users(db: AsyncSession, admin: Admin | None = None):
    """
    Activate all disabled users or users under a specific admin.

    Args:
        db (AsyncSession): Database session.
        admin (Optional[Admin]): Admin to filter users by, if any.
    """
    unmanaged = false()
    query_for_active_users = update(Subscription).where(Subscription.status == SubscriptionStatus.disabled, unmanaged)
    query_for_on_hold_users = update(Subscription).where(
        and_(
            Subscription.status == SubscriptionStatus.disabled,
            Subscription.expire.is_(None),
            Subscription.on_hold_expire_duration.isnot(None),
            unmanaged,
        )
    )
    if admin:
        query_for_active_users = query_for_active_users.where(Subscription.admin_id == admin.id)
        query_for_on_hold_users = query_for_on_hold_users.where(Subscription.admin_id == admin.id)

    # Back on hold: like an edit, it makes earlier traffic not count as coming online.
    now = dt.now(UTC)
    await db.execute(
        query_for_on_hold_users.values(
            {
                Subscription.status: SubscriptionStatus.on_hold,
                Subscription.last_status_change: now,
                Subscription.edit_at: now,
            },
        )
    )
    await db.execute(
        query_for_active_users.values(
            {Subscription.status: SubscriptionStatus.active, Subscription.last_status_change: dt.now(UTC)},
        )
    )

    await db.commit()
    await db.refresh(admin)


async def count_bulk_expire_targets(db: AsyncSession, bulk_model: BulkSubscription) -> int:
    """Count finite expirations eligible for the requested adjustment."""
    return (
        await db.execute(select(func.count(Subscription.uuid)).where(_bulk_user_target_filter(bulk_model, "expire")))
    ).scalar_one_or_none() or 0


async def count_bulk_datalimit_targets(db: AsyncSession, bulk_model: BulkSubscription) -> int:
    """Count finite quotas eligible for the requested adjustment, including subtraction guards."""
    return (
        await db.execute(select(func.count(Subscription.uuid)).where(_bulk_user_target_filter(bulk_model, "data_limit")))
    ).scalar_one_or_none() or 0


async def count_bulk_proxy_targets(db: AsyncSession, bulk_model: BulkSubscriptionsProxy) -> int:
    final_filter = _create_final_filter(bulk_model)
    return (await db.execute(select(func.count(Subscription.uuid)).where(final_filter))).scalar_one_or_none() or 0


def _create_final_filter(bulk_model: BulkSubscriptionFilter, *, include_core_managed: bool = False):
    """Create a comprehensive SQLAlchemy filter condition from a bulk model."""
    other_conditions = []
    if bulk_model.status:
        other_conditions.append(Subscription.status.in_([i.value for i in bulk_model.status]))
    if bulk_model.expire_after:
        other_conditions.append(Subscription.expire >= bulk_model.expire_after)
    if bulk_model.expire_before:
        other_conditions.append(Subscription.expire <= bulk_model.expire_before)
    if bulk_model.admins:
        other_conditions.append(Subscription.admin_id.in_([i for i in bulk_model.admins]))
    if bulk_model.group_ids:
        other_conditions.append(Subscription.groups.any(Group.id.in_(bulk_model.group_ids)))

    subscription_uuids = bulk_model.subscriptions or []

    filter_conditions = []
    if subscription_uuids:
        filter_conditions.append(_explicit_uuids(Subscription.uuid, subscription_uuids))
    if other_conditions:
        filter_conditions.append(and_(*other_conditions))

    if len(filter_conditions) > 1:
        condition = or_(*filter_conditions)
    elif filter_conditions:
        condition = filter_conditions[0]
    else:
        condition = true()
    if include_core_managed:
        return condition
    return and_(condition, false())


async def legacy_bulk_scope_contains_core_managed(db: AsyncSession, bulk_model: BulkSubscriptionFilter) -> bool:
    statement = (
        select(Subscription.uuid)
        .where(
            _create_final_filter(bulk_model, include_core_managed=True),
            true(),
        )
        .limit(1)
    )
    return (await db.execute(statement)).scalar_one_or_none() is not None


async def update_users_expire(
    db: AsyncSession,
    bulk_model: BulkSubscription,
    *,
    target_ids: Sequence[UUID] | None = None,
    minimum: dt | None = None,
    maximum: dt | None = None,
) -> tuple[list[Subscription], int] | tuple[list, int]:
    """
    Bulk update user expiration dates and return list of Subscription objects where status changed.
    """
    final_filter = _bulk_user_target_filter(bulk_model, "expire", target_ids)

    count_effctive_users = (
        await db.execute(select(func.count(Subscription.uuid)).where(final_filter))
    ).scalar_one_or_none() or 0
    # Get database-specific datetime addition expression
    new_expire = get_datetime_add_expression(db, Subscription.expire, bulk_model.amount)
    current_time = dt.now(UTC)

    # First, get the users that will have status changes BEFORE updating
    status_change_conditions = or_(
        and_(new_expire <= current_time, Subscription.status == SubscriptionStatus.active),
        and_(new_expire > current_time, Subscription.status == SubscriptionStatus.expired),
    )

    # Get IDs of users whose status will change
    result = await db.execute(select(Subscription.uuid).where(and_(final_filter, status_change_conditions)))
    status_changed_subscription_uuids = [row[0] for row in result.fetchall()]

    # Perform the update
    status_cases = [
        (
            and_(new_expire <= current_time, Subscription.status == SubscriptionStatus.active),
            SubscriptionStatus.expired,
        ),
        (and_(new_expire > current_time, Subscription.status == SubscriptionStatus.expired), SubscriptionStatus.active),
    ]

    result = await db.execute(
        update(Subscription)
        .where(final_filter, _bulk_final_value_bounds(new_expire, minimum, maximum))
        .values(expire=new_expire, status=case(*status_cases, else_=Subscription.status))
    )
    await _check_bulk_update_count(db, result, target_ids)
    await db.commit()

    # Return the users whose status changed
    if status_changed_subscription_uuids:
        users = await _reload_subscriptions_with_inbounds(db, status_changed_subscription_uuids)
        return users, count_effctive_users
    return [], count_effctive_users


async def update_users_datalimit(
    db: AsyncSession,
    bulk_model: BulkSubscription,
    *,
    target_ids: Sequence[UUID] | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
) -> tuple[list[Subscription], int] | tuple[list, int]:
    """
    Bulk update user data limits and return list of Subscription objects where status changed.
    """
    final_filter = _bulk_user_target_filter(bulk_model, "data_limit", target_ids)

    count_effctive_users = (
        await db.execute(select(func.count(Subscription.uuid)).where(final_filter))
    ).scalar_one_or_none() or 0

    # First, get the users that will have status changes BEFORE updating
    status_change_conditions = or_(
        and_(
            Subscription.data_limit + bulk_model.amount <= Subscription.used_traffic,
            Subscription.status == SubscriptionStatus.active,
        ),
        and_(
            Subscription.data_limit + bulk_model.amount > Subscription.used_traffic,
            Subscription.status == SubscriptionStatus.limited,
        ),
    )

    # Get IDs of users whose status will change
    result = await db.execute(select(Subscription.uuid).where(and_(final_filter, status_change_conditions)))
    status_changed_subscription_uuids = [row[0] for row in result.fetchall()]

    # Perform the update
    status_cases = [
        (
            and_(
                Subscription.data_limit + bulk_model.amount <= Subscription.used_traffic,
                Subscription.status == SubscriptionStatus.active,
            ),
            SubscriptionStatus.limited,
        ),
        (
            and_(
                Subscription.data_limit + bulk_model.amount > Subscription.used_traffic,
                Subscription.status == SubscriptionStatus.limited,
            ),
            SubscriptionStatus.active,
        ),
    ]

    result = await db.execute(
        update(Subscription)
        .where(final_filter, _bulk_final_value_bounds(Subscription.data_limit + bulk_model.amount, minimum, maximum))
        .values(
            data_limit=Subscription.data_limit + bulk_model.amount,
            status=case(*status_cases, else_=Subscription.status),
        )
    )
    await _check_bulk_update_count(db, result, target_ids)
    await db.commit()

    # Return the users whose status changed
    if status_changed_subscription_uuids:
        users = await _reload_subscriptions_with_inbounds(db, status_changed_subscription_uuids)
        return users, count_effctive_users
    return [], count_effctive_users


async def update_users_proxy_settings(
    db: AsyncSession, bulk_model: BulkSubscriptionsProxy
) -> tuple[list, int] | tuple[list[Subscription], int]:
    """
    Bulk update the `proxy_settings` JSON field for users and return updated rows.
    """
    final_filter = _create_final_filter(bulk_model)

    # Capture target IDs without materializing users before the update.
    select_stmt = select(Subscription.uuid).where(final_filter)
    result = await db.execute(select_stmt)
    updated_subscription_uuids = result.scalars().all()
    count_effctive_users = len(updated_subscription_uuids)

    if not updated_subscription_uuids:
        return [], count_effctive_users

    dialect = db.bind.dialect.name

    # Prepare the update statement
    if dialect == "postgresql":
        proxy_settings_expr = cast(Subscription.proxy_settings, JSONB)
        if bulk_model.method is not None:
            proxy_settings_expr = func.jsonb_set(
                proxy_settings_expr,
                text("'{shadowsocks,method}'"),
                cast(f"{bulk_model.method.value}", JSONB),
                True,
            )
    else:
        proxy_settings_expr = Subscription.proxy_settings
        if bulk_model.method is not None:
            proxy_settings_expr = func.json_set(
                proxy_settings_expr, "$.shadowsocks.method", f"{bulk_model.method.value}"
            )

    # Perform the update
    update_stmt = update(Subscription).where(final_filter).values(proxy_settings=proxy_settings_expr)
    await db.execute(update_stmt)
    await db.commit()

    # Reload the captured targets and preserve their original selection order.
    refreshed_users = await _reload_subscriptions_with_inbounds(db, updated_subscription_uuids)
    refreshed_users_map = {user.uuid: user for user in refreshed_users}
    ordered_refreshed_users = [refreshed_users_map[uid] for uid in updated_subscription_uuids if uid in refreshed_users_map]

    return ordered_refreshed_users, count_effctive_users
