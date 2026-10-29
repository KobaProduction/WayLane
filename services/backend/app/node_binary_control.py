"""Binary-only protobuf control protocol shared by gRPC and WebSocket adapters.

The wire never carries JSON/text/SSE frames. All database operations use a
short-lived AsyncSession; message framing and authentication are common.
"""

import asyncio
import hashlib
import hmac
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import HTTPException
from google.protobuf.json_format import MessageToDict, ParseDict
from sqlalchemy import case, select, update

from app.controlpb import control_pb2 as pb
from app.db.base import SessionLocal, begin_immediate_if_sqlite
from app.db.models import node_agent_registrations, node_agent_xray_desired

PROTOCOL_VERSION = 1
MAX_BINARY_FRAME = 2_000_000
CORE_ID = "xray/default"
logger = logging.getLogger(__name__)


def new_message(
    kind: str,
    node_uuid: str,
    *,
    generation: int = 0,
    operation: str = "",
    status: str = "",
    error_code: str = "",
    payload: dict | None = None,
    core_id: str = "",
) -> pb.Envelope:
    message = pb.Envelope(
        protocol_version=PROTOCOL_VERSION,
        node_uuid=node_uuid,
        message_id=str(uuid.uuid4()),
        kind=kind,
        core_id=core_id,
        generation=generation,
        operation=operation,
        status=status,
        error_code=error_code,
    )
    if payload is not None:
        ParseDict(payload, message.payload)
    return message


def decode_frame(frame: bytes) -> pb.Envelope:
    if not isinstance(frame, bytes) or not frame or len(frame) > MAX_BINARY_FRAME:
        raise ValueError("invalid binary frame size")
    message = pb.Envelope()
    message.ParseFromString(frame)
    if message.protocol_version != PROTOCOL_VERSION or not message.kind or not message.node_uuid:
        raise ValueError("unsupported binary control message")
    if len(message.message_id) > 64 or len(message.core_id) > 128:
        raise ValueError("invalid message identity")
    return message


async def check_agent(node_uuid: str, secret: str) -> None:
    if not secret or len(secret) > 128 or secret.startswith("wl_agent_"):
        raise HTTPException(401, "Invalid node credential")
    try:
        uuid.UUID(node_uuid)
    except (ValueError, TypeError) as exc:
        raise HTTPException(401, "Invalid node identity") from exc
    async with SessionLocal() as db:
        row = (
            await db.execute(
                select(node_agent_registrations.c.secret_hash, node_agent_registrations.c.status,
                       node_agent_registrations.c.invitation_id).where(
                    node_agent_registrations.c.node_uuid == node_uuid
                )
            )
        ).one_or_none()
        if (not row or (row[2] is None and not secret.startswith(f"wl_node_{node_uuid}_"))
                or not hmac.compare_digest(row[0], hashlib.sha256(secret.encode()).hexdigest())):
            raise HTTPException(401, "Invalid node credential")
        if row[1] != "approved":
            raise HTTPException(403, "Node not approved")


