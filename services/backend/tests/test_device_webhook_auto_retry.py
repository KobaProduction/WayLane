"""Automatic device webhook retry scheduling, exhausted recovery and observability."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.db.subscription_firewall import subscription_device_events, subscription_firewall_config
from app.routers.subscription_firewall import get_device_event_summary
from app.subscription import device_webhook
from app.subscription.firewall import SCENARIOS


@pytest.mark.parametrize(
    ("attempts", "expected"),
    [(1, 30), (2, 60), (3, 120), (4, 240), (8, 3600), (10, 3600)],
)
def test_exponential_retry_backoff_is_bounded(attempts, expected):
    assert device_webhook.retry_delay_seconds(attempts) == expected


@pytest.mark.asyncio
async def test_due_worker_backoff_exhaustion_and_expired_leases(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "retry.db"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def test_db():
        async with sessions() as session:
            yield session

    monkeypatch.setattr(device_webhook, "GetDB", test_db)
    monkeypatch.setenv("WAYLANE_DEVICE_WEBHOOK_SECRET", "temporary-secure-local-integration-secret-123")
    monkeypatch.setenv("WAYLANE_WEBHOOK_ALLOW_LOCAL_TEST", "1")
    # A closed local endpoint deterministically refuses TCP connections.
    webhook_url = "http://127.0.0.1:1/device-event"
    now = datetime.now(UTC)
    old_lease_id = str(uuid4())
    exhausted_id = str(uuid4())
    active_id = None

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sessions() as db:
            await db.execute(insert(subscription_firewall_config).values(
                id=1, enabled=False, scenarios=SCENARIOS,
                webhook_enabled=True, webhook_url=webhook_url,
            ))
            active_id = await device_webhook.enqueue_device_event(
                db, "device.added", 17, "synthetic-hwid"
            )
            await db.execute(insert(subscription_device_events).values(
                id=old_lease_id, event_type="device.removed", user_id=17,
                hwid="expired", payload={"id": old_lease_id, "type": "device.removed"},
                status="sending", attempts=9, delivery_token=str(uuid4()),
                lease_until=now - timedelta(seconds=90),
            ))
            await db.execute(insert(subscription_device_events).values(
                id=exhausted_id, event_type="device.reset", user_id=17,
                hwid=None, payload={"id": exhausted_id, "type": "device.reset"},
                status="failed", attempts=10,
            ))
            await db.commit()
        assert not await device_webhook.deliver_device_event(active_id)
        async with sessions() as db:
            row = (await db.execute(
                select(subscription_device_events).where(
                    subscription_device_events.c.id == active_id
                )
            )).mappings().one()
            assert row["status"] == "failed"
            assert row["attempts"] == 1
            assert row["next_attempt_at"] is not None
        # A second scheduled pass before the due time must NOT retry this event.
        worker = await device_webhook.retry_due_device_events()
        assert worker["selected"] == 1   # expired 10th lease is recovered
        async with sessions() as db:
            states = {row.id: row.status for row in (await db.execute(
                select(subscription_device_events.c.id, subscription_device_events.c.status)
            )).all()}
            assert states[old_lease_id] == "exhausted"
            assert states[exhausted_id] == "exhausted"
            assert states[active_id] == "failed"
            # Move only this failed event's due time into the past, simulating
            # downtime beyond its originally recorded backoff.
            await db.execute(update(subscription_device_events).where(
                subscription_device_events.c.id == active_id
            ).values(next_attempt_at=now - timedelta(seconds=1)))
            await db.commit()
        worker = await device_webhook.retry_due_device_events()
        assert worker == {"selected": 1, "delivered": 0, "failed": 1}
        async with sessions() as db:
            row = (await db.execute(select(subscription_device_events).where(
                subscription_device_events.c.id == active_id
            ))).mappings().one()
            assert row["attempts"] == 2
            assert row["status"] == "failed"
            assert row["last_error"] == "ConnectError"
            assert row["next_attempt_at"] is not None
            summary = await get_device_event_summary(db, None)
            assert summary["failed"] == 1
            assert summary["exhausted"] == 2

        assert await device_webhook.requeue_exhausted_event(exhausted_id)
        assert not await device_webhook.requeue_exhausted_event(exhausted_id)
        worker = await device_webhook.retry_due_device_events()
        assert worker == {"selected": 1, "delivered": 0, "failed": 1}
        async with sessions() as db:
            row = (await db.execute(select(subscription_device_events).where(
                subscription_device_events.c.id == exhausted_id
            ))).mappings().one()
            assert row["attempts"] == 1
            assert row["status"] == "failed"
    finally:
        await engine.dispose()


def test_scheduler_job_registration_uses_existing_scheduler():
    from app.jobs import device_webhook_retry
    from config import runtime_settings
    if runtime_settings.role.runs_scheduler:
        from app.scheduler import scheduler
        job = scheduler.get_job("retry_device_webhooks")
        assert job is not None
        assert job.max_instances == 1
        assert job.coalesce is True
