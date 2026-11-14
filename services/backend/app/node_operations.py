"""Single operator-owned lifecycle lane for the replaceable inner WayLane Node.

The stable Agent's private Unix API is the only Docker owner. This module
stores sanitized operation generations and never executes Docker or stores a
Node enrollment credential. Operator input is restricted to official digests.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import begin_immediate_if_sqlite
from app.db.models import node_agent_operations, node_agent_registrations, node_agent_registry_settings
from app.node_image_repositories import GHCR_NODE_REPOSITORY, image_repository, valid_pinned_image

OP_ID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ACTIVE_STATUSES = frozenset({"pending", "queued", "running"})
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "rolled_back"})
PHASES = frozenset({"queued", "pulling", "replacing", "awaiting_wss", "rollback", "rollback_verify",
                    "interrupted", "completed", "request", "reconnecting"})
ERROR_CODES = frozenset({"image_unavailable", "replacement_failed", "wss_ready_timeout",
                         "rollback_failed", "agent_restarted_during_update",
                         "private_agent_unavailable", "reconnect_failed", "request_uncertain", "node_report_timeout", "rollback_unverified"})


class NodeOperationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["update", "reconnect"]
    expected_generation: int = Field(ge=0)
    image: str | None = None

    @field_validator("image")
    @classmethod
    def official_immutable_image(cls, value: str | None) -> str | None:
        if value is not None and not valid_pinned_image(value):
            raise ValueError("Approved immutable WayLane Node digest required")
        return value


class NodeOperationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: UUID
    image: str | None = None
    previous_image: str | None = None
    status: Literal["queued", "running", "succeeded", "failed", "rolled_back"]
    phase: str = Field(min_length=1, max_length=32)
    progress: int = Field(ge=0, le=100)
    error_code: str | None = None

    @field_validator("image", "previous_image")
    @classmethod
    def known_images(cls, value: str | None) -> str | None:
        if value not in (None, "", "waylane-node-seed:bootstrap") and not valid_pinned_image(value):
            raise ValueError("Untrusted release image in Agent report")
        return value or None

    @field_validator("phase")
    @classmethod
    def known_phase(cls, value: str) -> str:
        if value not in PHASES:
            raise ValueError("Untrusted release phase")
        return value

    @field_validator("error_code")
    @classmethod
    def known_error(cls, value: str | None) -> str | None:
        if value and value not in ERROR_CODES:
            raise ValueError("Untrusted release error code")
        return value or None


# Longer than Agent's bounded pull, replacement, new WSS verification and rollback.
# Resolve abandoned operations only when a relevant request/connection is active.
MAX_OPERATION_AGE = timedelta(minutes=10)


async def expire_stale_node_operation(db: AsyncSession, node_uuid: str) -> bool:
    """Expire abandoned release commands without acquiring a write lock normally."""
    row = (await db.execute(
        select(node_agent_operations.c.status, node_agent_operations.c.created_at)
        .where(node_agent_operations.c.node_uuid == node_uuid)
    )).one_or_none()
    if row is None or row.status not in ACTIVE_STATUSES:
        return False
    started = row.created_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    now = datetime.now(UTC)
    if started >= now - MAX_OPERATION_AGE:
        return False
    changed = await db.execute(
        update(node_agent_operations)
        .where(node_agent_operations.c.node_uuid == node_uuid)
        .where(node_agent_operations.c.status.in_(tuple(ACTIVE_STATUSES)))
        .where(node_agent_operations.c.created_at < now - MAX_OPERATION_AGE)
        .values(status="failed", phase="expired", progress=100,
                error_code="node_report_timeout", updated_at=now)
    )
    if changed.rowcount:
        await db.commit()
        return True
    return False


def as_dict(row) -> dict:
    if row is None:
        return {"generation": 0, "status": "idle", "action": None,
                "operation_id": None, "progress": 0, "phase": "idle", "error_code": None,
                "image": None, "previous_image": None}
    return {field: row[field] for field in (
        "operation_id", "generation", "action", "image", "previous_image",
        "status", "phase", "progress", "error_code", "created_at",
        "updated_at", "reported_at")}


async def queue_node_operation(db: AsyncSession, node_uuid: UUID, input: NodeOperationInput) -> dict:
    if input.action == "update" and input.image is None:
        raise HTTPException(422, "An approved immutable Node image is required")
    if input.action == "reconnect" and input.image is not None:
        raise HTTPException(422, "Reconnect must not replace the Node image")
    await begin_immediate_if_sqlite(db)
    row = (await db.execute(
        select(node_agent_registrations.c.status, node_agent_registrations.c.capabilities,
               node_agent_registrations.c.last_seen_at)
        .where(node_agent_registrations.c.node_uuid == str(node_uuid)).with_for_update()
    )).one_or_none()
    if row is None or row.status != "approved":
        raise HTTPException(409, "Node must be approved")
    if input.action == "update" and input.image is not None:
        repository = image_repository(input.image)
        if repository != GHCR_NODE_REPOSITORY:
            allowed = await db.scalar(select(node_agent_registry_settings.c.repository).where(
                node_agent_registry_settings.c.node_uuid == str(node_uuid),
                node_agent_registry_settings.c.repository == repository,
            ))
            if not allowed:
                raise HTTPException(422, "This registry is not configured for the selected Node")
    capability = "node.release.v1" if input.action == "update" else "node.reconnect.v1"
    if capability not in (row.capabilities or []):
        raise HTTPException(409, "Node has not negotiated the private Agent lifecycle capability")
    seen = row.last_seen_at
    if seen is not None and seen.tzinfo is None:
        seen = seen.replace(tzinfo=UTC)
    if seen is None or seen < datetime.now(UTC) - timedelta(seconds=90):
        raise HTTPException(409, "Node WSS heartbeat is not fresh")
    current = (await db.execute(select(node_agent_operations)
        .where(node_agent_operations.c.node_uuid == str(node_uuid)).with_for_update())).mappings().one_or_none()
    if current is not None and current["status"] in ACTIVE_STATUSES:
        started = current["created_at"]
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        if started < datetime.now(UTC) - MAX_OPERATION_AGE:
            await db.execute(update(node_agent_operations)
                .where(node_agent_operations.c.node_uuid == str(node_uuid))
                .values(status="failed", phase="expired", progress=100,
                        error_code="node_report_timeout", updated_at=datetime.now(UTC)))
            current = {**current, "status": "failed"}
    generation = int(current["generation"]) if current else 0
    if generation != input.expected_generation:
        raise HTTPException(409, "Node operation generation changed; reload status")
    if current is not None and current["status"] in ACTIVE_STATUSES:
        raise HTTPException(409, "Another Node lifecycle operation is active")
    now = datetime.now(UTC)
    values = {
        "operation_id": str(uuid4()), "generation": generation + 1,
        "action": input.action, "image": input.image, "previous_image": None,
        "status": "pending", "phase": "queued", "progress": 0,
        "error_code": None, "created_at": now, "updated_at": now, "reported_at": None,
    }
    if current:
        await db.execute(update(node_agent_operations).where(
            node_agent_operations.c.node_uuid == str(node_uuid)).values(**values))
    else:
        await db.execute(insert(node_agent_operations).values(node_uuid=str(node_uuid), **values))
    await db.commit()
    return {"node_uuid": str(node_uuid), **values}


async def accept_node_operation_report(db: AsyncSession, node_uuid: str,
                                       generation: int, report: NodeOperationReport) -> bool:
    await begin_immediate_if_sqlite(db)
    current = (await db.execute(select(node_agent_operations).where(
        node_agent_operations.c.node_uuid == node_uuid).with_for_update())).mappings().one_or_none()
    if current is None or current["operation_id"] != str(report.operation_id):
        return False
    if generation not in (0, current["generation"]):
        return False
    if current["status"] in TERMINAL_STATUSES:
        return False
    if current["action"] == "update":
        if current["image"] != report.image:
            return False
    elif report.phase not in {"reconnecting", "completed"}:
        return False
    if report.progress < current["progress"] and report.status not in TERMINAL_STATUSES:
        return False
    await db.execute(update(node_agent_operations).where(
        node_agent_operations.c.node_uuid == node_uuid,
        node_agent_operations.c.operation_id == str(report.operation_id)).values(
        status=report.status,
        phase=report.phase, progress=report.progress,
        error_code=report.error_code, previous_image=report.previous_image,
        reported_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    ))
    await db.commit()
    return True


async def complete_node_reconnect(db: AsyncSession, node_uuid: str) -> None:
    await db.execute(update(node_agent_operations).where(
        node_agent_operations.c.node_uuid == node_uuid,
        node_agent_operations.c.action == "reconnect",
        node_agent_operations.c.status == "running",
        node_agent_operations.c.phase == "reconnecting",
    ).values(status="succeeded", phase="completed", progress=100,
             updated_at=datetime.now(UTC), reported_at=datetime.now(UTC)))
    await db.commit()
