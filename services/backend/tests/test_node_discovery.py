"""Source-level contract tests for the additive agent registry."""
import asyncio
import secrets
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.db.base import Base
from app.db.models import node_agent_registrations
from app.routers.node_discovery import (
    DirectAgentInput, Enrollment, Heartbeat, InvitationInput, approve, create_invitation, create_direct_agent,
    enroll, heartbeat, list_registrations, list_invitations, revoke, revoke_invitation,
)


def client_request() -> Request:
    return Request({
        "type": "http", "method": "POST", "path": "/api/agent/enroll",
        "client": ("203.0.113.19", 45123),
        "headers": [(b"x-forwarded-for", b"8.8.8.8")],
    })


def test_discovery_invitation_enroll_heartbeat_approve_revoke(tmp_path):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "registry.db"))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session = async_sessionmaker(engine, expire_on_commit=False)
        uuid = uuid4()
        credential = secrets.token_urlsafe(32)
        try:
            async with session() as db:
                invitation = await create_invitation(
                    InvitationInput(label="synthetic-node-pool"), db=db, _=object())
                token = invitation["discovery_token"]
                assert token.startswith("wl_disc_")
                invitations = await list_invitations(db=db, _=object())
                assert invitations["invitations"][0]["id"] == invitation["id"]
                assert "token_hash" not in invitations["invitations"][0]
                assert invitation["expires_at"] is None
                assert invitation["auto_approve"] is True
                assert invitations["invitations"][0]["auto_approve"] == "1"
                body = Enrollment(node_uuid=uuid, agent_secret=credential,
                                  display_name="test-node", agent_version="0.1.0")
                with pytest.raises(HTTPException) as wrong:
                    await enroll(body, client_request(), x_discovery_token="bogus", db=db)
                assert wrong.value.status_code == 401

                connected = await enroll(body, client_request(), x_discovery_token=token, db=db)
                assert connected["status"] == "approved"
                repeat = await enroll(body, client_request(), x_discovery_token=token, db=db)
                assert repeat["status"] == "approved"

                with pytest.raises(HTTPException) as takeover:
                    await enroll(body.model_copy(update={"agent_secret":secrets.token_urlsafe(32)}),
                                 client_request(), x_discovery_token=token, db=db)
                assert takeover.value.status_code == 409

                update = Heartbeat(node_uuid=uuid, agent_version="0.1.0",
                                   reported_ipv4="8.8.4.4", capabilities=["docker.rootless.inspect"])
                with pytest.raises(HTTPException) as invalid:
                    await heartbeat(update, client_request(), x_agent_secret="wrong", db=db)
                assert invalid.value.status_code == 401

                first = await heartbeat(update, client_request(), x_agent_secret=credential, db=db)
                assert first == {"status":"approved", "allowed_operations":[]}
                rows = await list_registrations(db=db, _=object())
                assert len(rows["registrations"]) == 1
                assert rows["registrations"][0]["observed_ip"] == "203.0.113.19"
                assert rows["registrations"][0]["reported_ipv4"] == "8.8.4.4"
                assert "secret_hash" not in rows["registrations"][0]
                assert "token_hash" not in rows["registrations"][0]

                approved = await approve(uuid, db=db, _=object())
                assert approved["status"] == "approved"
                second = await heartbeat(update, client_request(), x_agent_secret=credential, db=db)
                assert second["status"] == "approved"
                assert second["allowed_operations"] == []
                await revoke_invitation(invitation["id"], db=db, _=object())
                assert (await list_invitations(db=db, _=object()))["invitations"][0]["revoked"] == "1"
                with pytest.raises(HTTPException) as no_new_join:
                    await enroll(Enrollment(
                        node_uuid=uuid4(), display_name="another-node",
                        agent_secret=secrets.token_urlsafe(32), agent_version="0.1.0",
                    ), client_request(), x_discovery_token=token, db=db)
                assert no_new_join.value.status_code == 401
                await revoke(uuid, db=db, _=object())
                with pytest.raises(HTTPException) as revoked:
                    await heartbeat(update, client_request(), x_agent_secret=credential, db=db)
                assert revoked.value.status_code == 403
                with pytest.raises(HTTPException) as cannot_reenroll:
                    await enroll(body, client_request(), x_discovery_token=token, db=db)
                assert cannot_reenroll.value.status_code == 401

                row=(await db.execute(select(node_agent_registrations))).mappings().one()
                assert row["secret_hash"] != credential
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_agent_ip_and_input_validation():
    for bad in ["127.0.0.1", "10.0.0.2", "not-an-ip", "fe80::1", "::1"]:
        with pytest.raises(ValidationError):
            Heartbeat(node_uuid=uuid4(), agent_version="0.1.0", reported_ipv4=bad)
    with pytest.raises(ValidationError):
        Heartbeat(node_uuid=uuid4(), agent_version="0.1.0", capabilities=["a", "a"])
    with pytest.raises(ValidationError):
        Enrollment(node_uuid=uuid4(), display_name="node", agent_version="0.1.0", agent_secret="predictable")


