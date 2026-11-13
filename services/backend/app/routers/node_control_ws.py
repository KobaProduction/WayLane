"""Agent-initiated WebSocket binary Protobuf control transport."""

import asyncio
from uuid import UUID

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.db.base import SessionLocal
from app.node_binary_control import MAX_BINARY_FRAME, AgentBinarySession, check_agent, decode_frame
from app.node_events import append_node_event
from app.utils.logger import get_logger

router = APIRouter(prefix="/api/node-control", tags=["Node agent binary control"])
logger = get_logger("node-control-wss")


async def record_connection_event(node_uuid: str, kind: str) -> None:
    """Best-effort sanitized diagnostics; WSS availability is independent of logging."""
    try:
        async with SessionLocal() as db:
            await append_node_event(db, node_uuid, kind=kind, status=kind)
            await db.commit()
    except Exception:
        logger.warning("Node WSS event history unavailable (%s)", kind)


@router.websocket("/control/ws")
async def control_websocket(ws: WebSocket):
    uuid = ws.query_params.get("node_uuid", "")
    secret = ws.headers.get("x-node-secret", "")
    if ws.headers.get("origin"):
        # Agent uses a native client. Never accept browser-origin credential reuse.
        await ws.close(code=1008)
        return
    offered = [part.strip() for part in ws.headers.get("sec-websocket-protocol", "").split(",")]
    if "waylane.pb.v1" not in offered:
        await ws.close(code=1008)
        return
    try:
        UUID(uuid)
        await check_agent(uuid, secret)
    except (HTTPException, ValueError):
        await ws.close(code=1008)
        return
    await ws.accept(subprotocol="waylane.pb.v1")
    await record_connection_event(uuid, "connected")
    session = AgentBinarySession(uuid, secret)

    async def read_frames():
        try:
            while not session.stopped.is_set():
                event = await ws.receive()
                if event.get("type") == "websocket.disconnect":
                    return
                frame = event.get("bytes")
                if frame is None:
                    raise ValueError("WebSocket text frames are prohibited")
                await session.receive(decode_frame(frame))
        except WebSocketDisconnect:
            pass
        except (ValueError, HTTPException):
            await ws.close(code=1008, reason="Binary control protocol rejected")
        finally:
            await session.close()

    async def send_frames():
        async for message in session.outputs():
            frame = message.SerializeToString()
            if len(frame) > MAX_BINARY_FRAME:
                await ws.close(code=1009)
                break
            await ws.send_bytes(frame)

    tasks = [
        asyncio.create_task(read_frames()),
        asyncio.create_task(send_frames()),
        asyncio.create_task(session.publisher()),
    ]
    try:
        # Revocation can terminate the publisher while the socket reader is
        # otherwise idle: never leave a revoked Agent connected indefinitely.
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        await session.close()
        await record_connection_event(uuid, "disconnected")
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # A peer-initiated disconnect (or an earlier protocol rejection) has
        # already closed the WebSocket. Sending a second close can race with
        # TestClient/ASGI teardown and raise a transport cancellation.
        if (ws.client_state is not WebSocketState.DISCONNECTED
                and ws.application_state is not WebSocketState.DISCONNECTED):
            try:
                await ws.close(code=1000)
            except (OSError, RuntimeError, WebSocketDisconnect):
                # Transport vanished between the state check and close.
                pass
