"""Generic outgoing notification events; no product user/contact payloads.

Device-registration callbacks are separately persisted and HMAC-signed by
:mod:`app.subscription.device_webhook` and must remain independent of this queue.
"""

from datetime import UTC, datetime as dt
from typing import Any
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from app.models.subscription_models import SubscriptionNotificationResponse
from app.notification.queue_manager import enqueue_webhook
from app.settings import webhook_settings


def get_current_timestamp() -> float:
    return dt.now(UTC).timestamp()


class Notification(BaseModel):
    enqueued_at: float = Field(default_factory=get_current_timestamp)
    send_at: float = Field(default_factory=get_current_timestamp)
    tries: int = Field(default=0)


class PanelEvent(Notification):
    """A secret-free event addressed by the canonical subscription UUID."""

    action: str
    resource: str
    resource_id: str | int | None = None
    subscription_uuid: UUID | None = None
    actor: str | None = None
    details: dict[str, Any] | None = None


async def status_change(subscription: SubscriptionNotificationResponse) -> None:
    await notify(
        PanelEvent(
            action=f"subscription.status.{subscription.status.value}",
            resource="subscription",
            subscription_uuid=subscription.uuid,
        )
    )


async def notify(message: Notification | dict) -> None:
    if (await webhook_settings()).enable:
        await enqueue_webhook(jsonable_encoder(message))


async def bulk_notify(messages: list[Notification | dict]) -> None:
    if (await webhook_settings()).enable:
        for message in messages:
            await enqueue_webhook(jsonable_encoder(message))
