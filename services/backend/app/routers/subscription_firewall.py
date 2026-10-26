"""Admin editor for embedded Subscription Firewall messages and signed device webhooks."""

import os
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.db.subscription_firewall import subscription_firewall_config, subscription_device_events
from app.models.admin import AdminDetails
from app.subscription.device_webhook import (
    requeue_exhausted_event, retry_pending_events, validate_webhook_url,
)
from app.subscription.firewall import SCENARIOS, load_config, render_message

from .authentication import require_permission

router = APIRouter(prefix="/api/subscription-firewall", tags=["Subscription Firewall"])

Scenario = Literal[
    "invalid_link", "device_limit", "hwid_required", "unsupported_client",
    "rate_limited", "access_denied", "bot_unavailable",
]


class MessageTemplate(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    lines: list[str] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def check_text(self):
        if not self.title.strip() or any(not item.strip() or len(item) > 256 for item in self.lines):
            raise ValueError("Message lines must contain 1–256 nonblank characters")
        return self


class FirewallEditor(BaseModel):
    enabled: bool = False
    scenarios: dict[Scenario, MessageTemplate]
    webhook_url: str | None = Field(default=None, max_length=2048)
    webhook_enabled: bool = False
    device_check_url: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def ensure_webhook_configuration(self):
        for url in (self.webhook_url, self.device_check_url):
            if url:
                validate_webhook_url(url)
        if self.webhook_enabled and not self.webhook_url:
            raise ValueError("Webhook target URL is required when delivery is enabled")
        return self


@router.get("", response_model=FirewallEditor)
async def get_firewall_editor(
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("settings", "read")),
):
    data = await load_config(db)
    return FirewallEditor(**{key: value for key, value in data.items() if key in FirewallEditor.model_fields})


@router.put("", response_model=FirewallEditor)
async def save_firewall_editor(
    body: FirewallEditor,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("settings", "update")),
):
    if body.webhook_enabled and len(os.getenv("WAYLANE_DEVICE_WEBHOOK_SECRET", "")) < 32:
        raise HTTPException(422, "Set WAYLANE_DEVICE_WEBHOOK_SECRET (at least 32 characters) on the backend")
    data = body.model_dump(mode="json")
    await db.execute(delete(subscription_firewall_config).where(subscription_firewall_config.c.id == 1))
    await db.execute(insert(subscription_firewall_config).values(id=1, **data))
    await db.commit()
    return body


@router.post("/preview/{scenario}")
async def preview_message(
    scenario: Scenario,
    body: MessageTemplate,
    _: AdminDetails = Depends(require_permission("settings", "read")),
):
    response = render_message(body.title, body.lines)
    return {
        "title": body.title, "lines": body.lines,
        "base64_payload": response.body.decode("ascii"), "announce": response.headers["announce"],
    }


@router.get("/device-events")
async def get_device_event_status(
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("settings", "read")),
):
    rows = (await db.execute(
        select(
            subscription_device_events.c.id, subscription_device_events.c.event_type,
            subscription_device_events.c.status, subscription_device_events.c.attempts,
            subscription_device_events.c.last_error, subscription_device_events.c.created_at,
            subscription_device_events.c.delivered_at,
            subscription_device_events.c.next_attempt_at,
        ).order_by(subscription_device_events.c.created_at.desc()).limit(100)
    )).mappings().all()
    return [dict(row) for row in rows]


@router.post("/retry-device-events")
async def retry_device_events(
    _: AdminDetails = Depends(require_permission("settings", "update")),
):
    return await retry_pending_events()


@router.get("/device-events/summary")
async def get_device_event_summary(
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("settings", "read")),
):
    rows = (await db.execute(
        select(subscription_device_events.c.status, func.count())
        .group_by(subscription_device_events.c.status)
    )).all()
    return {status: count for status, count in rows}


@router.post("/device-events/{event_id}/requeue")
async def requeue_device_event(
    event_id: UUID,
    _: AdminDetails = Depends(require_permission("settings", "update")),
):
    if not await requeue_exhausted_event(str(event_id)):
        raise HTTPException(409, "Only exhausted device events can be requeued")
    return {"status": "pending", "event_id": str(event_id)}
