"""UUID-first commercial subscription reconciliation for autonomous WayLane Nodes.

Commercial state remains in the database. No legacy Node RPC, user queue,
transport-specific Protobuf user message or implicit bridge process exists.
An explicit WSS-managed inbound receives only the current committed policy.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence

from app.db import GetDB
from app.db.models import Subscription
from app.node_subscription_desired import enqueue_managed_subscription_changes

logger = logging.getLogger(__name__)

_pending_reconciles: set[asyncio.Task] = set()


async def node_payload_signature(subscription: Subscription) -> tuple[str, bytes, tuple[str, ...]]:
    """Compare product admission without depending on the removed RPC schema."""
    groups = subscription.__dict__.get("groups")
    tags: set[str] = set()
    if groups is None:
        tags = set(await subscription.inbounds())
    else:
        for group in groups:
            if group.is_disabled:
                continue
            inbounds = group.__dict__.get("inbounds")
            if inbounds is None:
                tags = set(await subscription.inbounds())
                break
            tags.update(inbound.tag for inbound in inbounds)
    # A client is present only if current admission permits it. Unrelated
    # metadata, an unchanged quota, or inactive-account status changes are
    # not a reason to reconfigure a running VPN Core.
    from app.db.models import SubscriptionStatus

    eligible = (
        subscription.entitlement == "active"
        and subscription.status in (SubscriptionStatus.active, SubscriptionStatus.on_hold)
        and not subscription.is_expired
        and not subscription.is_limited
        and bool(tags)
    )
    payload = (subscription.proxy_settings or {}) if eligible else {}
    return (
        str(subscription.uuid),
        json.dumps(payload, sort_keys=True, default=str).encode(),
        tuple(sorted(tags)) if eligible else (),
    )


async def reconcile_subscriptions() -> int:
    """Queue at most one new desired generation per opted-in autonomous Node."""
    async with GetDB() as db:
        return await enqueue_managed_subscription_changes(db)


async def sync_subscription_access(_: Subscription) -> None:
    """Synchronous API boundary after the commercial transaction commits."""
    await reconcile_subscriptions()


async def _notify_after_commit() -> None:
    try:
        await reconcile_subscriptions()
    except Exception:
        logger.exception("Autonomous Node admission reconcile failed")


def _schedule_reconcile() -> None:
    task = asyncio.create_task(_notify_after_commit())
    _pending_reconciles.add(task)
    task.add_done_callback(_pending_reconciles.discard)


async def sync_subscription(_: Subscription) -> None:
    _schedule_reconcile()


async def remove_subscription(_subscription) -> None:
    _schedule_reconcile()


async def remove_subscription_awaited(_subscription) -> None:
    await reconcile_subscriptions()


async def sync_subscriptions(
    subscriptions: Sequence[Subscription],
    *,
    await_dispatch: bool = False,
) -> None:
    if not subscriptions:
        return
    if await_dispatch:
        await reconcile_subscriptions()
    else:
        _schedule_reconcile()


async def remove_subscriptions(
    subscriptions: Sequence[Subscription],
    *,
    await_dispatch: bool = False,
) -> None:
    await sync_subscriptions(subscriptions, await_dispatch=await_dispatch)
