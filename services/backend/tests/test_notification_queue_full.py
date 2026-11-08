"""A full notification queue drops the new notification instead of failing the caller."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from nats.js.errors import NotFoundError, ServiceUnavailableError

from app.jobs import review_subscriptions
from app.models.settings import Webhook, WebhookInfo
from app.notification import queue_manager, webhook as webhook_notification
from app.notification.nats_queue import InMemoryNotificationQueue, NatsNotificationQueue

STREAM_FULL = ServiceUnavailableError(code=503, err_code=10077, description="maximum messages exceeded")
BYTES_FULL = ServiceUnavailableError(code=503, err_code=10077, description="maximum bytes exceeded")


@pytest.fixture
def full_queues(monkeypatch: pytest.MonkeyPatch):
    """The dedicated webhook outbox accepts one item; later events are dropped safely."""
    queues = SimpleNamespace(webhook=InMemoryNotificationQueue(maxsize=1))
    monkeypatch.setattr(queue_manager, "webhook_queue_instance", queues.webhook)
    logger = MagicMock()
    monkeypatch.setattr(queue_manager, "logger", logger)
    queues.logger = logger
    return queues


def _nats_queue(publish_error) -> NatsNotificationQueue:
    queue = NatsNotificationQueue(stream_name="WEBHOOKS", subject="webhooks.queue")
    queue._js = SimpleNamespace(publish=AsyncMock(side_effect=publish_error))
    return queue


@pytest.mark.parametrize("error", [STREAM_FULL, BYTES_FULL], ids=["max-msgs", "max-bytes"])
async def test_nats_enqueue_reports_a_full_stream_as_queue_full(error):
    with pytest.raises(asyncio.QueueFull):
        await _nats_queue(error).enqueue({"id": 1})


@pytest.mark.parametrize(
    "error",
    [
        ServiceUnavailableError(code=503, err_code=10077, description="store closed"),
        NotFoundError(code=404, err_code=10059, description="stream not found"),
    ],
    ids=["other-store-failure", "missing-stream"],
)
async def test_nats_enqueue_keeps_other_publish_errors(error):
    with pytest.raises(type(error)):
        await _nats_queue(error).enqueue({"id": 1})


async def test_enqueue_helpers_drop_and_log_when_the_queue_is_full(full_queues):
    await queue_manager.enqueue_webhook({"id": 1})
    await queue_manager.enqueue_webhook({"id": 2})

    assert full_queues.webhook._count == 1
    full_queues.logger.warning.assert_called_once_with("Webhook queue full; dropping notification")


async def test_enqueue_helper_drops_when_the_nats_stream_is_full(full_queues, monkeypatch):
    queue = _nats_queue(STREAM_FULL)
    monkeypatch.setattr(queue_manager, "webhook_queue_instance", queue)
    await queue_manager.enqueue_webhook({"id": 1})
    queue._js.publish.assert_awaited_once()
    full_queues.logger.warning.assert_called_once()


async def test_enqueue_helpers_still_raise_unexpected_errors(monkeypatch):
    monkeypatch.setattr(queue_manager, "webhook_queue_instance", _nats_queue(RuntimeError("broken")))
    with pytest.raises(RuntimeError, match="broken"):
        await queue_manager.enqueue_webhook({"id": 1})


async def test_bulk_notify_survives_a_full_webhook_queue(full_queues, monkeypatch):
    settings = Webhook(
        enable=True, webhooks=[WebhookInfo(url="http://127.0.0.1:9/h", secret="")], timeout=1, recurrent=1
    )
    monkeypatch.setattr(webhook_notification, "webhook_settings", AsyncMock(return_value=settings))

    await webhook_notification.bulk_notify([{"id": i} for i in range(5)])

    assert full_queues.webhook._count == 1
    assert full_queues.logger.warning.call_count == 4


async def test_days_left_job_records_every_threshold_when_the_queue_is_full(full_queues, monkeypatch):
    """review_subscriptions enqueues right after recording reminders; a full queue must not abort the job."""
    settings = Webhook(
        enable=True,
        webhooks=[WebhookInfo(url="http://127.0.0.1:9/h", secret="")],
        days_left=[3, 1],
        timeout=1,
        recurrent=1,
    )
    monkeypatch.setattr(review_subscriptions, "webhook_settings", AsyncMock(return_value=settings))
    monkeypatch.setattr(webhook_notification, "webhook_settings", AsyncMock(return_value=settings))

    @asynccontextmanager
    async def fake_db():
        yield AsyncMock()

    users = {
        3: [SimpleNamespace(uuid=uuid4(), days_left=3, expire=None), SimpleNamespace(uuid=uuid4(), days_left=3, expire=None)],
        1: [SimpleNamespace(uuid=uuid4(), days_left=1, expire=None)],
    }
    monkeypatch.setattr(review_subscriptions, "GetDB", fake_db)
    monkeypatch.setattr(
        review_subscriptions, 'get_days_left_reached_subscriptions', AsyncMock(side_effect=lambda db, days: users[days])
    )
    record_reminders = AsyncMock()
    monkeypatch.setattr(review_subscriptions, "bulk_create_notification_reminders", record_reminders)

    await review_subscriptions.days_left_notification_job()

    assert [[row["subscription_uuid"] for row in call.args[1]] for call in record_reminders.await_args_list] == [
        [user.uuid for user in users[3]],
        [user.uuid for user in users[1]],
    ]
    assert full_queues.webhook._count == 1
    assert full_queues.logger.warning.call_count == 2
