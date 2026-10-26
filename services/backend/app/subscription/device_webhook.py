"""Signed, durable, best-effort-first device event delivery.

Record events in the *same transaction* as HWID modifications. Request handling
does not wait for arbitrary third-party callbacks. Undelivered events persist
until the configured scheduler role retries due events. One-shot manual
recovery stays available to operators without running monitoring here.
"""
import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from sqlalchemy import and_, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import GetDB
from app.db.subscription_firewall import subscription_device_events
from app.subscription.firewall import load_config

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 10
RETRY_BASE_SECONDS = 30
RETRY_MAX_SECONDS = 3600
RETRY_BATCH_LIMIT = 25
RETRY_CONCURRENCY = 4


def retry_delay_seconds(attempts: int) -> int:
    """Deterministic exponential backoff; first failure waits 30 seconds."""
    return min(RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * (2 ** max(0, attempts - 1)))


def due_event_filter(now: datetime):
    """Return unclaimed, retry-eligible events; crash leases can expire."""
    return or_(
        subscription_device_events.c.status == "pending",
        and_(
            subscription_device_events.c.status == "failed",
            or_(
                subscription_device_events.c.next_attempt_at.is_(None),
                subscription_device_events.c.next_attempt_at <= now,
            ),
        ),
        and_(
            subscription_device_events.c.status == "sending",
            subscription_device_events.c.lease_until < now,
        ),
    )



def validate_webhook_url(url: str) -> str:
    """Forbid redirects, credentials and private-address SSRF in production."""
    parts = urlsplit(url)
    if (parts.scheme not in ("https", "http") or not parts.hostname or
            parts.username or parts.password or parts.fragment):
        raise ValueError("Webhook requires a valid HTTP(S) URL without credentials/fragment")
    host = parts.hostname.lower()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    local_dev = os.getenv("WAYLANE_WEBHOOK_ALLOW_LOCAL_TEST") == "1"
    # Legacy bot/webhook receivers may be exposed only on an internal Coolify
    # network. Require explicit server-side host allowlisting, never a blind
    # exemption for private IPs or localhost based on a submitted URL.
    trusted = {value.strip().lower() for value in
               os.getenv("WAYLANE_WEBHOOK_ALLOWED_INTERNAL_HOSTS", "").split(",") if value.strip()}
    allow_internal = host in trusted or (local_dev and host in ("localhost", "127.0.0.1", "::1"))
    if parts.scheme != "https" and not allow_internal:
        raise ValueError("Webhook URL must use HTTPS or an explicitly allowlisted internal host")
    if address and not address.is_global and not allow_internal:
        raise ValueError("Private IP address webhook destinations are prohibited")
    if host in {"localhost", "localhost.localdomain"} and not allow_internal:
        raise ValueError("Loopback webhook destinations are prohibited")
    return url


async def enqueue_device_event(db: AsyncSession, event_type: str, user_id: int,
                               hwid: str | None, details: dict | None = None) -> str | None:
    # Do not permanently accumulate HWID data or webhooks while the feature is
    # disabled. When enabled, delivery failures remain durably retriable.
    if not (await load_config(db))["webhook_enabled"]:
        return None
    event_id = str(uuid4())
    payload = {
        "id": event_id, "type": event_type, "user_id": user_id,
        "hwid": hwid, "details": details or {},
        "occurred_at": datetime.now(UTC).isoformat(),
    }
    await db.execute(insert(subscription_device_events).values(
        id=event_id, event_type=event_type, user_id=user_id,
        hwid=hwid, payload=payload, status="pending",
    ))
    return event_id


