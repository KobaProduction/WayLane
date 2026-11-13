import asyncio
from collections.abc import Sequence

from app.db import AsyncSession, GetDB
from app.db.crud.admin import get_active_to_limited_admins, update_admin_status
from app.db.crud.admin_role import get_role
from app.db.crud.subscription import get_admin_subscriptions_for_node_sync, get_role_subscriptions_for_node_sync
from app.db.models import Admin, AdminRole, AdminStatus, SubscriptionStatus
from app.subscription_delivery import remove_subscriptions as sync_remove_subscriptions, sync_subscriptions
from app.utils.logger import get_logger

logger = get_logger("admin-sync")

# Subscriptions per UUID-ordered page when a role policy change is applied to existing subscribers.
ROLE_SYNC_BATCH_SIZE = 500

_role_sync_locks: dict[int, asyncio.Lock] = {}
# Keeps background reconciliations referenced until they finish.
_role_sync_tasks: set[asyncio.Task] = set()


async def admin_users_sync_blocked(admin: Admin) -> bool:
    """Return whether the admin's current status and role policy block node access."""
    role = admin.__dict__.get("role")
    if role is None:
        try:
            role = await admin.awaitable_attrs.role
        except AttributeError:
            role = None

    if admin.status == AdminStatus.limited:
        return bool(role and role.disconnect_users_when_limited)
    if admin.status == AdminStatus.disabled:
        return bool(role and role.disconnect_users_when_disabled)
    return False


async def sync_admin_users_for_block_transition(
    db: AsyncSession,
    admin: Admin,
    was_blocked: bool,
) -> int:
    """Dispatch user removals or restorations when an admin's block state changes."""
    is_blocked = await admin_users_sync_blocked(admin)
    if was_blocked == is_blocked:
        return 0

    if is_blocked:
        users = await get_admin_subscriptions_for_node_sync(
            db,
            admin.id,
            statuses=[SubscriptionStatus.active, SubscriptionStatus.on_hold],
        )
        await sync_remove_subscriptions(users)
    else:
        users = await get_admin_subscriptions_for_node_sync(db, admin.id)
        await sync_subscriptions(users)

    return len(users)


async def sync_role_users_for_block_transition(
    db: AsyncSession,
    role: AdminRole,
    statuses: Sequence[AdminStatus],
) -> int:
    """Apply the role's current policy for the given admin statuses in bounded user batches."""
    blocked_statuses: list[AdminStatus] = []
    unblocked_statuses: list[AdminStatus] = []
    for status, is_blocked in (
        (AdminStatus.limited, role.disconnect_users_when_limited),
        (AdminStatus.disabled, role.disconnect_users_when_disabled),
    ):
        if status in statuses:
            (blocked_statuses if is_blocked else unblocked_statuses).append(status)

    synced_count = 0
    for statuses_to_sync, sync in (
        (blocked_statuses, sync_remove_subscriptions),
        (unblocked_statuses, sync_subscriptions),
    ):
        if statuses_to_sync:
            after_subscription_uuid = None
            while users := await get_role_subscriptions_for_node_sync(
                db,
                role.id,
                statuses_to_sync,
                after_subscription_uuid=after_subscription_uuid,
                limit=ROLE_SYNC_BATCH_SIZE,
            ):
                after_subscription_uuid = users[-1].uuid
                await sync(users, await_dispatch=True)
                synced_count += len(users)
                del users
    return synced_count


async def apply_role_users_block_sync(role_id: int, statuses: Sequence[AdminStatus]) -> int:
    """Reconcile node users after a committed role policy change, outside the request.

    The role is read again under a per-role lock, so overlapping edits apply in order
    and the last one wins. Node delivery failures are logged by the node manager; an
    unreachable node receives a fresh snapshot when it reconnects.
    """
    lock = _role_sync_locks.setdefault(role_id, asyncio.Lock())
    try:
        async with lock, GetDB() as db:
            role = await get_role(db, role_id)
            if role is None:
                return 0
            synced_count = await sync_role_users_for_block_transition(db, role, statuses)
    except Exception:
        logger.exception("Failed to apply the disconnect policy of role %s to node users", role_id)
        return 0
    if synced_count:
        logger.info("Applied the disconnect policy of role %s to %s node users", role_id, synced_count)
    return synced_count


def schedule_role_users_block_sync(role_id: int, statuses: Sequence[AdminStatus]) -> asyncio.Task:
    """Start the node reconciliation for a committed role policy change in the background."""
    task = asyncio.create_task(apply_role_users_block_sync(role_id, list(statuses)))
    _role_sync_tasks.add(task)
    task.add_done_callback(_role_sync_tasks.discard)
    return task


async def limit_exceeded_admins(db: AsyncSession, logger=None) -> int:
    admins = await get_active_to_limited_admins(db)
    if not admins:
        return 0

    for admin in admins:
        await update_admin_status(db, admin, AdminStatus.limited)
        if logger:
            logger.info(f'Admin "{admin.username}" status changed to limited')

        synced_count = await sync_admin_users_for_block_transition(db, admin, was_blocked=False)
        if synced_count and logger:
            logger.info(f'Admin "{admin.username}" removed {synced_count} users from nodes')

    return len(admins)


async def enforce_admin_limits_now(logger=None):
    """Flip active admins that exceeded data_limit without waiting for the scheduler tick."""
    async with GetDB() as db:
        await limit_exceeded_admins(db, logger=logger)
