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
    Enrollment, Heartbeat, InvitationInput, approve, create_invitation,
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
                body = Enrollment(node_uuid=uuid, agent_secret=credential,
                                  display_name="test-node", agent_version="0.1.0")
                with pytest.raises(HTTPException) as wrong:
                    await enroll(body, client_request(), x_discovery_token="bogus", db=db)
                assert wrong.value.status_code == 401

                pending = await enroll(body, client_request(), x_discovery_token=token, db=db)
                assert pending["status"] == "pending"
                repeat = await enroll(body, client_request(), x_discovery_token=token, db=db)
                assert repeat["status"] == "pending"

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
                assert first == {"status":"pending", "allowed_operations":[]}
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
