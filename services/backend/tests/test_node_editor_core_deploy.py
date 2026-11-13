"""Core Editor profiles deploy through the autonomous Node's WSS desired state."""
import asyncio
import json
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.core_presets import core_presets, core_preset_bindings
from app.db.models import CoreConfig, CoreType, node_agent_registrations, node_agent_xray_desired
from app.operation.tls_presets import encrypt_private_key
from app.routers.node_registry import (
    EditorCoreDeployInput, XrayDesiredInput, XrayReport, agent_xray_desired,
    agent_xray_report, create_classic_node, deploy_editor_core,
    get_desired_xray, set_desired_xray,
)


def local_socks():
    return {"inbounds": [{"tag": "local", "listen": "127.0.0.1", "port": 18443,
                           "protocol": "socks", "settings": {"auth": "password",
                           "accounts": [{"user": "qa", "pass": "not-a-real-secret"}]}}],
            "outbounds": [{"tag": "direct", "protocol": "freedom"}]}


def test_save_core_deploy_rejects_stale_and_preserves_source(tmp_path):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/editor.db")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as db:
                agent = await create_classic_node(db=db, _=object())
                node_uuid = UUID(agent["node_uuid"])
                core = CoreConfig(name="Saved local Xray", config=local_socks(), type=CoreType.xray)
                db.add(core)
                await db.commit()
                request = EditorCoreDeployInput(core_id=core.id, expected_generation=0)
                with pytest.raises(HTTPException) as unavailable:
                    await deploy_editor_core(node_uuid, request, db=db, _=object())
                assert unavailable.value.status_code == 409
                await db.rollback()
                await db.execute(update(node_agent_registrations).where(
                    node_agent_registrations.c.node_uuid == str(node_uuid)).values(
                    capabilities=["xray.protobuf.binary"]))
                await db.commit()
                queued = await deploy_editor_core(node_uuid, request, db=db, _=object())
                assert queued["generation"] == 1
                assert queued["source_core_name"] == core.name
                row = (await db.execute(select(node_agent_xray_desired))).mappings().one()
                assert row["source_core_id"] == core.id
                assert row["config"] == local_socks()
                public = await get_desired_xray(node_uuid, db=db, _=object())
                assert public["source_core_id"] == core.id
                assert "config" not in public
                fetched = await agent_xray_desired(node_uuid, x_node_secret=agent["node_token"], db=db)
                assert fetched["config"] == local_socks()
                with pytest.raises(HTTPException) as pending:
                    await deploy_editor_core(node_uuid, request.model_copy(update={"expected_generation": 1}), db=db, _=object())
                assert pending.value.status_code == 409
                await db.rollback()
                await agent_xray_report(XrayReport(node_uuid=node_uuid, generation=1, status="running"),
                                        x_node_secret=agent["node_token"], db=db)
                with pytest.raises(HTTPException) as stale:
                    await deploy_editor_core(node_uuid, request, db=db, _=object())
                assert stale.value.status_code == 409
                await db.rollback()
                second = await deploy_editor_core(node_uuid, request.model_copy(update={"expected_generation": 1}),
                                                  db=db, _=object())
                assert second["generation"] == 2
                assert second["source_core_id"] == core.id
                await agent_xray_report(XrayReport(node_uuid=node_uuid, generation=2, status="running"),
                                        x_node_secret=agent["node_token"], db=db)
                await set_desired_xray(node_uuid, XrayDesiredInput(action="deploy", config=local_socks()),
                                       db=db, _=object())
                changed = (await db.execute(select(node_agent_xray_desired))).mappings().one()
                assert changed["source_core_id"] is None
                with pytest.raises(HTTPException) as missing:
                    await deploy_editor_core(node_uuid, EditorCoreDeployInput(core_id=99999, expected_generation=3),
                                             db=db, _=object())
                assert missing.value.status_code == 409  # pending previous generation
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_bound_preset_secret_only_goes_to_agent_payload(tmp_path, monkeypatch):
    from google.protobuf.json_format import MessageToDict
    from app import node_binary_control as control
    monkeypatch.setenv("WAYLANE_TLS_SECRET_KEY", Fernet.generate_key().decode())

    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/secret.db")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(control, "SessionLocal", factory)
        try:
            async with factory() as db:
                agent = await create_classic_node(db=db, _=object())
                node_uuid = UUID(agent["node_uuid"])
                await db.execute(update(node_agent_registrations).where(
                    node_agent_registrations.c.node_uuid == str(node_uuid)).values(
                    capabilities=["xray.protobuf.binary"]))
                secret = "synthetic-server-decryption-only"
                preset_id, binding_id = str(uuid4()), str(uuid4())
                public_core = {"inbounds": [{"tag": "private", "listen": "127.0.0.1", "port": 18444,
                           "protocol": "vless", "settings": {"clients": [], "decryption": "none",
                           "waylanePresetId": preset_id}}],
                           "outbounds": [{"tag": "direct", "protocol": "freedom"}]}
                core = CoreConfig(name="Encrypted sample", config=public_core, type=CoreType.xray)
                db.add(core)
                await db.flush()
                await db.execute(core_presets.insert().values(
                    id=preset_id, name="secret-example", kind="vless", public_data={"encryption": "public"},
                    encrypted_secret=encrypt_private_key(json.dumps({"decryption": secret})), revision=1))
                await db.execute(core_preset_bindings.insert().values(
                    id=binding_id, preset_id=preset_id, core_id=core.id, kind="vless", inbound_tag="private"))
                await db.commit()
                result = await deploy_editor_core(
                    node_uuid, EditorCoreDeployInput(core_id=core.id, expected_generation=0), db=db, _=object())
                assert result["generation"] == 1
                saved = (await db.execute(select(node_agent_xray_desired))).mappings().one()
                assert secret not in json.dumps(saved["config"])
                assert saved["config"] == public_core
                public = await get_desired_xray(node_uuid, db=db, _=object())
                assert secret not in json.dumps(public)
                agent_data = await agent_xray_desired(node_uuid, x_node_secret=agent["node_token"], db=db)
                assert agent_data["config"]["inbounds"][0]["settings"]["decryption"] == secret
                assert "waylanePresetId" not in json.dumps(agent_data["config"])
                # Drive the same authenticated binary session used in production.
                session = control.AgentBinarySession(str(node_uuid), agent["node_token"])
                await session.receive(control.new_message("hello", str(node_uuid)))
                greeting = await session.outgoing.get()
                assert greeting.kind == "hello_ack"
                await session.push_desired()
                frame = await session.outgoing.get()
                assert frame.kind == "desired" and frame.core_id == "xray/default"
                payload = MessageToDict(frame.payload, preserving_proto_field_name=True)
                assert payload["config"]["inbounds"][0]["settings"]["decryption"] == secret
                assert "waylanePresetId" not in json.dumps(payload)
                # An operator cannot rotate/rebind these public keys under an
                # already-applied Node generation; it would break reconnect.
                from app.routers.core_presets import BindingInput, bind_preset
                with pytest.raises(HTTPException) as bound:
                    await bind_preset(BindingInput(
                        core_id=core.id, preset_id=preset_id, inbound_tag="private"),
                        db=db, _=object())
                assert bound.value.status_code == 409
                # Corrupt synthetic ciphertext to simulate a lost/invalid
                # managed secret: the Node must not receive an invalid frame.
                await db.execute(core_presets.update().where(
                    core_presets.c.id == preset_id).values(encrypted_secret="invalid-test-ciphertext"))
                await db.execute(node_agent_xray_desired.update().where(
                    node_agent_xray_desired.c.node_uuid == str(node_uuid)).values(
                    generation=2, action="reconfigure", runtime_status="pending"))
                await db.commit()
                await session.push_desired()
                assert session.outgoing.empty()
                state = (await db.execute(select(node_agent_xray_desired))).mappings().one()
                assert state["runtime_status"] == "error"
                assert "invalid-test-ciphertext" not in state["last_error"]
                await session.close()
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_proxy_exposure_fails_closed_without_credentials():
    from app.routers.node_xray_resources import validate_local_proxy_exposure
    safe = local_socks()
    validate_local_proxy_exposure(safe)
    public = json.loads(json.dumps(safe))
    public["inbounds"][0]["listen"] = "0.0.0.0"
    with pytest.raises(ValueError, match="public unauthenticated"):
        validate_local_proxy_exposure({**public, "inbounds": [
            {**public["inbounds"][0], "settings": {"auth": "noauth"}}
        ]})
    validate_local_proxy_exposure(public)
    public["inbounds"][0].pop("listen")
    public["inbounds"][0]["settings"].pop("accounts")
    with pytest.raises(ValueError, match="public unauthenticated"):
        validate_local_proxy_exposure(public)