async def deliver_device_event(event_id: str, *, force: bool = False) -> bool:
    """One delivery attempt, or an explicit manual retry bypassing the delay.

    Claiming and finalizing are fenced by an attempt-specific lease token, so
    parallel schedulers do not duplicate active attempts. The receiver must
    still deduplicate IDs after lost HTTP responses (at-least-once delivery).
    """
    secret = os.getenv("WAYLANE_DEVICE_WEBHOOK_SECRET", "")
    async with GetDB() as db:
        settings = await load_config(db)
        if not settings["webhook_enabled"] or not settings["webhook_url"] or len(secret) < 32:
            return False

        now = datetime.now(UTC)
        claim_token = str(uuid4())
        eligible = (
            or_(
                subscription_device_events.c.status.in_(("pending", "failed")),
                and_(
                    subscription_device_events.c.status == "sending",
                    subscription_device_events.c.lease_until < now,
                ),
            )
            if force else due_event_filter(now)
        )
        stmt = (
            update(subscription_device_events)
            .where(
                subscription_device_events.c.id == event_id,
                subscription_device_events.c.attempts < MAX_ATTEMPTS,
                eligible,
            )
            .values(
                status="sending",
                delivery_token=claim_token,
                lease_until=now + timedelta(seconds=15),
                next_attempt_at=None,
                attempts=subscription_device_events.c.attempts + 1,
            )
            .returning(
                subscription_device_events.c.payload,
                subscription_device_events.c.attempts,
            )
        )
        claimed = (await db.execute(stmt)).one_or_none()
        if claimed is None:
            await db.rollback()
            return False
        await db.commit()
        payload = dict(claimed.payload)
        attempt = claimed.attempts
        url = settings["webhook_url"]

    try:
        validate_webhook_url(url)
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        timestamp = str(int(datetime.now(UTC).timestamp()))
        signature = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        async with httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False) as client:
            response = await client.post(url, content=body, headers={
                "Content-Type": "application/json",
                "X-WayLane-Event": payload["type"],
                "X-WayLane-Event-ID": payload["id"],
                "X-WayLane-Timestamp": timestamp,
                "X-WayLane-Signature": "sha256=" + signature,
            })
        response.raise_for_status()
        ok = True
        error = None
    except httpx.HTTPStatusError as exc:
        ok = False
        # Log only a numeric response status, not headers or bodies that may
        # contain secrets from the recipient.
        error = f"HTTP_{exc.response.status_code}"
    except (httpx.HTTPError, ValueError) as exc:
        ok = False
        error = type(exc).__name__  # No secrets, payloads or arbitrary server responses in logs.

    finished_at = datetime.now(UTC)
    exhausted = not ok and attempt >= MAX_ATTEMPTS
    next_attempt_at = None if ok or exhausted else (
        finished_at + timedelta(seconds=retry_delay_seconds(attempt))
    )
    async with GetDB() as db:
        finalized = await db.execute(
            update(subscription_device_events)
            .where(
                subscription_device_events.c.id == event_id,
                subscription_device_events.c.status == "sending",
                subscription_device_events.c.delivery_token == claim_token,
            )
            .values(
                status="delivered" if ok else ("exhausted" if exhausted else "failed"),
                lease_until=None, delivery_token=None,
                next_attempt_at=next_attempt_at,
                last_error=error,
                delivered_at=finished_at if ok else None,
            )
        )
        await db.commit()
    if finalized.rowcount:
        if exhausted:
            logger.error(
                "Device webhook exhausted retries: event_id=%s attempts=%s reason=%s",
                event_id, attempt, error,
            )
        elif not ok:
            logger.warning(
                "Device webhook delivery failed: event_id=%s attempt=%s reason=%s next_retry_at=%s",
                event_id, attempt, error, next_attempt_at.isoformat(),
            )
        else:
            logger.info("Device webhook delivered: event_id=%s attempt=%s", event_id, attempt)
    return ok and bool(finalized.rowcount)


def dispatch_after_commit(event_id: str) -> None:
    """Start one attempt on the active request loop; durable DB row survives crashes."""
    async def attempt():
        try:
            await deliver_device_event(event_id)
        except Exception:
            logger.exception("Device webhook event failed (id=%s)", event_id)
    asyncio.create_task(attempt())


