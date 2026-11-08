"""Durable outbound webhook events; no embedded messaging clients or bot queues."""

import asyncio
import time
from typing import Literal

from pydantic import BaseModel, Field

from app.nats import is_nats_enabled
from app.notification.nats_queue import InMemoryNotificationQueue, NatsNotificationQueue, NotificationQueue
from app.utils.logger import get_logger
from config import nats_settings, runtime_settings

logger = get_logger("Notification")


class WebhookNotification(BaseModel):
    type: Literal["webhook"] = "webhook"
    payload: dict
    send_at: float = Field(allow_inf_nan=False)
    tries: int = Field(default=0, ge=0)
    pending_webhooks: list[str] | None = None


webhook_queue_instance: NotificationQueue = (
    NatsNotificationQueue(
        stream_name=nats_settings.webhook_stream,
        subject=nats_settings.webhook_subject,
        consumer_name=nats_settings.webhook_consumer,
    )
    if is_nats_enabled()
    else InMemoryNotificationQueue()
)


def get_webhook_queue() -> NotificationQueue:
    return webhook_queue_instance


async def initialize_queues() -> None:
    await initialize_webhook_queue()


async def initialize_webhook_queue() -> None:
    queue = webhook_queue_instance
    if isinstance(queue, NatsNotificationQueue):
        await queue.initialize(create_consumer=runtime_settings.role.runs_scheduler)


async def shutdown_webhook_queue() -> None:
    queue = webhook_queue_instance
    if isinstance(queue, NatsNotificationQueue):
        if queue._consumer:
            try:
                await queue._consumer.unsubscribe()
            except Exception:
                pass
        if queue._nc and not queue._nc.is_closed:
            try:
                await asyncio.wait_for(queue._nc.close(), timeout=3)
            except Exception:
                pass
        queue._consumer = None
        queue._js = None
        queue._nc = None


async def enqueue_webhook(payload: dict, send_at: float | None = None, tries: int = 0) -> None:
    notification = WebhookNotification(
        payload=payload,
        send_at=send_at if send_at is not None else time.time(),
        tries=tries,
    )
    try:
        await webhook_queue_instance.enqueue(notification.model_dump())
    except asyncio.QueueFull:
        logger.warning("Webhook queue full; dropping notification")
