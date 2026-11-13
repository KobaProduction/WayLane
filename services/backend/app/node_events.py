"""Sanitized, UUID-first WayLane Node lifecycle event history.

Events capture metadata only. Never persist raw Protobuf payloads, Xray
configuration, client identifiers, tokens or private certificate material.
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import node_agent_events

EVENT_KINDS = frozenset({
    "connected", "disconnected", "approved", "revoked",
    "xray_result", "awg_result", "node_error", "desired_queued",
})
STATUSES = frozenset({
    "running", "stopped", "pending", "error", "requires_restart",
    "accepted", "connected", "disconnected",
})
MAX_EVENTS_PER_NODE = 1000


async def append_node_event(
    db: AsyncSession,
    node_uuid: str,
    *,
    kind: str,
    status: str | None = None,
    core_id: str | None = None,
    generation: int | None = None,
) -> None:
    """Append one whitelisted event without committing the caller's transaction."""
    if kind not in EVENT_KINDS or (status is not None and status not in STATUSES):
        raise ValueError("unsupported sanitized Node event")
    if core_id not in (None, "xray/default", "awg/default"):
        raise ValueError("unsupported Node event Core")
    if generation is not None and (generation < 0 or generation > 2_147_483_647):
        raise ValueError("invalid Node event generation")
    await db.execute(insert(node_agent_events).values(
        event_uuid=str(uuid4()),
        node_uuid=node_uuid,
        created_at=datetime.now(UTC),
        kind=kind,
        status=status,
        core_id=core_id,
        generation=generation,
    ))
    # Retain a bounded diagnostic history per Node without a polling job.
    stale = (
        select(node_agent_events.c.event_uuid)
        .where(node_agent_events.c.node_uuid == node_uuid)
        .order_by(node_agent_events.c.created_at.desc(),
                  node_agent_events.c.event_uuid.desc())
        .offset(MAX_EVENTS_PER_NODE)
    )
    await db.execute(delete(node_agent_events).where(
        node_agent_events.c.event_uuid.in_(stale)
    ))