async def retry_due_device_events(limit: int = RETRY_BATCH_LIMIT) -> dict:
    """Bounded scheduler pass; no busy loop or unbounded flood after a restart."""
    now = datetime.now(UTC)
    async with GetDB() as db:
        # A crashed 10th delivery must not remain in "sending" forever.
        finished = await db.execute(
            update(subscription_device_events)
            .where(
                subscription_device_events.c.attempts >= MAX_ATTEMPTS,
                or_(
                    subscription_device_events.c.status == "failed",
                    and_(
                        subscription_device_events.c.status == "sending",
                        subscription_device_events.c.lease_until < now,
                    ),
                ),
            )
            .values(
                status="exhausted", lease_until=None,
                delivery_token=None, next_attempt_at=None,
            )
        )
        if finished.rowcount:
            await db.commit()
            logger.error("Device webhooks exhausted after recovery: count=%s", finished.rowcount)
        settings = await load_config(db)
        if not settings["webhook_enabled"] or not settings["webhook_url"]:
            return {"selected": 0, "delivered": 0, "failed": 0}
        events = (await db.scalars(
            select(subscription_device_events.c.id)
            .where(
                due_event_filter(now),
                subscription_device_events.c.attempts < MAX_ATTEMPTS,
            )
            .order_by(
                subscription_device_events.c.next_attempt_at,
                subscription_device_events.c.created_at,
                subscription_device_events.c.id,
            )
            .limit(max(1, min(limit, 100)))
        )).all()
    semaphore = asyncio.Semaphore(RETRY_CONCURRENCY)

    async def attempt(event_id: str) -> bool:
        async with semaphore:
            try:
                return bool(await deliver_device_event(event_id))
            except Exception as exc:
                # A failed row stays pending or leased until expiry; do not
                # stop unrelated events because one delivery encounters an
                # unexpected DB/transport error. Never log raw event payloads.
                logger.error("Device webhook retry worker error: event_id=%s error_type=%s",
                             event_id, type(exc).__name__)
                return False

    results = await asyncio.gather(*(attempt(event_id) for event_id in events))
    delivered = sum(results)
    if events:
        logger.info(
            "Device webhook retry batch: selected=%s delivered=%s deferred_or_failed=%s",
            len(events), delivered, len(events) - delivered,
        )
    return {"selected": len(events), "delivered": delivered, "failed": len(events) - delivered}


async def requeue_exhausted_event(event_id: str) -> bool:
    """Explicit operator recovery resets a permanently exhausted event."""
    async with GetDB() as db:
        changed = await db.execute(
            update(subscription_device_events)
            .where(
                subscription_device_events.c.id == event_id,
                subscription_device_events.c.status == "exhausted",
            )
            .values(
                status="pending", attempts=0, last_error=None,
                next_attempt_at=None, lease_until=None, delivery_token=None,
            )
        )
        await db.commit()
    if changed.rowcount:
        logger.warning("Device webhook manually requeued: event_id=%s", event_id)
    return bool(changed.rowcount)


async def retry_pending_events(limit: int = 50) -> dict:
    """Explicit admin replay of pending/failed events without their backoff delay."""
    async with GetDB() as db:
        events = (await db.scalars(
            select(subscription_device_events.c.id)
            .where(
                subscription_device_events.c.status.in_(("pending", "failed")),
                subscription_device_events.c.attempts < MAX_ATTEMPTS,
            )
            .order_by(
                subscription_device_events.c.created_at,
                subscription_device_events.c.id,
            )
            .limit(max(1, min(limit, 100)))
        )).all()
    delivered = 0
    for event_id in events:
        delivered += bool(await deliver_device_event(event_id, force=True))
    return {"selected": len(events), "delivered": delivered, "failed": len(events) - delivered}


async def check_legacy_device_access(
    db: AsyncSession,
    *,
    token: str,
    ip: str,
    hwid: str | None,
    user_agent: str,
    device_os: str | None,
    os_version: str | None,
    device_model: str | None,
) -> None:
    """Honor the old bot /webhook/check_device decision protocol when configured.

    BOT_ERROR/unreachable remains fail-open as in the legacy firewall; WayLane's
    own device count/HWID limits continue to apply regardless of this decision.
    """
    if not hwid:
        return
    config = await load_config(db)
    url = config.get("device_check_url")
    if not url:
        return
    validate_webhook_url(url)
    payload = {
        "subscription": token,
        "ip": ip,
        "hwid": {
            "user_agent": user_agent, "hwid": hwid,
            "os": device_os or "", "os_version": os_version or "",
            "device_locale": "", "app_version": "",
        },
    }
    try:
        async with httpx.AsyncClient(timeout=2, trust_env=False, follow_redirects=False) as client:
            response = await client.post(url, json=payload)
        if response.status_code != 200:
            return
        status = response.json().get("status", "BOT_ERROR")
    except (httpx.HTTPError, ValueError):
        return
    if status in ("LIMIT_DEVICES", "ACCESS_DENIED"):
        from fastapi import HTTPException
        if status == "LIMIT_DEVICES":
            raise HTTPException(403, "Device limit reached")
        raise HTTPException(403, "Access denied by device-check webhook")