def test_assigned_profile_cannot_be_deleted_without_reassigning(tmp_path):
    from app.operation import OperatorType
    from app.operation.core import CoreOperation

    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/core-guard.db")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as db:
                agent = await create_classic_node(db=db, _=object())
                uid = UUID(agent["node_uuid"])
                await db.execute(update(node_agent_registrations).where(
                    node_agent_registrations.c.node_uuid == str(uid)
                ).values(capabilities=["xray.protobuf.binary"]))
                core = CoreConfig(name="test bind", config=local_socks(), type=CoreType.xray)
                db.add(core)
                await db.commit()
                core_id = core.id
                operator = CoreOperation(operator_type=OperatorType.API)
                await operator._ensure_editor_core_not_bound(db, [core_id])
                await deploy_editor_core(uid, EditorCoreDeployInput(core_id=core_id, expected_generation=0),
                                         db=db, _=object())
                with pytest.raises(HTTPException) as blocked:
                    await operator._ensure_editor_core_not_bound(db, [core_id])
                assert blocked.value.status_code == 409
                await db.rollback()
                # Replacing the runtime config clears the persisted profile
                # reference without deleting the editor's stored definition.
                await set_desired_xray(uid, XrayDesiredInput(action="deploy", config=local_socks()),
                                       db=db, _=object())
                await operator._ensure_editor_core_not_bound(db, [core_id])
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_saved_core_editor_save_and_restart_use_only_wss_generations(tmp_path):
    """A Core Editor save updates approved running Nodes, not stopped Nodes."""
    from app.routers.core import restart_core
    from app.routers.node_xray_resources import queue_assigned_editor_cores

    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/editor-updates.db")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as db:
                core = CoreConfig(name="shared test Core", config=local_socks(), type=CoreType.xray)
                db.add(core)
                await db.commit()
                core_id = core.id
                nodes = [await create_classic_node(db=db, _=object()) for _ in range(2)]
                for node in nodes:
                    await db.execute(update(node_agent_registrations).where(
                        node_agent_registrations.c.node_uuid == node["node_uuid"]
                    ).values(capabilities=["xray.protobuf.binary"]))
                await db.commit()
                for node in nodes:
                    uid = UUID(node["node_uuid"])
                    await deploy_editor_core(uid, EditorCoreDeployInput(
                        core_id=core_id, expected_generation=0), db=db, _=object())
                for node, state in zip(nodes, ["running", "stopped"]):
                    await agent_xray_report(XrayReport(
                        node_uuid=UUID(node["node_uuid"]), generation=1, status=state,
                    ), x_node_secret=node["node_token"], db=db)
                modified = local_socks()
                modified["outbounds"].append({"tag": "blocked", "protocol": "blackhole"})
                core = await db.get(CoreConfig, core_id)
                core.config = modified
                await db.commit()
                assert await queue_assigned_editor_cores(db, core_id) == 1
                running = (await db.execute(select(node_agent_xray_desired).where(
                    node_agent_xray_desired.c.node_uuid == nodes[0]["node_uuid"]
                ))).mappings().one()
                stopped = (await db.execute(select(node_agent_xray_desired).where(
                    node_agent_xray_desired.c.node_uuid == nodes[1]["node_uuid"]
                ))).mappings().one()
                assert running["generation"] == 2 and running["action"] == "deploy"
                assert running["config"] == modified
                assert running["runtime_status"] == "pending"
                assert stopped["generation"] == 1 and stopped["runtime_status"] == "stopped"
                await agent_xray_report(XrayReport(node_uuid=UUID(nodes[0]["node_uuid"]),
                    generation=2, status="running"), x_node_secret=nodes[0]["node_token"], db=db)
                assert await queue_assigned_editor_cores(db, core_id, live_update=True) == 1
                reconfigured = (await db.execute(select(node_agent_xray_desired).where(
                    node_agent_xray_desired.c.node_uuid == nodes[0]["node_uuid"]
                ))).mappings().one()
                assert reconfigured["generation"] == 3 and reconfigured["action"] == "reconfigure"
                await agent_xray_report(XrayReport(node_uuid=UUID(nodes[0]["node_uuid"]),
                    generation=3, status="running"), x_node_secret=nodes[0]["node_token"], db=db)
                await restart_core(core_id, admin=object(), db=db)
                restarted = (await db.execute(select(node_agent_xray_desired).where(
                    node_agent_xray_desired.c.node_uuid == nodes[0]["node_uuid"]
                ))).mappings().one()
                assert restarted["generation"] == 4
                assert restarted["action"] == "restart"
                assert restarted["config"] == modified
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_registry_display_name_is_editable_without_touching_node_identity(tmp_path):
    from pydantic import ValidationError
    from app.routers.node_registry import NodeNameUpdate, rename_registration, list_registrations

    with pytest.raises(ValidationError):
        NodeNameUpdate(display_name='  ')
    with pytest.raises(ValidationError):
        NodeNameUpdate(display_name='invalid\nname')

    async def run():
        engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/rename-node.db')
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as db:
                agent = await create_classic_node(db=db, _=object())
                node_uuid = UUID(agent['node_uuid'])
                renamed = await rename_registration(
                    node_uuid, NodeNameUpdate(display_name='  EU Test Node  '), db=db, _=object())
                assert renamed['display_name'] == 'EU Test Node'
                listings = await list_registrations(db=db, _=object())
                assert listings['nodes'][0]['display_name'] == 'EU Test Node'
                assert listings['nodes'][0]['node_uuid'] == str(node_uuid)
                assert listings['nodes'][0]['connection_mode'] == 'classic'
                with pytest.raises(HTTPException) as unknown:
                    await rename_registration(UUID('0192b532-184f-41bc-9952-78e15b14cc55'),
                                              NodeNameUpdate(display_name='No node'), db=db, _=object())
                assert unknown.value.status_code == 404
        finally:
            await engine.dispose()
    asyncio.run(run())
