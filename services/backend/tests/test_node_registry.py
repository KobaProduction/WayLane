"""Source-level contract tests for the additive agent registry."""
import asyncio
import secrets
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.db.base import Base
from app.db.models import node_agent_registrations
from app.routers.node_registry import (
    Enrollment, Heartbeat, InvitationInput, approve, create_invitation, create_classic_node,
    XrayDesiredInput, XrayReport, set_desired_xray, get_desired_xray, agent_xray_desired, agent_xray_report,
    enroll, heartbeat, list_registrations, list_invitations, revoke, revoke_invitation,
)


def client_request() -> Request:
    return Request({
        "type": "http", "method": "POST", "path": "/api/node-control/enroll",
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
                    InvitationInput(label="synthetic-node-pool", auto_approve=True), db=db, _=object())
                token = invitation["discovery_token"]
                assert token.startswith("wl_disc_")
                invitations = await list_invitations(db=db, _=object())
                assert invitations["invitations"][0]["id"] == invitation["id"]
                assert "token_hash" not in invitations["invitations"][0]
                assert invitation["expires_at"] is None
                assert invitation["auto_approve"] is True
                assert invitations["invitations"][0]["auto_approve"] == "1"
                body = Enrollment(node_uuid=uuid, node_secret=credential,
                                  display_name="test-node", node_version="0.1.0")
                with pytest.raises(HTTPException) as wrong:
                    await enroll(body, client_request(), x_discovery_token="bogus", db=db)
                assert wrong.value.status_code == 401

                connected = await enroll(body, client_request(), x_discovery_token=token, db=db)
                assert connected["status"] == "approved"
                repeat = await enroll(body, client_request(), x_discovery_token=token, db=db)
                assert repeat["status"] == "approved"

                with pytest.raises(HTTPException) as takeover:
                    await enroll(body.model_copy(update={"node_secret":secrets.token_urlsafe(32)}),
                                 client_request(), x_discovery_token=token, db=db)
                assert takeover.value.status_code == 409

                update = Heartbeat(node_uuid=uuid, node_version="0.1.0",
                                   reported_ipv4="8.8.4.4", capabilities=["docker.rootless.inspect"])
                with pytest.raises(HTTPException) as invalid:
                    await heartbeat(update, client_request(), x_node_secret="wrong", db=db)
                assert invalid.value.status_code == 401

                first = await heartbeat(update, client_request(), x_node_secret=credential, db=db)
                assert first == {"status":"approved", "allowed_operations":[]}
                rows = await list_registrations(db=db, _=object())
                assert len(rows["nodes"]) == 1
                assert rows["nodes"][0]["observed_ip"] == "203.0.113.19"
                assert rows["nodes"][0]["connection_mode"] == "discovery"
                assert rows["nodes"][0]["reported_ipv4"] == "8.8.4.4"
                assert "secret_hash" not in rows["nodes"][0]
                assert "token_hash" not in rows["nodes"][0]

                approved = await approve(uuid, db=db, _=object())
                assert approved["status"] == "approved"
                second = await heartbeat(update, client_request(), x_node_secret=credential, db=db)
                assert second["status"] == "approved"
                assert second["allowed_operations"] == []
                await revoke_invitation(invitation["id"], db=db, _=object())
                assert (await list_invitations(db=db, _=object()))["invitations"][0]["revoked"] == "1"
                with pytest.raises(HTTPException) as no_new_join:
                    await enroll(Enrollment(
                        node_uuid=uuid4(), display_name="another-node",
                        node_secret=secrets.token_urlsafe(32), node_version="0.1.0",
                    ), client_request(), x_discovery_token=token, db=db)
                assert no_new_join.value.status_code == 401
                await revoke(uuid, db=db, _=object())
                with pytest.raises(HTTPException) as revoked:
                    await heartbeat(update, client_request(), x_node_secret=credential, db=db)
                assert revoked.value.status_code == 403
                with pytest.raises(HTTPException) as cannot_reenroll:
                    await enroll(body, client_request(), x_discovery_token=token, db=db)
                assert cannot_reenroll.value.status_code == 401

                row=(await db.execute(select(node_agent_registrations))).mappings().one()
                assert row["secret_hash"] != credential
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_agent_runtime_metrics_validation():
    from app.routers.node_registry import NodeMachineMetrics
    valid = NodeMachineMetrics(
        hostname="qa-agent", os="linux", arch="amd64",
        cpu_logical_count=4, memory_total_bytes=4 * 1024**3,
        memory_available_bytes=2 * 1024**3, disk_total_bytes=100 * 1024**3,
        disk_free_bytes=80 * 1024**3, load_1=0.25, uptime_seconds=90,
    )
    assert Heartbeat(node_uuid=uuid4(),node_version="0.1.0",machine_metrics=valid).machine_metrics.scope == "agent-runtime"
    with pytest.raises(ValidationError):
        NodeMachineMetrics(hostname="invalid",os="linux",arch="amd64",cpu_logical_count=-1,
            memory_total_bytes=0,memory_available_bytes=0,disk_total_bytes=0,disk_free_bytes=0,
            load_1=0,uptime_seconds=0)
    with pytest.raises(ValidationError):
        NodeMachineMetrics(scope="host",hostname="invalid",os="linux",arch="amd64",cpu_logical_count=2,
            memory_total_bytes=0,memory_available_bytes=0,disk_total_bytes=0,disk_free_bytes=0,
            load_1=0,uptime_seconds=0)


def test_agent_ip_and_input_validation():
    for bad in ["127.0.0.1", "10.0.0.2", "not-an-ip", "fe80::1", "::1"]:
        with pytest.raises(ValidationError):
            Heartbeat(node_uuid=uuid4(), node_version="0.1.0", reported_ipv4=bad)
    with pytest.raises(ValidationError):
        Heartbeat(node_uuid=uuid4(), node_version="0.1.0", capabilities=["a", "a"])
    with pytest.raises(ValidationError):
        Enrollment(node_uuid=uuid4(), display_name="node", node_version="0.1.0", node_secret="predictable")


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
                                  node_version="0.1.0", node_secret=credential)
                first = await enroll(body, client_request(),
                                     x_discovery_token=invitation["discovery_token"], db=db)
                assert first["status"] == "pending"
                valid = Heartbeat(node_uuid=uuid, node_version="0.1.0",
                                  reported_ipv4="8.8.8.8")
                await heartbeat(valid, client_request(), x_node_secret=credential, db=db)
                missing = Heartbeat(node_uuid=uuid, node_version="0.1.0")
                result = await heartbeat(missing, client_request(), x_node_secret=credential, db=db)
                assert result == {"status": "pending", "allowed_operations": []}
                row = (await db.execute(select(node_agent_registrations))).mappings().one()
                assert row["reported_ipv4"] == "8.8.8.8"
                changed = Heartbeat(node_uuid=uuid, node_version="0.1.0",
                                    reported_ipv4="1.1.1.1")
                await heartbeat(changed, client_request(), x_node_secret=credential, db=db)
                row = (await db.execute(select(node_agent_registrations))).mappings().one()
                assert row["reported_ipv4"] == "1.1.1.1"
                await approve(uuid, db=db, _=object())
                result = await heartbeat(missing, client_request(), x_node_secret=credential, db=db)
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
                direct = await create_classic_node(db=db, _=object())
                assert direct["status"] == "approved"
                assert direct["node_token"].startswith("wl_node_")
                assert len(direct["node_token"]) == 88
                assert direct["node_token"].startswith("wl_node_" + direct["node_uuid"] + "_")
                preheartbeat = (await db.execute(select(node_agent_registrations).where(
                    node_agent_registrations.c.node_uuid == direct["node_uuid"]))).mappings().one()
                assert preheartbeat["display_name"] == "node-" + direct["node_uuid"]
                assert preheartbeat["last_seen_at"] is None
                assert not direct["node_uuid"].startswith("wl_disc_")
                direct_uuid = direct["node_uuid"]

                status = await heartbeat(
                    Heartbeat(node_uuid=direct_uuid, node_version="0.1.0",
                              reported_ipv4="8.8.4.4", capabilities=["docker.rootless.inspect"]),
                    client_request(), x_node_secret=direct["node_token"], db=db)
                assert status == {"status": "approved", "allowed_operations": []}
                listing = await list_registrations(db=db, _=object())
                assert len(listing["nodes"]) == 1
                assert listing["nodes"][0]["connection_mode"] == "classic"
                assert listing["nodes"][0]["reported_ipv4"] == "8.8.4.4"
                assert "node_token" not in listing["nodes"][0]
                assert "secret_hash" not in listing["nodes"][0]

                with pytest.raises(HTTPException) as bad:
                    await heartbeat(Heartbeat(node_uuid=direct_uuid, node_version="0.1.0"),
                                    client_request(), x_node_secret="invalid", db=db)
                assert bad.value.status_code == 401

                await revoke(direct_uuid, db=db, _=object())
                with pytest.raises(HTTPException) as revoked:
                    await heartbeat(Heartbeat(node_uuid=direct_uuid, node_version="0.1.0"),
                                    client_request(), x_node_secret=direct["node_token"], db=db)
                assert revoked.value.status_code == 403

                unnamed = await create_classic_node(db=db, _=object())
                unnamed_row = (await db.execute(select(node_agent_registrations).where(
                    node_agent_registrations.c.node_uuid == unnamed["node_uuid"]))).mappings().one()
                assert unnamed_row["display_name"] == "node-" + unnamed["node_uuid"]
                other = await create_classic_node(db=db, _=object())
                assert other["node_uuid"] != direct_uuid
                assert other["node_token"] != direct["node_token"]
                assert (await list_registrations(db=db, _=object()))["nodes"][0]["connection_mode"] == "classic"
                stored = (await db.execute(select(node_agent_registrations).where(
                    node_agent_registrations.c.node_uuid == direct_uuid))).mappings().one()
                assert stored["invitation_id"] is None
                assert stored["secret_hash"] != direct["node_token"]
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_xray_desired_generation_and_authentication(tmp_path):
    """Admin sets desired Xray; only approved Agent may retrieve and report."""
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "xray.db"))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as db:
                agent = await create_classic_node(db=db, _=object())
                node_uuid = UUID(agent["node_uuid"])
                config = {"inbounds": [{"listen": "127.0.0.1", "port": 18443,
                                       "protocol": "socks", "settings": {"auth": "password",
                                       "accounts": [{"user": "qa", "pass": "synthetic"}]}}],
                          "outbounds": [{"protocol": "freedom"}]}
                initial = await get_desired_xray(node_uuid, db=db, _=object())
                assert initial == {"configured": False}
                desired = await set_desired_xray(
                    node_uuid, XrayDesiredInput(action="deploy", config=config, region="test"),
                    db=db, _=object())
                assert desired["generation"] == 1 and desired["image_version"] == "26.10.21"
                with pytest.raises(HTTPException) as missing_auth:
                    await agent_xray_desired(node_uuid, x_node_secret="wrong", db=db)
                assert missing_auth.value.status_code == 401
                fetched = await agent_xray_desired(
                    node_uuid, x_node_secret=agent["node_token"], db=db)
                assert fetched["config"] == config
                assert fetched["action"] == "deploy"
                reported = await agent_xray_report(
                    XrayReport(node_uuid=node_uuid, generation=1, status="running"),
                    x_node_secret=agent["node_token"], db=db)
                assert reported["accepted"]
                panel = await get_desired_xray(node_uuid, db=db, _=object())
                assert panel["runtime_status"] == "running"
                assert panel["applied_generation"] == 1
                assert "config" not in panel

                update = await set_desired_xray(
                    node_uuid, XrayDesiredInput(action="update", image_version="26.10.21",
                                                region="test-2"), db=db, _=object())
                assert update["generation"] == 2
                with pytest.raises(HTTPException) as stale:
                    await agent_xray_report(
                        XrayReport(node_uuid=node_uuid, generation=1, status="running"),
                        x_node_secret=agent["node_token"], db=db)
                assert stale.value.status_code == 409
                with pytest.raises(ValidationError):
                    XrayDesiredInput(action="deploy", image_version="latest", config=config)
                with pytest.raises(ValidationError):
                    XrayDesiredInput(action="deploy", image_version="26.10.21;rm", config=config)
                await revoke(node_uuid, db=db, _=object())
                with pytest.raises(HTTPException) as denied:
                    await agent_xray_desired(node_uuid, x_node_secret=agent["node_token"], db=db)
                assert denied.value.status_code == 403
                with pytest.raises(HTTPException) as no_update:
                    await set_desired_xray(node_uuid, XrayDesiredInput(action="restart"),
                                           db=db, _=object())
                assert no_update.value.status_code == 409
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_node_registry_has_no_compatibility_routes():
    from app.routers.node_registry import node_registry_router, node_control_router
    paths = {route.path for route in [*node_registry_router.routes, *node_control_router.routes]}
    assert {"/api/node-registry/classic", "/api/node-registry/nodes",
            "/api/node-registry/discovery-tokens",
            "/api/node-registry/discovery-tokens/{invitation_id}/revoke",
            "/api/node-registry/nodes/{node_uuid}/xray"} <= paths
    assert not any(p.startswith("/api/agent") or p.startswith("/api/node-discovery") for p in paths)


def test_new_discovery_invitation_requires_explicit_approval_by_default():
    assert InvitationInput(label="ordinary").auto_approve is False
    assert InvitationInput(label="trusted", auto_approve=True).auto_approve is True
