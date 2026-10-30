"""Binary WebSocket Node control regressions (no secrets logged)."""
import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.websockets import WebSocketDisconnect

from app import node_binary_control as control
from app.db.base import Base
from app.db.models import node_agent_registrations, node_agent_xray_desired
from app.routers.node_control_ws import router as ws_router

NODE_UUID="0192b532-184f-41bc-9952-78e15b14cc55"
SECRET="wl_node_" + NODE_UUID + "_0123456789012345678901234567890123456789012"
QA_CONFIG={"inbounds":[{"tag":"qa","protocol":"socks","port":18443,
                         "settings":{"auth":"password","accounts":[{"user":"qa","pass":"synthetic"}]}}],
           "outbounds":[{"tag":"direct","protocol":"freedom"}]}


async def seed(sessions):
    node=NODE_UUID
    async with sessions() as db:
        await db.execute(insert(node_agent_registrations).values(
            node_uuid=node,secret_hash=control.hashlib.sha256(SECRET.encode()).hexdigest(),
            invitation_id=None,display_name="qa-binary",agent_version="0.1.0",status="approved",
            capabilities=[],created_at=datetime.now(UTC),last_seen_at=None))
        await db.execute(insert(node_agent_xray_desired).values(
            node_uuid=node,generation=1,action="deploy",image_version="26.10.21",
            config=QA_CONFIG,region=None,runtime_status="pending",last_error=None,
            updated_at=datetime.now(UTC)))
        await db.commit()
    return node


@pytest.fixture
def runtime(tmp_path,monkeypatch):
    engine=create_async_engine("sqlite+aiosqlite:///"+str(tmp_path/"binary-control.db"))
    async def startup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.run(startup())
    factory=async_sessionmaker(engine,expire_on_commit=False)
    monkeypatch.setattr(control,"SessionLocal",factory)
    node=asyncio.run(seed(factory))
    yield node,factory
    asyncio.run(engine.dispose())


def test_protobuf_schema_exposes_binary_envelope_without_grpc_rpc():
    from app.controlpb import control_pb2 as pb
    assert pb.DESCRIPTOR.services_by_name == {}
    assert "Envelope" in pb.DESCRIPTOR.message_types_by_name


def test_websocket_only_binary_protobuf_frames_and_desired_state(runtime):
    """Drive the real FastAPI ASGI WebSocket without TestClient teardown races.

    Starlette's TestClient starts an anyio portal and may cancel its future
    during context-manager exit on Python 3.14.9. A deterministic ASGI harness
    preserves the same authentication, binary frames and state assertions.
    """
    node, factory = runtime
    app = FastAPI()
    app.include_router(ws_router)

    async def run_connection(secret: str | None):
        inbound = asyncio.Queue()
        outbound = asyncio.Queue()
        headers = [(b"sec-websocket-protocol", b"waylane.pb.v1")]
        if secret is not None:
            headers.append((b"x-node-secret", secret.encode()))
        scope = {
            "type": "websocket", "asgi": {"version": "3.0"},
            "path": "/api/node-control/control/ws",
            "raw_path": b"/api/node-control/control/ws",
            "query_string": f"node_uuid={node}".encode(),
            "scheme": "ws", "headers": headers,
            "subprotocols": ["waylane.pb.v1"],
            "server": ("testserver", 80), "client": ("127.0.0.1", 12345),
            "root_path": "", "state": {},
        }

        async def receive():
            return await inbound.get()

        async def send(message):
            await outbound.put(message)

        task = asyncio.create_task(app(scope, receive, send))
        await inbound.put({"type": "websocket.connect"})
        first = await asyncio.wait_for(outbound.get(), timeout=5)
        return first, inbound, outbound, task

    async def scenario():
        denied, _, _, denied_task = await run_connection(None)
        assert denied["type"] == "websocket.close"
        assert denied["code"] == 1008
        await asyncio.wait_for(denied_task, timeout=5)

        accepted, inbound, outbound, task = await run_connection(SECRET)
        assert accepted["type"] == "websocket.accept"
        assert accepted["subprotocol"] == "waylane.pb.v1"

        async def send_frame(frame):
            await inbound.put({"type": "websocket.receive", "bytes": frame.SerializeToString()})

        async def next_frame():
            message = await asyncio.wait_for(outbound.get(), timeout=8)
            assert message["type"] == "websocket.send", message
            assert message.get("text") is None
            return control.decode_frame(message["bytes"])

        await send_frame(control.new_message("hello", node))
        ack = await next_frame()
        assert ack.kind == "hello_ack"
        assert ack.payload.fields["binary_only"].bool_value is True

        desired = await next_frame()
        assert desired.kind == "desired" and desired.generation == 1
        assert desired.operation == "deploy" and desired.core_id == "xray/default"
        assert desired.payload.fields["config"].struct_value.fields["inbounds"].list_value.values

        response = control.new_message("result", node, generation=1,
                                       core_id="xray/default", status="running")
        await send_frame(response)
        confirmation = await next_frame()
        assert confirmation.kind == "ack" and confirmation.status == "accepted"
        await inbound.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(scenario())

    async def inspect():
        async with factory() as db:
            row = await db.execute(node_agent_xray_desired.select())
            return row.mappings().first()
    row = asyncio.run(inspect())
    assert row["applied_generation"] == 1 and row["runtime_status"] == "running"


