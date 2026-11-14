"""Operator-triggered Node-only releases and WSS reconnects stay UUID-scoped."""
import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import node_binary_control as control
from app.db.base import Base
from app.db.models import node_agent_operations, node_agent_registrations
from app.node_image_repositories import (
    GHCR_NODE_REPOSITORY,
    approved_node_image,
    trusted_node_repositories,
    valid_repository,
)
from app.node_operations import (
    NodeOperationInput,
    NodeOperationReport,
    queue_node_operation,
)
from app.routers.node_registry import (
    Heartbeat,
    create_classic_node,
    list_node_release_repositories,
    get_node_lifecycle_operation,
    trigger_node_lifecycle_operation,
)

IMAGE = "ghcr.io/kobaproduction/waylane-node@sha256:" + "a" * 64


@pytest.mark.parametrize("use_custom_registry", [False, True])
def test_node_operator_release_and_rollback_status_over_binary_wss(tmp_path, monkeypatch, use_custom_registry):
    image = IMAGE
    if use_custom_registry:
        image = "registry.example.com:5000/waylane/node@sha256:" + "b" * 64
        monkeypatch.setenv("WAYLANE_NODE_EXTRA_IMAGE_REPOSITORY", "registry.example.com:5000/waylane/node")
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/operation.db")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(control, "SessionLocal", factory)
        try:
            async with factory() as db:
                node = await create_classic_node(db=db, _=object())
                uid = UUID(node["node_uuid"])
                await db.execute(update(node_agent_registrations).where(
                    node_agent_registrations.c.node_uuid == str(uid)
                ).values(last_seen_at=datetime.now(UTC),
                         capabilities=["xray.protobuf.binary", "node.release.v1", "node.reconnect.v1"]))
                await db.commit()
                queued = await trigger_node_lifecycle_operation(
                    uid, NodeOperationInput(action="update", image=image, expected_generation=0), db=db, _=object())
                assert queued["status"] == "pending" and queued["generation"] == 1
                assert queued["image"] == image
                with pytest.raises(HTTPException) as duplicate:
                    await trigger_node_lifecycle_operation(uid, NodeOperationInput(
                        action="reconnect", expected_generation=1), db=db, _=object())
                assert duplicate.value.status_code == 409
                await db.rollback()
                private = await get_node_lifecycle_operation(uid, db=db, _=object())
                assert private["status"] == "pending"
                assert "secret" not in str(private).lower()
                session = control.AgentBinarySession(str(uid), node["node_token"])
                await session.receive(control.new_message("hello", str(uid)))
                assert (await session.outgoing.get()).kind == "hello_ack"
                await session.receive(control.new_message("telemetry", str(uid), payload={
                    "node_version": "0.1.0", "node_image": "waylane-node-seed:bootstrap",
                    "capabilities": ["xray.protobuf.binary", "node.release.v1", "node.reconnect.v1"],
                }))
                await session.push_desired()
                frame = await session.outgoing.get()
                assert frame.kind == "node_action"
                assert frame.operation == "update" and frame.core_id == "node/runtime"
                assert frame.payload.fields["image"].string_value == image
                operation = queued["operation_id"]
                await session.receive(control.new_message("node_action_report", str(uid), core_id="node/runtime",
                    generation=1, payload={"operation_id":operation,"image":image,"previous_image":"waylane-node-seed:bootstrap",
                                           "status":"running","phase":"awaiting_wss","progress":85}))
                async with factory() as inspect:
                    state = (await inspect.execute(select(node_agent_operations))).mappings().one()
                    assert state["status"] == "running" and state["progress"] == 85
                await session.receive(control.new_message("node_action_report", str(uid), core_id="node/runtime",
                    generation=0, payload={"operation_id":operation,"image":image,"previous_image":"waylane-node-seed:bootstrap",
                                           "status":"rolled_back","phase":"rollback","progress":100,"error_code":"wss_ready_timeout"}))
                async with factory() as inspect:
                    state = (await inspect.execute(select(node_agent_operations))).mappings().one()
                    assert state["status"] == "rolled_back" and state["error_code"] == "wss_ready_timeout"
                await session.receive(control.new_message("node_action_report", str(uid), core_id="node/runtime",
                    generation=1, payload={"operation_id":operation,"image":image,"status":"succeeded",
                                           "phase":"completed","progress":100}))
                async with factory() as inspect:
                    state = (await inspect.execute(select(node_agent_operations))).mappings().one()
                    assert state["status"] == "rolled_back"  # terminal state is immutable
                await session.close()
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_manual_wss_reconnect_requires_new_authenticated_hello(tmp_path, monkeypatch):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/reconnect.db")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(control, "SessionLocal", factory)
        try:
            async with factory() as db:
                node = await create_classic_node(db=db, _=object())
                uid = UUID(node["node_uuid"])
                await db.execute(update(node_agent_registrations).where(
                    node_agent_registrations.c.node_uuid == str(uid)
                ).values(last_seen_at=datetime.now(UTC),capabilities=["node.reconnect.v1"]))
                await db.commit()
                queued = await queue_node_operation(db, uid, NodeOperationInput(
                    action="reconnect", expected_generation=0))
                first = control.AgentBinarySession(str(uid), node["node_token"])
                await first.receive(control.new_message("hello", str(uid)))
                await first.outgoing.get()
                await first.receive(control.new_message("telemetry", str(uid), payload={
                    "node_version": "0.1.0", "capabilities":["node.reconnect.v1"]}))
                await first.push_desired()
                command = await first.outgoing.get()
                assert command.kind == "node_action" and command.operation == "reconnect"
                await first.receive(control.new_message("node_action_report", str(uid), core_id="node/runtime",
                    generation=1, payload={"operation_id":queued["operation_id"],
                                           "status":"running", "phase":"reconnecting", "progress":50}))
                async with factory() as inspect:
                    state = (await inspect.execute(select(node_agent_operations))).mappings().one()
                    assert state["status"] == "running"
                await first.close()
                second = control.AgentBinarySession(str(uid),node["node_token"])
                await second.receive(control.new_message("hello",str(uid)))
                await second.outgoing.get()
                async with factory() as inspect:
                    state = (await inspect.execute(select(node_agent_operations))).mappings().one()
                    assert state["status"] == "succeeded" and state["progress"] == 100
                await second.close()
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_operator_input_rejects_mutable_images_and_untrusted_errors():
    for image in ("ghcr.io/kobaproduction/waylane-node:latest", "ubuntu:latest", "", "https://evil"):
        with pytest.raises(ValidationError):
            NodeOperationInput(action="update", image=image, expected_generation=0)
    with pytest.raises(ValidationError):
        NodeOperationInput(action="restart", expected_generation=0)
    with pytest.raises(ValidationError):
        NodeOperationReport(operation_id=uuid4(), status="running", phase="shell",
                            progress=10, error_code="unexpected command text")


