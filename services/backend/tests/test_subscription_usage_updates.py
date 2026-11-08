"""Subscription usage/statistics behavior that must survive the UUID API migration."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, select

from app.db.crud.subscription import (
    get_subscription_by_uuid as get_db_subscription,
    get_subscriptions as get_db_subscriptions,
    update_subscriptions_status,
)
from app.db.models import Subscription, SubscriptionStatus, SubscriptionUsageResetLogs
from app.models.subscription_models import SubscriptionListQuery
from tests.api import TestSession


def test_update_users_status_does_not_refresh_users_individually():
    subscription_uuids = [uuid4() for _ in range(3)]

    async def _update_status():
        async with TestSession() as session:
            session.add_all([Subscription(uuid=subscription_uuid) for subscription_uuid in subscription_uuids])
            await session.commit()

            statements: list[str] = []
            listener_registered = False

            def record_statement(_, __, statement, *args):
                statements.append(statement)

            try:
                users = await get_db_subscriptions(
                    session, SubscriptionListQuery(uuids=subscription_uuids)
                )
                event.listen(session.bind.sync_engine, "before_cursor_execute", record_statement)
                listener_registered = True

                assert await update_subscriptions_status(session, [], SubscriptionStatus.expired) == []
                assert statements == []

                updated_users = await update_subscriptions_status(session, users, SubscriptionStatus.expired)

                assert all(user.status == SubscriptionStatus.expired for user in updated_users)
                changed_at_values = {user.last_status_change for user in updated_users}
                assert None not in changed_at_values
                assert len(changed_at_values) == 1
                assert all("groups" in user.__dict__ for user in updated_users)
                assert all("usage_logs" in user.__dict__ for user in updated_users)

                event.remove(session.bind.sync_engine, "before_cursor_execute", record_statement)
                listener_registered = False

                async with TestSession() as verification_session:
                    persisted_result = await verification_session.execute(
                        select(Subscription.status, Subscription.last_status_change).where(
                            Subscription.uuid.in_(subscription_uuids)
                        )
                    )
                    persisted_rows = persisted_result.all()

                assert len(persisted_rows) == len(subscription_uuids)
                assert all(row.status == SubscriptionStatus.expired for row in persisted_rows)
                persisted_changed_at_values = {row.last_status_change for row in persisted_rows}
                assert None not in persisted_changed_at_values
                assert len(persisted_changed_at_values) == 1

                return statements
            finally:
                if listener_registered:
                    event.remove(session.bind.sync_engine, "before_cursor_execute", record_statement)
                await session.rollback()
                await session.execute(delete(Subscription).where(Subscription.uuid.in_(subscription_uuids)))
                await session.commit()

    statements = asyncio.run(_update_status())

    assert len(statements) == 1
    assert statements[0].lstrip().upper().startswith("UPDATE")


@pytest.mark.asyncio
async def test_subscription_lifetime_usage_refresh_after_new_reset_log():
    subscription_uuid = uuid4()
    async with TestSession() as session:
        subscription = Subscription(uuid=subscription_uuid)
        session.add(subscription)
        await session.commit()
        subscription_uuid = subscription.uuid
    try:
        async with TestSession() as session:
            db_subscription = await get_db_subscription(
                session,
                subscription_uuid,
                load_admin=False,
                load_next_plan=False,
                load_usage_logs=False,
                load_groups=False,
                load_lifetime_used_traffic=True,
            )
            assert db_subscription is not None
            initial = db_subscription.lifetime_used_traffic
            session.add(SubscriptionUsageResetLogs(subscription_uuid=subscription_uuid, used_traffic_at_reset=23456))
            await session.commit()
            await session.refresh(db_subscription)
            await db_subscription.awaitable_attrs.usage_logs
            assert db_subscription.lifetime_used_traffic == initial + 23456
    finally:
        async with TestSession() as session:
            await session.execute(
                delete(SubscriptionUsageResetLogs).where(SubscriptionUsageResetLogs.subscription_uuid == subscription_uuid)
            )
            await session.execute(delete(Subscription).where(Subscription.uuid == subscription_uuid))
            await session.commit()