def test_classic_node_uses_host_machine_name_only_when_not_customized(runtime):
    node, factory = runtime

    async def check():
        async with factory() as db:
            await db.execute(
                node_agent_registrations.update()
                .where(node_agent_registrations.c.node_uuid == node)
                .values(display_name="node-" + node)
            )
            await db.commit()
        session = control.AgentBinarySession(node, SECRET)
        await session.receive(control.new_message("hello", node))
        machine = {
            "scope": "agent-runtime", "hostname": "eu-test-host-01",
            "os": "linux", "arch": "amd64", "cpu_logical_count": 2,
            "memory_total_bytes": 1024, "memory_available_bytes": 512,
            "disk_total_bytes": 1024, "disk_free_bytes": 512,
            "load_1": 0, "uptime_seconds": 12
        }
        await session.receive(control.new_message("telemetry", node, payload={
            "node_version": "0.1.0", "capabilities": [], "machine_metrics": machine
        }))
        async with factory() as db:
            stored = (await db.execute(
                node_agent_registrations.select()
                .where(node_agent_registrations.c.node_uuid == node)
            )).mappings().one()
            assert stored["display_name"] == "eu-test-host-01"
            await db.execute(
                node_agent_registrations.update()
                .where(node_agent_registrations.c.node_uuid == node)
                .values(display_name="Operator's name")
            )
            await db.commit()
        machine["hostname"] = "new-machine-host"
        await session.receive(control.new_message("telemetry", node, payload={
            "node_version": "0.1.0", "capabilities": [], "machine_metrics": machine
        }))
        async with factory() as db:
            stored = (await db.execute(node_agent_registrations.select())).mappings().one()
            assert stored["display_name"] == "Operator's name"
        await session.close()

    asyncio.run(check())


def test_rejected_websocket_agent_cannot_handshake(runtime):
    node,_=runtime
    app=FastAPI()
    app.include_router(ws_router)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/api/node-control/control/ws?node_uuid={node}",
                                          headers={"x-node-secret":"invalid"},
                                          subprotocols=["waylane.pb.v1"]):
                pass


def test_reject_obsolete_credential_even_if_its_hash_matches(runtime):
    """A saved wl_agent_ credential is not a compatibility login route."""
    from fastapi import HTTPException
    node, factory = runtime
    obsolete = "wl_agent_" + node + "_0123456789012345678901234567890123456789012"

    async def run():
        async with factory() as db:
            await db.execute(
                node_agent_registrations.update().where(
                    node_agent_registrations.c.node_uuid == node
                ).values(secret_hash=control.hashlib.sha256(obsolete.encode()).hexdigest())
            )
            await db.commit()
        with pytest.raises(HTTPException) as exc:
            await control.check_agent(node, obsolete)
        assert exc.value.status_code == 401

    asyncio.run(run())
