from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import bindparam, delete, func, insert, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.schema import Table

from app.db.models import Subscription, SubscriptionHWID


async def get_subscription_hwids(db: AsyncSession, subscription_uuid: UUID) -> list[SubscriptionHWID]:
    """Retrieve all HWIDs registered for a specific user."""
    stmt = (
        select(SubscriptionHWID)
        .where(SubscriptionHWID.subscription_uuid == subscription_uuid)
        .order_by(SubscriptionHWID.created_at.desc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_subscription_hwid_by_value(db: AsyncSession, subscription_uuid: UUID, hwid_str: str) -> SubscriptionHWID | None:
    """Retrieve a specific HWID for a user by its value."""
    stmt = select(SubscriptionHWID).where(
        SubscriptionHWID.subscription_uuid == subscription_uuid, SubscriptionHWID.hwid == hwid_str
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_subscription_hwid_count(db: AsyncSession, subscription_uuid: UUID) -> int:
    """Count the number of HWIDs registered for a user."""
    stmt = select(func.count(SubscriptionHWID.id)).where(SubscriptionHWID.subscription_uuid == subscription_uuid)
    return (await db.execute(stmt)).scalar_one()


async def register_subscription_hwid(
    db: AsyncSession,
    subscription_uuid: UUID,
    hwid: str,
    device_os: str | None = None,
    os_version: str | None = None,
    device_model: str | None = None,
    *,
    limit: int | None = None,
) -> bool:
    """Commit an HWID insert/refresh, or return False if a new device exceeds the limit."""
    dialect = db.bind.dialect.name
    if limit is not None and limit > 0:
        # Subscription validation may have established a read snapshot already.
        # Start fresh so MySQL's REPEATABLE READ sees registrations committed
        # before this request acquires the parent lock.
        await db.commit()
        if dialect == "sqlite":
            # Acquire SQLite's write lock before reading the HWID set. A no-op
            # write serializes even separate workers without upgrading a snapshot.
            user_table = cast(Table, Subscription.__table__)
            await db.execute(
                update(user_table).where(user_table.c.uuid == subscription_uuid).values(hwid_limit=user_table.c.hwid_limit)
            )
        else:
            # Lock the parent: locking HWID rows alone cannot protect an empty set.
            await db.execute(select(Subscription.uuid).where(Subscription.uuid == subscription_uuid).with_for_update())

        existing_stmt = select(SubscriptionHWID.id).where(
            SubscriptionHWID.subscription_uuid == subscription_uuid, SubscriptionHWID.hwid == hwid
        )
        existing_id = (await db.execute(existing_stmt)).scalar_one_or_none()
        if existing_id is None and await get_subscription_hwid_count(db, subscription_uuid) >= limit:
            await db.commit()  # Release the lock on rejected registrations too.
            return False

    # For unlimited-device accounts the registration path still needs to
    # detect first registration, not emit "added" on every refresh.
    if limit is None or limit <= 0:
        existing_id = (
            await db.execute(
                select(SubscriptionHWID.id).where(
                    SubscriptionHWID.subscription_uuid == subscription_uuid, SubscriptionHWID.hwid == hwid
                )
            )
        ).scalar_one_or_none()

    now = datetime.now(UTC)
    params = {
        "subscription_uuid": subscription_uuid,
        "hwid": hwid,
        "device_os": device_os[:256] if device_os else None,
        "os_version": os_version[:128] if os_version else None,
        "device_model": device_model[:256] if device_model else None,
        "created_at": now,
        "last_used_at": now,
    }
    table = cast(Table, SubscriptionHWID.__table__)
    existing_device = await get_subscription_hwid_by_value(db, subscription_uuid, hwid) if existing_id is not None else None
    changed_metadata = {
        field: params[field]
        for field in ("device_os", "os_version", "device_model")
        if existing_device is not None
        and params[field] is not None
        and getattr(existing_device, field) != params[field]
    }

    if dialect == "postgresql":
        stmt = pg_insert(table).values(
            subscription_uuid=bindparam("subscription_uuid"),
            hwid=bindparam("hwid"),
            device_os=bindparam("device_os"),
            os_version=bindparam("os_version"),
            device_model=bindparam("device_model"),
            created_at=bindparam("created_at"),
            last_used_at=bindparam("last_used_at"),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["subscription_uuid", "hwid"],
            set_={"last_used_at": bindparam("last_used_at")},
        )
        await db.execute(stmt, [params])
    elif dialect == "mysql":
        stmt = mysql_insert(table).values(
            subscription_uuid=bindparam("subscription_uuid"),
            hwid=bindparam("hwid"),
            device_os=bindparam("device_os"),
            os_version=bindparam("os_version"),
            device_model=bindparam("device_model"),
            created_at=bindparam("created_at"),
            last_used_at=bindparam("last_used_at"),
        )
        stmt = stmt.on_duplicate_key_update(last_used_at=stmt.inserted.last_used_at)
        await db.execute(stmt, [params])
    else:  # SQLite
        insert_stmt = (
            insert(table)
            .values(
                subscription_uuid=bindparam("subscription_uuid"),
                hwid=bindparam("hwid"),
                device_os=bindparam("device_os"),
                os_version=bindparam("os_version"),
                device_model=bindparam("device_model"),
                created_at=bindparam("created_at"),
                last_used_at=bindparam("last_used_at"),
            )
            .prefix_with("OR IGNORE")
        )
        update_stmt = (
            update(table)
            .values(last_used_at=bindparam("last_used_at"))
            .where(table.c.subscription_uuid == bindparam("b_subscription_uuid"), table.c.hwid == bindparam("b_hwid"))
        )
        update_params = {
            "last_used_at": now,
            "b_subscription_uuid": subscription_uuid,
            "b_hwid": hwid,
        }
        await db.execute(insert_stmt, [params])
        await db.execute(update_stmt, [update_params])

    event_id = None
    if changed_metadata:
        await db.execute(
            update(table)
            .where(table.c.subscription_uuid == subscription_uuid, table.c.hwid == hwid)
            .values(**changed_metadata)
        )
    if existing_id is None or changed_metadata:
        from app.subscription.device_webhook import enqueue_device_event

        event_id = await enqueue_device_event(
            db,
            "device.added" if existing_id is None else "device.updated",
            subscription_uuid,
            hwid,
            {field: params[field] for field in ("device_os", "os_version", "device_model")},
        )
    await db.commit()
    if event_id:
        from app.subscription.device_webhook import dispatch_after_commit

        dispatch_after_commit(event_id)
    return True


async def delete_subscription_hwid(db: AsyncSession, subscription_uuid: UUID, hwid: str) -> bool:
    """Delete a specific HWID for a user by its value. Returns True if deleted."""
    stmt = delete(SubscriptionHWID).where(
        SubscriptionHWID.subscription_uuid == subscription_uuid, SubscriptionHWID.hwid == hwid
    )
    result = await db.execute(stmt)
    event_id = None
    if result.rowcount > 0:
        from app.subscription.device_webhook import enqueue_device_event

        event_id = await enqueue_device_event(db, "device.removed", subscription_uuid, hwid)
    await db.commit()
    if event_id:
        from app.subscription.device_webhook import dispatch_after_commit

        dispatch_after_commit(event_id)
    return result.rowcount > 0


async def reset_subscription_hwids(db: AsyncSession, subscription_uuid: UUID) -> int:
    """Delete all HWIDs for a user. Returns the number of HWIDs deleted."""
    stmt = delete(SubscriptionHWID).where(SubscriptionHWID.subscription_uuid == subscription_uuid)
    result = await db.execute(stmt)
    event_id = None
    if result.rowcount > 0:
        from app.subscription.device_webhook import enqueue_device_event

        event_id = await enqueue_device_event(
            db, "device.reset", subscription_uuid, None, {"removed_count": result.rowcount}
        )
    await db.commit()
    if event_id:
        from app.subscription.device_webhook import dispatch_after_commit

        dispatch_after_commit(event_id)
    return result.rowcount
