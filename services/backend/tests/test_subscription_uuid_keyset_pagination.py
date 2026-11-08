"""Role reconciliation pages subscriptions by UUID, never by retired numeric IDs."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import Uuid, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.crud.subscription import get_role_subscriptions_for_node_sync
from app.db.models import Admin, AdminRole, AdminStatus, Subscription, SubscriptionStatus


@pytest.mark.asyncio
async def test_role_subscription_batches_use_uuid_keyset_without_duplicates():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            role = AdminRole(
                name=f"uuid-role-{uuid4().hex}", is_owner=False,
                permissions={}, limits={}, features={}, access={},
            )
            db.add(role)
            await db.flush()
            admin = Admin(
                username=f"uuid-admin-{uuid4().hex}", hashed_password="unused",
                role_id=role.id, status=AdminStatus.disabled,
            )
            db.add(admin)
            await db.flush()
            subscriptions = [
                Subscription(
                    uuid=uuid4(), admin_id=admin.id, entitlement="active",
                    status=SubscriptionStatus.expired if number % 11 == 0 else SubscriptionStatus.active,
                    proxy_settings={},
                )
                for number in range(54)
            ]
            db.add_all(subscriptions)
            await db.commit()
            wanted = sorted(sub.uuid for sub in subscriptions if sub.status == SubscriptionStatus.active)

        cursor: UUID | None = None
        collected: list[UUID] = []
        async with sessions() as db:
            while True:
                page = await get_role_subscriptions_for_node_sync(
                    db, role.id, [AdminStatus.disabled], after_subscription_uuid=cursor, limit=7,
                )
                if not page:
                    break
                uuids = [sub.uuid for sub in page]
                assert all(isinstance(uid, UUID) and uid.version == 4 for uid in uuids)
                assert uuids == sorted(uuids)
                if cursor is not None:
                    assert uuids[0] > cursor
                collected.extend(uuids)
                cursor = uuids[-1]
        assert collected == wanted
        assert len(set(collected)) == len(wanted)
    finally:
        await engine.dispose()


def test_postgres_subscription_pagination_binds_uuid_not_integer():
    cursor = uuid4()
    stmt = select(Subscription.uuid).where(Subscription.uuid > cursor).order_by(Subscription.uuid)
    compiled = stmt.compile(dialect=postgresql.dialect())
    assert "subscriptions.uuid >" in str(compiled)
    assert "ORDER BY subscriptions.uuid" in str(compiled)
    uuid_params = [bind.type for bind in compiled.binds.values() if bind.value == cursor]
    assert uuid_params and all(isinstance(kind, Uuid) for kind in uuid_params)
