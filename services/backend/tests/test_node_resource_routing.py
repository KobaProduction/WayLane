"""Use the existing saved Xray Core Editor routing for autonomous Agents."""
import asyncio
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import CoreConfig, CoreType
from app.routers.node_registry import (
    EditorRoutingInput, XrayDesiredInput, XrayReport,
    agent_xray_desired, agent_xray_report, create_classic_node,
    get_desired_xray, set_desired_xray, sync_editor_routing,
)
from app.routers.node_xray_resources import replace_routing


def qa_config():
    return {"inbounds": [{"tag": "qa-in", "protocol": "socks", "port": 18443,
                           "settings": {"accounts": [{"user": "dev", "pass": "synthetic"}]}}],
            "outbounds": [{"tag": "direct", "protocol": "freedom"}],
            "routing": {"domainStrategy": "AsIs", "rules": []}}


def test_routing_replacement_preserves_all_other_editor_fields():
    raw = qa_config()
    changed = replace_routing(raw, {
        "domainStrategy": "IPIfNonMatch",
        "rules": [{"type": "field", "domain": ["domain:example.invalid"], "outboundTag": "direct"}]})
    assert changed["inbounds"] == raw["inbounds"]
    assert changed["outbounds"] == raw["outbounds"]
    assert raw["routing"]["rules"] == []
    with pytest.raises(ValueError):
        replace_routing(raw, {"rules": "not-a-list"})


def test_saved_routing_editor_reaches_private_agent_desired_state(tmp_path):
    async def run():
        engine=create_async_engine("sqlite+aiosqlite:///"+str(tmp_path/"qa.db"))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions=async_sessionmaker(engine,expire_on_commit=False)
        try:
            async with sessions() as db:
                agent=await create_classic_node(db=db, _=object())
                uuid=UUID(agent["node_uuid"])
                await set_desired_xray(uuid,XrayDesiredInput(action="deploy",config=qa_config()),db=db,_=object())
                await agent_xray_report(XrayReport(node_uuid=uuid,generation=1,status="running"),
                                        x_node_secret=agent["node_token"],db=db)
                config=qa_config()
                config["routing"]={"domainStrategy":"IPIfNonMatch","rules":[{
                    "type":"field","domain":["domain:example.invalid"],"outboundTag":"direct"}]}
                core=CoreConfig(name="qa-editor",config=config,type=CoreType.xray)
                db.add(core)
                await db.commit()
                result=await sync_editor_routing(uuid,EditorRoutingInput(core_id=core.id,expected_generation=1),db=db,_=object())
                assert result["generation"]==2
                with pytest.raises(HTTPException) as stale:
                    await sync_editor_routing(uuid,EditorRoutingInput(core_id=core.id,expected_generation=1),db=db,_=object())
                assert stale.value.status_code==409
                desired=await agent_xray_desired(uuid,x_node_secret=agent["node_token"],db=db)
                assert desired["config"]["routing"]==config["routing"]
                assert desired["config"]["inbounds"]==qa_config()["inbounds"]
                public=await get_desired_xray(uuid,db=db,_=object())
                assert "config" not in public and "tls_certificates" not in public
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_tls_preset_agent_binding_keeps_key_out_of_admin_api(tmp_path, monkeypatch):
    import uuid as uuidlib
    from datetime import UTC, datetime, timedelta
    from cryptography.fernet import Fernet
    from sqlalchemy import insert
    from app.db.tls import tls_presets
    from app.operation.tls_presets import encrypt_private_key
    from app.routers.node_registry import (
        AgentTLSBindingInput, bind_agent_tls, list_agent_tls_bindings,
        revoke,
    )
    monkeypatch.setenv("WAYLANE_TLS_SECRET_KEY", Fernet.generate_key().decode())
    async def run():
        engine=create_async_engine("sqlite+aiosqlite:///"+str(tmp_path/"qa-tls.db"))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions=async_sessionmaker(engine,expire_on_commit=False)
        try:
            async with sessions() as db:
                agent=await create_classic_node(db=db, _=object())
                uid=UUID(agent["node_uuid"])
                config=qa_config()
                config["inbounds"].append({
                    "tag":"qa-tls", "protocol":"vless", "port":18444,
                    "settings":{"decryption":"none","clients":[]},
                    "streamSettings":{"security":"tls","tlsSettings":{}}})
                await set_desired_xray(uid,XrayDesiredInput(action="deploy",config=config),db=db,_=object())
                await agent_xray_report(XrayReport(node_uuid=uid,generation=1,status="running"),
                                        x_node_secret=agent["node_token"],db=db)
                preset_id=str(uuidlib.uuid4())
                secret="synthetic-private-key-for-data-flow-test"
                # Direct insert is a persistence-layer fixture only:
                # the public TLS preset API separately validates real X.509 pairs.
                await db.execute(insert(tls_presets).values(
                    id=preset_id,name="unit-test-only",certificate_pem="synthetic-public-cert",
                    encrypted_private_key=encrypt_private_key(secret),
                    fingerprint_sha256="a"*64,subject="CN=test.invalid",
                    not_after=datetime.now(UTC)+timedelta(days=1),revision=1))
                await db.commit()
                bound=await bind_agent_tls(uid,"qa-tls",AgentTLSBindingInput(
                    preset_id=UUID(preset_id),expected_generation=1),db=db,_=object())
                assert bound["generation"]==2
                admin=await list_agent_tls_bindings(uid,db=db,_=object())
                assert admin["bindings"][0]["preset_name"]=="unit-test-only"
                assert secret not in str(admin)
                assert secret not in str(await get_desired_xray(uid,db=db,_=object()))
                material=await agent_xray_desired(uid,x_node_secret=agent["node_token"],db=db)
                assert material["tls_certificates"][0]["private_key"]==secret
                assert secret not in str(material["config"])
                with pytest.raises(HTTPException) as denied:
                    await agent_xray_desired(uid,x_node_secret="wrong",db=db)
                assert denied.value.status_code==401
                await revoke(uid,db=db,_=object())
                with pytest.raises(HTTPException) as revoked:
                    await agent_xray_desired(uid,x_node_secret=agent["node_token"],db=db)
                assert revoked.value.status_code==403
        finally:
            await engine.dispose()
    asyncio.run(run())