def test_stale_release_expiration_unblocks_explicit_operator_retry(tmp_path):
    from datetime import timedelta
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/stale-operation.db")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as db:
                node = await create_classic_node(db=db, _=object())
                uid = UUID(node["node_uuid"])
                await db.execute(update(node_agent_registrations).where(
                    node_agent_registrations.c.node_uuid == str(uid)
                ).values(last_seen_at=datetime.now(UTC), capabilities=["node.release.v1", "node.reconnect.v1"]))
                await db.commit()
                await queue_node_operation(db, uid, NodeOperationInput(
                    action="update", image=IMAGE, expected_generation=0))
                await db.execute(update(node_agent_operations).where(
                    node_agent_operations.c.node_uuid == str(uid)
                ).values(created_at=datetime.now(UTC) - timedelta(minutes=11)))
                await db.commit()
                expired = await get_node_lifecycle_operation(uid, db=db, _=object())
                assert expired["status"] == "failed"
                assert expired["error_code"] == "node_report_timeout"
                renewed = await queue_node_operation(db, uid, NodeOperationInput(
                    action="reconnect", expected_generation=1))
                assert renewed["generation"] == 2 and renewed["status"] == "pending"
        finally:
            await engine.dispose()
    asyncio.run(run())


CUSTOM_REPO = "registry.example.com:5000/waylane/node"
CUSTOM_IMAGE = CUSTOM_REPO + "@sha256:" + "b" * 64


def test_two_trusted_node_repositories_and_immutable_control_images(monkeypatch):
    monkeypatch.delenv("WAYLANE_NODE_EXTRA_IMAGE_REPOSITORY", raising=False)
    assert trusted_node_repositories() == [GHCR_NODE_REPOSITORY]
    assert approved_node_image(IMAGE)
    assert not approved_node_image(CUSTOM_IMAGE)
    with pytest.raises(ValidationError):
        NodeOperationInput(action="update", image=CUSTOM_IMAGE, expected_generation=0)

    monkeypatch.setenv("WAYLANE_NODE_EXTRA_IMAGE_REPOSITORY", CUSTOM_REPO)
    assert trusted_node_repositories() == [GHCR_NODE_REPOSITORY, CUSTOM_REPO]
    assert asyncio.run(list_node_release_repositories(_=object())) == {
        "repositories": [GHCR_NODE_REPOSITORY, CUSTOM_REPO],
    }
    assert NodeOperationInput(action="update", image=CUSTOM_IMAGE, expected_generation=0).image == CUSTOM_IMAGE
    assert NodeOperationReport(operation_id=uuid4(), image=CUSTOM_IMAGE,
                               previous_image=IMAGE, status="running",
                               phase="awaiting_wss", progress=85).image == CUSTOM_IMAGE
    assert Heartbeat(node_uuid=uuid4(), node_version="0.1.0", node_image=CUSTOM_IMAGE).node_image == CUSTOM_IMAGE
    assert approved_node_image(IMAGE)  # Custom registry does not disable GHCR.
    for invalid in (CUSTOM_REPO + ":latest", CUSTOM_REPO + "@sha256:" + "A" * 64,
                    "evil.example.com/waylane/node@sha256:" + "b" * 64,
                    "https://" + CUSTOM_IMAGE, CUSTOM_IMAGE + "/extra"):
        assert not approved_node_image(invalid)
        with pytest.raises(ValidationError):
            NodeOperationInput(action="update", image=invalid, expected_generation=0)

    monkeypatch.setenv("WAYLANE_NODE_EXTRA_IMAGE_REPOSITORY", "other.example.org/waylane/node")
    assert not approved_node_image(CUSTOM_IMAGE)
    assert approved_node_image(IMAGE)


def test_invalid_registry_configuration_fails_closed(monkeypatch):
    for invalid in ("https://registry.example.com/waylane/node", "unqualified/node",
                    "registry.example.com/../node", "registry.example.com//node",
                    "registry.example.com/waylane/node:latest",
                    "registry.example.com/" + "x" * 55):
        assert not valid_repository(invalid)
        monkeypatch.setenv("WAYLANE_NODE_EXTRA_IMAGE_REPOSITORY", invalid)
        with pytest.raises(ValueError):
            trusted_node_repositories()
