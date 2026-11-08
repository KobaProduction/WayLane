from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.db.crud.subscription import (
    get_subscriptions_agent_counts,
    get_subscriptions_agent_stats,
    get_subscriptions_sub_update_list,
)
from app.db.models import Subscription, SubscriptionUpdate
from app.models.stats import Period
from app.subscription import sub_update_buffer
from tests.api import TestSession, engine


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", ["list", "counts", "stats"])
@pytest.mark.parametrize("already_flushed", [False, True])
async def test_subscription_reads_see_updates_after_request_snapshot(reader, already_flushed):
    # SQLite may give this user the id of one an earlier test deleted, whose update rows (foreign keys are not enforced
    # there) or still-queued buffer entries would then count as this user's: start from none.
    await sub_update_buffer.reset_user_sub_update_buffer()
    async with TestSession() as setup:
        user = Subscription(uuid=uuid4())
        setup.add(user)
        await setup.commit()
        subscription_uuid = user.uuid
        await setup.execute(delete(SubscriptionUpdate).where(SubscriptionUpdate.subscription_uuid == subscription_uuid))
        await setup.commit()

    try:
        async with TestSession() as request:
            if engine.dialect.name in {"mysql", "mariadb", "postgresql"}:
                await request.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            # Authorization/user lookup establishes the request's read snapshot.
            db_user = await request.scalar(select(Subscription).where(Subscription.uuid == subscription_uuid))
            db_user.note = "uncommitted caller change"
            await sub_update_buffer.queue_user_sub_update(subscription_uuid, "snapshot-client")
            if already_flushed:
                await sub_update_buffer.flush_user_sub_updates()

            start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
            end = start + timedelta(hours=3)
            if reader == "list":
                rows, count = await get_subscriptions_sub_update_list(request, subscription_uuid)
                assert count == 1
                assert [row.user_agent for row in rows] == ["snapshot-client"]
            elif reader == "counts":
                counts = await get_subscriptions_agent_counts(request, subscription_uuid=subscription_uuid, start=start, end=end)
                assert counts == [("snapshot-client", 1)]
            else:
                stats = await get_subscriptions_agent_stats(request, start, end, Period.hour, subscription_uuid=subscription_uuid)
                assert [(row["agent"], row["count"]) for row in stats] == [("snapshot-client", 1)]

            assert request.in_transaction()
            assert db_user in request.dirty
            assert db_user.note == "uncommitted caller change"

        async with TestSession() as check:
            assert await check.scalar(select(Subscription.note).where(Subscription.uuid == subscription_uuid)) is None
    finally:
        await sub_update_buffer.flush_user_sub_updates()
        async with TestSession() as cleanup:
            # The API SQLite test engine does not enable foreign-key cascades.
            await cleanup.execute(delete(SubscriptionUpdate).where(SubscriptionUpdate.subscription_uuid == subscription_uuid))
            await cleanup.execute(delete(Subscription).where(Subscription.uuid == subscription_uuid))
            await cleanup.commit()
