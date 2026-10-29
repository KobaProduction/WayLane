"""Optional gRPC bidirectional Protobuf stream for the same Agent protocol."""

import asyncio
import ipaddress
import logging
import os
from pathlib import Path

import grpc
from fastapi import HTTPException

from app.node_binary_control import PROTOCOL_VERSION, AgentBinarySession, check_agent
from app.controlpb import control_pb2_grpc as grpc_pb
from app.lifecycle import on_shutdown, on_startup

logger = logging.getLogger(__name__)
_server: grpc.aio.Server | None = None


class ControlServicer(grpc_pb.AgentControlServicer):
    async def Connect(self, request_iterator, context):
        metadata = dict(context.invocation_metadata())
        node_uuid = metadata.get("x-node-uuid", "")
        secret = metadata.get("x-node-secret", "")
        try:
            await check_agent(node_uuid, secret)
        except HTTPException as exc:
            await context.abort(
                grpc.StatusCode.PERMISSION_DENIED if exc.status_code == 403 else grpc.StatusCode.UNAUTHENTICATED,
                "Agent unauthorized",
            )

        session = AgentBinarySession(node_uuid, secret)

        async def consume():
            try:
                async for incoming in request_iterator:
                    if incoming.ByteSize() > 2_000_000 or incoming.protocol_version != PROTOCOL_VERSION:
                        raise ValueError("Invalid protobuf envelope")
                    await session.receive(incoming)
            except (ValueError, HTTPException):
                await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Invalid Agent binary command")
            finally:
                await session.close()

        reader = asyncio.create_task(consume())
        publisher = asyncio.create_task(session.publisher())
        try:
            async for outgoing in session.outputs():
                yield outgoing
        finally:
            await session.close()
            for task in (reader, publisher):
                task.cancel()
            await asyncio.gather(reader, publisher, return_exceptions=True)


async def create_grpc_server(bind: str, *, cert: bytes | None = None, key: bytes | None = None) -> grpc.aio.Server:
    server = grpc.aio.server(
        options=[
            ("grpc.max_receive_message_length", 2_000_000),
            ("grpc.max_send_message_length", 2_000_000),
            ("grpc.keepalive_time_ms", 30000),
        ]
    )
    grpc_pb.add_AgentControlServicer_to_server(ControlServicer(), server)
    if cert is not None and key is not None:
        bound = server.add_secure_port(bind, grpc.ssl_server_credentials([(key, cert)]))
    else:
        host = bind.rsplit(":", 1)[0].strip("[]")
        if ipaddress.ip_address(host).is_loopback is False:
            raise ValueError("plaintext gRPC may bind only loopback; use TLS for remote access")
        bound = server.add_insecure_port(bind)
    if not bound:
        raise ValueError("Unable to bind binary gRPC control listener")
    await server.start()
    return server


@on_startup
async def start_grpc_control() -> None:
    global _server
    bind = os.getenv("CONTROL_GRPC_BIND", "")
    if not bind:
        return
    cert_path = os.getenv("CONTROL_GRPC_TLS_CERT")
    key_path = os.getenv("CONTROL_GRPC_TLS_KEY")
    if bool(cert_path) != bool(key_path):
        raise ValueError("gRPC TLS requires both certificate and key")
    cert = await asyncio.to_thread(Path(cert_path).read_bytes) if cert_path else None
    key = await asyncio.to_thread(Path(key_path).read_bytes) if key_path else None
    _server = await create_grpc_server(bind, cert=cert, key=key)
    logger.info("WayLane binary gRPC control listener started")


@on_shutdown
async def stop_grpc_control() -> None:
    global _server
    if _server is not None:
        await _server.stop(grace=3)
        _server = None
