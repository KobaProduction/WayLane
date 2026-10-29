"""Agent-initiated WebSocket binary Protobuf control transport."""

import asyncio
from uuid import UUID

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app.node_binary_control import MAX_BINARY_FRAME, AgentBinarySession, check_agent, decode_frame

router = APIRouter(prefix="/api/node-control", tags=["Node agent binary control"])


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
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await ws.close(code=1000)
        except RuntimeError:
            pass