def test_manual_discovery_and_ip_preserved_on_temporary_lookup_failure(tmp_path):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "manual.db"))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        uuid, credential = uuid4(), secrets.token_urlsafe(32)
        try:
            async with factory() as db:
                invitation = await create_invitation(
                    InvitationInput(label="explicit-approval", auto_approve=False),
                    db=db, _=object(),
                )
                assert invitation["auto_approve"] is False
                invitations = await list_invitations(db=db, _=object())
                assert invitations["invitations"][0]["auto_approve"] == "0"
                body = Enrollment(node_uuid=uuid, display_name="manual-node",
                                  agent_version="0.1.0", agent_secret=credential)
                first = await enroll(body, client_request(),
                                     x_discovery_token=invitation["discovery_token"], db=db)
                assert first["status"] == "pending"
                valid = Heartbeat(node_uuid=uuid, agent_version="0.1.0",
                                  reported_ipv4="8.8.8.8")
                await heartbeat(valid, client_request(), x_agent_secret=credential, db=db)
                missing = Heartbeat(node_uuid=uuid, agent_version="0.1.0")
                result = await heartbeat(missing, client_request(), x_agent_secret=credential, db=db)
                assert result == {"status": "pending", "allowed_operations": []}
                row = (await db.execute(select(node_agent_registrations))).mappings().one()
                assert row["reported_ipv4"] == "8.8.8.8"
                changed = Heartbeat(node_uuid=uuid, agent_version="0.1.0",
                                    reported_ipv4="1.1.1.1")
                await heartbeat(changed, client_request(), x_agent_secret=credential, db=db)
                row = (await db.execute(select(node_agent_registrations))).mappings().one()
                assert row["reported_ipv4"] == "1.1.1.1"
                await approve(uuid, db=db, _=object())
                result = await heartbeat(missing, client_request(), x_agent_secret=credential, db=db)
                assert result == {"status": "approved", "allowed_operations": []}
        finally:
            await engine.dispose()
    asyncio.run(run())



def test_direct_agent_token_registration_and_revocation(tmp_path):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "direct.db"))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as db:
                direct = await create_direct_agent(DirectAgentInput(display_name="direct-qa-node"),
                                                   db=db, _=object())
                assert direct["status"] == "approved"
                assert direct["agent_token"].startswith("wl_agent_")
                assert len(direct["agent_token"]) == 89
                assert direct["agent_token"].startswith("wl_agent_" + direct["node_uuid"] + "_")
                preheartbeat = (await db.execute(select(node_agent_registrations).where(
                    node_agent_registrations.c.node_uuid == direct["node_uuid"]))).mappings().one()
                assert preheartbeat["last_seen_at"] is None
                assert not direct["node_uuid"].startswith("wl_disc_")
                direct_uuid = direct["node_uuid"]

                status = await heartbeat(
                    Heartbeat(node_uuid=direct_uuid, agent_version="0.1.0",
                              reported_ipv4="8.8.4.4", capabilities=["docker.rootless.inspect"]),
                    client_request(), x_agent_secret=direct["agent_token"], db=db)
                assert status == {"status": "approved", "allowed_operations": []}
                listing = await list_registrations(db=db, _=object())
                assert len(listing["registrations"]) == 1
                assert listing["registrations"][0]["enrollment_mode"] == "direct"
                assert listing["registrations"][0]["reported_ipv4"] == "8.8.4.4"
                assert "agent_token" not in listing["registrations"][0]
                assert "secret_hash" not in listing["registrations"][0]

                with pytest.raises(HTTPException) as bad:
                    await heartbeat(Heartbeat(node_uuid=direct_uuid, agent_version="0.1.0"),
                                    client_request(), x_agent_secret="invalid", db=db)
                assert bad.value.status_code == 401

                await revoke(direct_uuid, db=db, _=object())
                with pytest.raises(HTTPException) as revoked:
                    await heartbeat(Heartbeat(node_uuid=direct_uuid, agent_version="0.1.0"),
                                    client_request(), x_agent_secret=direct["agent_token"], db=db)
                assert revoked.value.status_code == 403

                other = await create_direct_agent(DirectAgentInput(display_name="direct-qa-node"),
                                                  db=db, _=object())
                assert other["node_uuid"] != direct_uuid
                assert other["agent_token"] != direct["agent_token"]
                assert (await list_registrations(db=db, _=object()))["registrations"][0]["enrollment_mode"] == "direct"
                stored = (await db.execute(select(node_agent_registrations).where(
                    node_agent_registrations.c.node_uuid == direct_uuid))).mappings().one()
                assert stored["invitation_id"] is None
                assert stored["secret_hash"] != direct["agent_token"]
        finally:
            await engine.dispose()
    asyncio.run(run())