class AgentBinarySession:
    def __init__(self, node_uuid: str, secret: str):
        self.node_uuid = node_uuid
        self._secret = secret
        self._started = False
        self._last_sent_generation = 0
        self._last_seen_ids: set[str] = set()
        self.outgoing: asyncio.Queue[pb.Envelope | None] = asyncio.Queue(maxsize=8)
        self.stopped = asyncio.Event()

    async def receive(self, message: pb.Envelope):
        if message.protocol_version != PROTOCOL_VERSION or message.node_uuid != self.node_uuid:
            raise ValueError("binary control identity/version mismatch")
        if not message.message_id or len(message.message_id) > 64:
            raise ValueError("invalid message ID")
        if message.message_id in self._last_seen_ids:
            return
        if len(self._last_seen_ids) > 512:
            self._last_seen_ids.clear()
        self._last_seen_ids.add(message.message_id)
        if not self._started:
            if message.kind != "hello":
                raise ValueError("hello required before control traffic")
            await check_agent(self.node_uuid, self._secret)
            self._started = True
            await self.outgoing.put(
                new_message(
                    "hello_ack", self.node_uuid, payload={"transport_protocol": "protobuf-v1", "binary_only": True}
                )
            )
            return
        await check_agent(self.node_uuid, self._secret)
        if message.kind == "telemetry":
            payload = MessageToDict(message.payload, preserving_proto_field_name=True)
            from app.routers.node_registry import Heartbeat

            body = Heartbeat(node_uuid=uuid.UUID(self.node_uuid), **payload)
            changes = {
                "agent_version": body.node_version,
                "capabilities": body.capabilities,
                "last_seen_at": datetime.now(UTC),
            }
            if body.machine_metrics:
                changes["machine_metrics"] = body.machine_metrics.model_dump()
                # Adopt the host-machine hostname only for unnamed Classic Nodes.
                # Never override an administrator-provided display name.
                hostname = body.machine_metrics.hostname
                if hostname != "unknown-host":
                    changes["display_name"] = case(
                        (node_agent_registrations.c.display_name == f"node-{self.node_uuid}", hostname),
                        else_=node_agent_registrations.c.display_name,
                    )
            if body.reported_ipv4:
                changes["reported_ipv4"] = body.reported_ipv4
            if body.reported_ipv6:
                changes["reported_ipv6"] = body.reported_ipv6
            async with SessionLocal() as db:
                await db.execute(
                    update(node_agent_registrations)
                    .where(node_agent_registrations.c.node_uuid == self.node_uuid)
                    .values(**changes)
                )
                await db.commit()
        elif message.kind == "result":
            if message.core_id != CORE_ID or message.generation <= 0:
                raise ValueError("unknown core result")
            if message.status not in {"running", "stopped", "error", "requires_restart"}:
                raise ValueError("invalid core status")
            async with SessionLocal() as db:
                await begin_immediate_if_sqlite(db)
                row = (
                    (
                        await db.execute(
                            select(node_agent_xray_desired).where(node_agent_xray_desired.c.node_uuid == self.node_uuid)
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if not row or row["generation"] != message.generation:
                    await self.outgoing.put(
                        new_message("ack", self.node_uuid, generation=message.generation, status="stale")
                    )
                    return
                await db.execute(
                    update(node_agent_xray_desired)
                    .where(
                        node_agent_xray_desired.c.node_uuid == self.node_uuid,
                        node_agent_xray_desired.c.generation == message.generation,
                    )
                    .values(
                        applied_generation=message.generation
                        if message.status in ("running", "stopped")
                        else row["applied_generation"],
                        runtime_status=message.status,
                        last_error=message.error_code[:240] if message.error_code else None,
                        reported_at=datetime.now(UTC),
                    )
                )
                await db.commit()
            await self.outgoing.put(
                new_message("ack", self.node_uuid, generation=message.generation, status="accepted")
            )
        elif message.kind == "ping":
            await self.outgoing.put(new_message("pong", self.node_uuid))
        elif message.kind == "pong":
            pass
        else:
            raise ValueError("unsupported Agent message")

    async def push_desired(self) -> None:
        if not self._started:
            return
        await check_agent(self.node_uuid, self._secret)
        async with SessionLocal() as db:
            entry = (
                (
                    await db.execute(
                        select(node_agent_xray_desired).where(node_agent_xray_desired.c.node_uuid == self.node_uuid)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if not entry or entry["generation"] <= self._last_sent_generation:
                return
            from app.routers.node_xray_resources import agent_tls_material

            desired = {
                "configured": True,
                "generation": entry["generation"],
                "applied_generation": entry["applied_generation"],
                "action": entry["action"],
                "image_version": entry["image_version"],
                "region": entry["region"] or "",
                "config": entry["config"],
                "tls_certificates": await agent_tls_material(db, self.node_uuid),
            }
            self._last_sent_generation = int(entry["generation"])
            await self.outgoing.put(
                new_message(
                    "desired",
                    self.node_uuid,
                    core_id=CORE_ID,
                    generation=int(entry["generation"]),
                    operation=entry["action"],
                    payload=desired,
                )
            )

    async def publisher(self) -> None:
        """Send desired changes and bounded protobuf heartbeats (no text ping)."""
        elapsed = 0
        try:
            while not self.stopped.is_set():
                if self._started:
                    await self.push_desired()
                    if elapsed >= 12 and not self.outgoing.full():
                        await self.outgoing.put(new_message("ping", self.node_uuid))
                        elapsed = 0
                try:
                    await asyncio.wait_for(self.stopped.wait(), timeout=4)
                except TimeoutError:
                    elapsed += 4
        except (HTTPException, ValueError):
            self.stopped.set()

    async def outputs(self) -> AsyncIterator[pb.Envelope]:
        while not self.stopped.is_set():
            try:
                message = await asyncio.wait_for(self.outgoing.get(), timeout=3)
            except TimeoutError:
                continue
            if message is None:
                return
            yield message

    async def close(self) -> None:
        self.stopped.set()
