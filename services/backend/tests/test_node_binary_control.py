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
    node,factory=runtime
    app=FastAPI()
    app.include_router(ws_router)
    with TestClient(app) as client:
        path=f"/api/node-control/control/ws?node_uuid={node}"
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(path,subprotocols=["waylane.pb.v1"]):
                pass
        with client.websocket_connect(path,headers={"x-node-secret":SECRET},
                                      subprotocols=["waylane.pb.v1"]) as ws:
            assert ws.accepted_subprotocol=="waylane.pb.v1"
            ws.send_bytes(control.new_message("hello",node).SerializeToString())
            ack=control.decode_frame(ws.receive_bytes())
            assert ack.kind=="hello_ack"
            assert ack.payload.fields["binary_only"].bool_value is True
            desired=control.decode_frame(ws.receive_bytes())
            assert desired.kind=="desired" and desired.generation==1
            assert desired.operation=="deploy" and desired.core_id=="xray/default"
            assert desired.payload.fields["config"].struct_value.fields["inbounds"].list_value.values
            response=control.new_message("result",node,generation=1,
                                         core_id="xray/default",status="running")
            ws.send_bytes(response.SerializeToString())
            accepted=control.decode_frame(ws.receive_bytes())
            assert accepted.kind=="ack" and accepted.status=="accepted"
    async def inspect():
        async with factory() as db:
            row=await db.execute(node_agent_xray_desired.select())
            return row.mappings().first()
    row=asyncio.run(inspect())
    assert row["applied_generation"]==1 and row["runtime_status"]=="running"


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
