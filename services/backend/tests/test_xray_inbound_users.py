"""Inbound user synchronization extends, not replaces, PasarGuard API semantics."""
import asyncio
import copy
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.routers.node_registry import (
    XrayDesiredInput, XrayInboundUsersInput, XrayReport,
    agent_xray_desired, agent_xray_report, create_classic_node,
    get_desired_xray, list_xray_inbounds, revoke, set_desired_xray, sync_xray_inbound_users,
)
from app.routers.xray_inbound_users import replace_inbound_users


def baseline():
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {"tag": "qa-socks", "protocol": "socks", "port": 18443,
             "settings": {"auth": "password", "accounts": [
                 {"user": "old", "pass": "synthetic-original-secret"}]}},
            {"tag": "qa-vless", "protocol": "vless", "port": 18444,
             "settings": {"decryption": "none", "clients": []}},
        ],
        "outbounds": [{"protocol": "freedom", "tag": "direct"}],
    }


def test_user_replace_preserves_untouched_inbounds_and_secrets():
    original = baseline()
    users = [{"user": "new-qa", "pass": "synthetic-rotated-secret"}]
    updated = replace_inbound_users(original, "qa-socks", users)
    assert updated["inbounds"][0]["settings"]["accounts"] == users
    assert original["inbounds"][0]["settings"]["accounts"][0]["user"] == "old"
    assert updated["inbounds"][1] == original["inbounds"][1]
    assert updated["outbounds"] == original["outbounds"]
    assert replace_inbound_users(original, "qa-socks", [])["inbounds"][0]["settings"]["accounts"] == []
    for tag, members in [("missing", users), ("qa-socks", [users[0], users[0]]),
                         ("qa-socks", [{"user": "bad"}])]:
        with pytest.raises(ValueError):
            replace_inbound_users(original, tag, members)
    with pytest.raises(ValueError):
        replace_inbound_users(
            {"inbounds": [{"tag": "dup", "protocol": "socks", "settings": {}},
                          {"tag": "dup", "protocol": "socks", "settings": {}}]},
            "dup", users)
    uid = str(uuid4())
    vless = replace_inbound_users(original, "qa-vless", [{"id": uid, "email": "qa@example.test"}])
    assert vless["inbounds"][1]["settings"]["clients"][0]["id"] == uid
    with pytest.raises(ValueError):
        replace_inbound_users(original, "qa-vless", [{"id": "invalid", "email": "qa@example.test"}])


def test_user_change_is_generation_guarded_and_authenticated(tmp_path):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "qa.db"))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as db:
                node = await create_classic_node(db=db, _=object())
                uid = UUID(node["node_uuid"])
                created = await set_desired_xray(uid, XrayDesiredInput(
                    action="deploy", config=baseline()), db=db, _=object())
                assert created["generation"] == 1
                await agent_xray_report(XrayReport(node_uuid=uid, generation=1, status="running"),
                                        x_node_secret=node["node_token"], db=db)
                first = await sync_xray_inbound_users(uid, "qa-socks", XrayInboundUsersInput(
                    expected_generation=1, users=[{"user": "next", "pass": "strong-qa-secret"}]),
                    db=db, _=object())
                assert first == {"generation": 2, "status": "pending",
                                 "inbound_tag": "qa-socks", "user_count": 1}
                with pytest.raises(HTTPException) as stale:
                    await sync_xray_inbound_users(uid, "qa-socks", XrayInboundUsersInput(
                        expected_generation=1, users=[]), db=db, _=object())
                assert stale.value.status_code == 409
                with pytest.raises(HTTPException) as pending:
                    await sync_xray_inbound_users(uid, "qa-socks", XrayInboundUsersInput(
                        expected_generation=2, users=[]), db=db, _=object())
                assert pending.value.status_code == 409
                inbound_list = await list_xray_inbounds(uid, db=db, _=object())
                assert inbound_list["inbounds"][0]["user_count"] == 1
                assert inbound_list["inbounds"][0]["identities"] == ["next"]
                assert "strong-qa-secret" not in str(inbound_list)
                desired = await agent_xray_desired(uid, x_node_secret=node["node_token"], db=db)
                assert desired["generation"] == 2
                assert desired["config"]["inbounds"][0]["settings"]["accounts"][0]["user"] == "next"
                assert desired["config"]["inbounds"][1] == baseline()["inbounds"][1]
                admin_view = await get_desired_xray(uid, db=db, _=object())
                assert "config" not in admin_view
                await agent_xray_report(XrayReport(node_uuid=uid, generation=2, status="running"),
                                        x_node_secret=node["node_token"], db=db)
                assert (await get_desired_xray(uid, db=db, _=object()))["applied_generation"] == 2
                with pytest.raises(HTTPException) as invalid_tag:
                    await sync_xray_inbound_users(uid, "missing", XrayInboundUsersInput(
                        expected_generation=2, users=[]), db=db, _=object())
                assert invalid_tag.value.status_code == 422
                await revoke(uid, db=db, _=object())
                with pytest.raises(HTTPException) as revoked:
                    await sync_xray_inbound_users(uid, "qa-socks", XrayInboundUsersInput(
                        expected_generation=2, users=[]), db=db, _=object())
                assert revoked.value.status_code == 409
        finally:
            await engine.dispose()
    asyncio.run(run())
