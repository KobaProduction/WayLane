"""Shared GHCR credentials and Discovery invitation settings on isolated SQLite."""
import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import node_binary_control as control
from app.db.base import Base
from app.db.models import node_agent_registrations, node_shared_registry_credentials
from app.node_image_repositories import GHCR_NODE_REPOSITORY
from app.node_operations import NodeOperationInput, queue_node_operation
from app.node_registry_settings import RegistryUpdate, public_registry_list, save_node_registry, transport_registry
from app.node_shared_ghcr import SharedGHCRWrite, clear_shared_ghcr, save_shared_ghcr, shared_ghcr_metadata
from app.routers.node_registry import create_classic_node

IMAGE = GHCR_NODE_REPOSITORY + "@sha256:" + "a" * 64


def test_ghcr_token_write_only_and_node_wss_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("WAYLANE_TLS_SECRET_KEY", Fernet.generate_key().decode())
    fixture = "synthetic-credential-42"

    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/shared.db")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(control, "SessionLocal", factory)
        try:
            async with factory() as db:
                first = await create_classic_node(db=db, _=object())
                second = await create_classic_node(db=db, _=object())
                assert not (await shared_ghcr_metadata(db))["configured"]
                metadata = await save_shared_ghcr(db, SharedGHCRWrite(username="robot", token=fixture))
                assert metadata["configured"] and "token" not in metadata
                assert fixture not in str(metadata)
                encrypted = await db.scalar(select(node_shared_registry_credentials.c.encrypted_token))
                assert encrypted and fixture not in encrypted
                for node in (first, second):
                    profile = (await public_registry_list(db, UUID(node["node_uuid"])))["registries"][0]
                    assert profile["credential_scope"] == "shared" and profile["has_credentials"]
                    assert fixture not in str(profile)
                    assert (await transport_registry(db, node["node_uuid"], IMAGE))["password"] == fixture
                await save_node_registry(db, UUID(first["node_uuid"]), RegistryUpdate(
                    repository=GHCR_NODE_REPOSITORY, username="local", password="local-test-value"))
                assert (await transport_registry(db, first["node_uuid"], IMAGE))["username"] == "local"
                await save_shared_ghcr(db, SharedGHCRWrite(username="rotated", token="replaced-test-value"))
                assert (await transport_registry(db, second["node_uuid"], IMAGE))["password"] == "replaced-test-value"
                await db.execute(update(node_agent_registrations).where(
                    node_agent_registrations.c.node_uuid == second["node_uuid"]
                ).values(last_seen_at=datetime.now(UTC), capabilities=["node.release.v1"]))
                await db.commit()
                queued = await queue_node_operation(db, UUID(second["node_uuid"]), NodeOperationInput(
                    action="update", image=IMAGE, expected_generation=0))
                assert "replaced-test-value" not in str(queued)
                session = control.AgentBinarySession(second["node_uuid"], second["node_token"])
                await session.receive(control.new_message("hello", second["node_uuid"]))
                assert (await session.outgoing.get()).kind == "hello_ack"
                await session.receive(control.new_message("telemetry", second["node_uuid"], payload={
                    "node_version": "0.1.0", "capabilities": ["node.release.v1"],
                }))
                await session.push_desired()
                frame = await session.outgoing.get()
                assert frame.kind == "node_action"
                assert frame.payload.fields["username"].string_value == "rotated"
                assert frame.payload.fields["password"].string_value == "replaced-test-value"
                await session.close()
                cleared = await clear_shared_ghcr(db)
                assert not cleared["configured"]
                assert not (await transport_registry(db, second["node_uuid"], IMAGE))["password"]
                assert (await transport_registry(db, first["node_uuid"], IMAGE))["password"] == "local-test-value"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_discovery_rotation_and_settings_are_one_way(tmp_path):
    import secrets
    from uuid import uuid4
    from fastapi import HTTPException
    from starlette.requests import Request
    from app.routers.node_registry import (
        InvitationInput, InvitationSettings, InvitationRotate, Enrollment,
        create_invitation, update_invitation, rotate_invitation, list_invitations, enroll,
    )
    async def run():
        engine=create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/discovery.db")
        async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
        factory=async_sessionmaker(engine,expire_on_commit=False)
        request=Request({"type":"http", "method":"POST", "path":"/api/node-control/enroll", "client":("203.0.113.5",1234), "headers":[]})
        try:
            async with factory() as db:
                invitation=await create_invitation(InvitationInput(label="QA discovery",auto_approve=True),db=db,_=object())
                old=invitation["discovery_token"]
                node=Enrollment(node_uuid=uuid4(),node_secret=secrets.token_urlsafe(32),display_name="fixture-node",node_version="0.1.0")
                assert (await enroll(node,request,x_discovery_token=old,db=db))["status"]=="approved"
                await update_invitation(invitation["id"],InvitationSettings(label="New name",enabled=False,auto_approve=False),db=db,_=object())
                data=await list_invitations(db=db,_=object())
                assert data["invitations"][0]["label"]=="New name"
                assert data["invitations"][0]["revoked"]=="1"
                assert data["invitations"][0]["auto_approve"]=="0"
                assert old not in str(data)
                fresh=await rotate_invitation(invitation["id"],InvitationRotate(lifetime_hours=24),db=db,_=object())
                assert fresh["discovery_token"]!=old and fresh["expires_at"] is not None
                with pytest.raises(HTTPException) as blocked:
                    await enroll(node.model_copy(update={"node_uuid":uuid4()}),request,x_discovery_token=fresh["discovery_token"],db=db)
                assert blocked.value.status_code==401
                await update_invitation(invitation["id"],InvitationSettings(enabled=True),db=db,_=object())
                with pytest.raises(HTTPException) as revoked:
                    await enroll(node.model_copy(update={"node_uuid":uuid4()}),request,x_discovery_token=old,db=db)
                assert revoked.value.status_code==401
                next_node=Enrollment(node_uuid=uuid4(),node_secret=secrets.token_urlsafe(32),display_name="next",node_version="0.1.0")
                assert (await enroll(next_node,request,x_discovery_token=fresh["discovery_token"],db=db))["status"]=="pending"
                preserved=await db.scalar(select(node_agent_registrations.c.status).where(node_agent_registrations.c.node_uuid==str(node.node_uuid)))
                assert preserved=="approved"
        finally: await engine.dispose()
    asyncio.run(run())
