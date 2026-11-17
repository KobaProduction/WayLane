"""REALITY, VLESS Encryption and routing presets preserve secret boundaries."""

import base64
import json
from datetime import UTC

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.xray import XRayConfig
from app.db import Base
from app.db.models import CoreConfig, CoreType
from app.operation.core_presets import (
    ROUTING_TAG,
    CorePresetError,
    build_preset,
    hydrate_core_preset_config,
    project_bound_core_config,
    project_public_config,
)
from app.operation.tls_presets import decrypt_private_key
from app.routers import core_presets as api
from app.routers.core_presets import BindingInput, PresetInput


def sample_private():
    key = x25519.X25519PrivateKey.generate()
    raw = key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def core_example():
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "reality-in", "listen": "127.0.0.1", "port": 18211,
                "protocol": "vless", "settings": {"clients": []},
                "streamSettings": {
                    "network": "tcp", "security": "reality",
                    "realitySettings": {
                        "privateKey": sample_private(),
                        "serverNames": ["example.com"], "shortIds": ["aabb"],
                    },
                },
            },
            {
                "tag": "encrypted-in", "listen": "127.0.0.1", "port": 18212,
                "protocol": "vless",
                "settings": {"clients": [], "decryption": "none", "encryption": "none"},
                "streamSettings": {"network": "tcp", "security": "none"},
            },
        ],
        "outbounds": [{"tag": "DIRECT", "protocol": "freedom"},
                      {"tag": "BLOCK", "protocol": "blackhole"}],
        "routing": {"rules": [{"type": "field", "outboundTag": "DIRECT", "network": "tcp,udp"}]},
    }


def fake_vless_pair():
    return {
        "decryption": "mlkem768x25519plus.native.600s.100-111-1111.75-0-111.50-0-3333.abc123",
        "encryption": "mlkem768x25519plus.native.0rtt.100-111-1111.75-0-111.50-0-3333.xyz456",
    }


def test_reality_private_key_is_encrypted_and_public_key_is_derived(monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("WAYLANE_TLS_SECRET_KEY", Fernet.generate_key().decode())
    private = sample_private()
    public, encrypted = build_preset("reality", {"private_key": private, "short_ids": ["aabb"]})
    assert private not in encrypted
    assert private not in json.dumps(public)
    assert json.loads(decrypt_private_key(encrypted))["privateKey"] == private
    assert public["publicKey"] and len(public["publicKey"]) > 35
    with pytest.raises(CorePresetError):
        build_preset("reality", {"private_key": private, "public_key": "wrong", "short_ids": ["aabb"]})


def test_reality_projection_parses_without_private_key():
    config = core_example()
    public, _ = build_preset_for_test("reality", {
        "private_key": sample_private(), "short_ids": ["ab12"],
    })
    result = project_public_config(config, "reality", "reality-in", "test-preset", public)
    reality = result["inbounds"][0]["streamSettings"]["realitySettings"]
    assert "privateKey" not in reality
    assert reality["waylanePresetId"] == "test-preset"
    assert "privateKey" in config["inbounds"][0]["streamSettings"]["realitySettings"]
    parsed = XRayConfig(result)
    assert parsed.inbounds_by_tag["reality-in"]["pbk"] == public["publicKey"]


def build_preset_for_test(kind, body):
    import os

    from cryptography.fernet import Fernet
    os.environ.setdefault("WAYLANE_TLS_SECRET_KEY", Fernet.generate_key().decode())
    return build_preset(kind, body)


@pytest.mark.asyncio
async def test_presets_binding_projection_rotation_and_hydration(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("WAYLANE_TLS_SECRET_KEY", Fernet.generate_key().decode())
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/core-presets.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    refreshed = []

    async def observe(core, _):
        refreshed.append(core.id)

    monkeypatch.setattr(api, "_refresh_core", observe)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with factory() as db:
            db.add(CoreConfig(
                name="Managed core", type=CoreType.xray, config=core_example(),
                exclude_inbound_tags=set(), fallbacks_inbound_tags=set(), sort_order=1,
            ))
            await db.commit()
            core = (await db.scalars(select(CoreConfig).where(CoreConfig.name == "Managed core"))).one()
            core_id = core.id
            reality = await api.create_preset(
                PresetInput(name="Reality EU", kind="reality", data={
                    "private_key": sample_private(), "short_ids": ["aabb"],
                }), db, None,
            )
            vless = await api.create_preset(
                PresetInput(name="Encrypted VLESS", kind="vless", data=fake_vless_pair()), db, None
            )
            routing = await api.create_preset(
                PresetInput(name="Route EU", kind="routing", data={
                    "routing": {"domainStrategy": "AsIs", "rules": [
                        {"type": "field", "domain": ["domain:internal.test"], "outboundTag": "DIRECT"},
                        {"type": "field", "network": "tcp,udp", "outboundTag": "BLOCK"},
                    ]}
                }), db, None,
            )
            for preset, tag in (
                (reality, "reality-in"), (vless, "encrypted-in"), (routing, ROUTING_TAG),
            ):
                result = await api.bind_preset(
                    BindingInput(core_id=core_id, preset_id=preset["id"], inbound_tag=tag),
                    db, None,
                )
                assert result["requires_node_apply"]
            stored = (await db.get(CoreConfig, core_id)).config
            serialized = json.dumps(stored)
            assert "privateKey" not in serialized
            assert "mldsa65Seed" not in serialized
            assert "abc123" not in serialized
            assert "waylanePresetId" in serialized
            assert len(refreshed) == 3
            assert len(await api.list_core_bindings(db, None)) == 3

            # Manager/client-facing metadata must accept the sanitized core.
            parsed = XRayConfig(stored)
            assert parsed.inbounds_by_tag["reality-in"]["pbk"] == reality["data"]["publicKey"]
            assert parsed.inbounds_by_tag["encrypted-in"]["encryption"] == vless["data"]["encryption"]

            hydrated = json.loads(await hydrate_core_preset_config(db, json.dumps(stored), core_id))
            assert hydrated["inbounds"][0]["streamSettings"]["realitySettings"]["privateKey"]
            assert "waylanePresetId" not in json.dumps(hydrated)
            assert hydrated["inbounds"][1]["settings"]["decryption"].endswith("abc123")
            assert hydrated["routing"]["rules"][1]["outboundTag"] == "BLOCK"

            with pytest.raises(HTTPException) as error:
                await api.delete_preset(reality["id"], db, None)
            assert error.value.status_code == 409
            rotated = await api.replace_preset(
                reality["id"], PresetInput(name="Reality EU", kind="reality", data={
                    "private_key": sample_private(), "short_ids": ["abcd"],
                }), db, None,
            )
            assert rotated["affected_core_ids"] == [core_id]
            assert rotated["requires_node_apply"]
            assert (await db.get(CoreConfig, core_id)).config["inbounds"][0]["streamSettings"]["realitySettings"]["shortIds"] == ["abcd"]

            edited = json.loads(json.dumps(stored))
            edited["inbounds"][0]["streamSettings"]["realitySettings"]["privateKey"] = "leaked"
            clean = await project_bound_core_config(db, core_id, edited)
            assert "leaked" not in json.dumps(clean)
            assert "privateKey" not in json.dumps(clean)
    finally:
        await engine.dispose()

@pytest.mark.asyncio
async def test_switch_reality_preset_back_to_manual_requires_valid_new_key(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("WAYLANE_TLS_SECRET_KEY", Fernet.generate_key().decode())
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/manual-mode.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    refreshed = []

    async def observe(core, _):
        refreshed.append(core.id)

    monkeypatch.setattr(api, "_refresh_core", observe)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with factory() as db:
            core = CoreConfig(name="Manual switch", type=CoreType.xray,
                              config=core_example(), exclude_inbound_tags=set(), fallbacks_inbound_tags=set())
            db.add(core)
            await db.commit()
            core_id = core.id
            old_key = core.config["inbounds"][0]["streamSettings"]["realitySettings"]["privateKey"]
            preset = await api.create_preset(PresetInput(name="Encrypted source", kind="reality", data={
                "private_key": sample_private(), "short_ids": ["aabb"],
            }), db, None)
            await api.bind_preset(BindingInput(core_id=core_id, preset_id=preset["id"], inbound_tag="reality-in"), db, None)
            assert old_key not in json.dumps(core.config)
            # The saved Core must not have its managed key changed under an active Node.
            from datetime import datetime

            from app.db.models import node_agent_xray_desired
            from app.routers.node_registry import create_classic_node
            node = await create_classic_node(db=db, _=object())
            await db.execute(node_agent_xray_desired.insert().values(
                node_uuid=node["node_uuid"], generation=1, action="deploy", image_version="26.10.21",
                config=core.config, source_core_id=core_id, updated_at=datetime.now(UTC),
            ))
            await db.commit()
            # Metadata-only rename is safe even while this Core is assigned to a Node.
            # It must not enqueue a generation or re-materialize the private key.
            from app.db.core_presets import core_presets
            from app.routers.core_presets import PresetRenameInput, RealityManualInput
            secret_before = (await db.execute(
                select(core_presets.c.encrypted_secret).where(core_presets.c.id == preset["id"])
            )).scalar_one()
            result = await api.rename_preset(
                preset["id"], PresetRenameInput(name="Renamed while assigned", expected_revision=1), db, None
            )
            assert result["requires_node_apply"] is False
            assert (await db.execute(select(core_presets.c.encrypted_secret).where(
                core_presets.c.id == preset["id"]
            ))).scalar_one() == secret_before
            assert (await db.execute(select(node_agent_xray_desired.c.generation).where(
                node_agent_xray_desired.c.node_uuid == node["node_uuid"]
            ))).scalar_one() == 1
            with pytest.raises(HTTPException) as protected:
                await api.unbind_reality_to_manual(
                    RealityManualInput(core_id=core_id, inbound_tag="reality-in", private_key=sample_private()), db, None)
            assert protected.value.status_code == 409
            await db.rollback()
            await db.execute(node_agent_xray_desired.delete().where(node_agent_xray_desired.c.node_uuid == node["node_uuid"]))
            await db.commit()
            with pytest.raises(HTTPException) as invalid:
                await api.unbind_reality_to_manual(
                    RealityManualInput(core_id=core_id, inbound_tag="reality-in", private_key="!" * 43), db, None)
            assert invalid.value.status_code == 422
            assert (await api.list_core_bindings(db, None))[0]["preset_id"] == preset["id"]
            await db.rollback()
            await db.refresh(core)
            replacement = sample_private()
            result = await api.unbind_reality_to_manual(
                RealityManualInput(core_id=core_id, inbound_tag="reality-in", private_key=replacement), db, None)
            assert result["mode"] == "manual"
            assert await api.list_core_bindings(db, None) == []
            settings = core.config["inbounds"][0]["streamSettings"]["realitySettings"]
            assert settings["privateKey"] == replacement
            assert "publicKey" not in settings
            assert "waylanePresetId" not in settings
            assert settings["shortIds"] == ["aabb"]
            assert XRayConfig(core.config).inbounds_by_tag["reality-in"]["pbk"]
            assert refreshed == [core_id, core_id]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rename_only_preserves_encrypted_secret_and_rejects_stale_revision(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    from app.db.core_presets import core_presets
    from app.routers.core_presets import PresetRenameInput

    monkeypatch.setenv("WAYLANE_TLS_SECRET_KEY", Fernet.generate_key().decode())
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/rename.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with factory() as db:
            one = await api.create_preset(PresetInput(name="Initial", kind="reality", data={
                "private_key": sample_private(), "short_ids": ["aabb"],
            }), db, None)
            two = await api.create_preset(PresetInput(name="Other", kind="reality", data={
                "private_key": sample_private(), "short_ids": ["abcd"],
            }), db, None)
            secret_before = (
                await db.execute(select(core_presets.c.encrypted_secret).where(core_presets.c.id == one["id"]))
            ).scalar_one()
            public_before = one["data"]
            result = await api.rename_preset(
                one["id"], PresetRenameInput(name="  New name  ", expected_revision=1), db, None
            )
            assert result == {"id": one["id"], "name": "New name", "revision": 2,
                              "requires_node_apply": False}
            stored = (
                await db.execute(select(core_presets).where(core_presets.c.id == one["id"]))
            ).mappings().one()
            assert stored["encrypted_secret"] == secret_before
            assert stored["public_data"] == public_before
            assert stored["revision"] == 2

            with pytest.raises(HTTPException) as stale:
                await api.rename_preset(one["id"], PresetRenameInput(name="Stale", expected_revision=1), db, None)
            assert stale.value.status_code == 409
            await db.rollback()
            with pytest.raises(HTTPException) as name_conflict:
                await api.rename_preset(one["id"], PresetRenameInput(name="Other", expected_revision=2), db, None)
            assert name_conflict.value.status_code == 409
            await db.rollback()
            with pytest.raises(HTTPException) as rotate_conflict:
                await api.replace_preset(one["id"], PresetInput(
                    name="New name", kind="reality", expected_revision=1,
                    data={"private_key": sample_private(), "short_ids": ["ffab"]},
                ), db, None)
            assert rotate_conflict.value.status_code == 409
            await db.rollback()
            assert (await api.list_presets(db, None))[0]["kind"] == "reality"
            assert two["name"] == "Other"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_apply_saved_core_queues_new_snapshot_not_old_restart(monkeypatch):
    from app.routers import core as core_api
    accepted = []

    async def queue(db, core_id, *, restart_only=False, live_update=False):
        accepted.append((core_id, restart_only, live_update))
        return 2 if core_id == 42 else 0

    monkeypatch.setattr(core_api, "queue_assigned_editor_cores", queue)
    assert await core_api.apply_saved_core_to_assigned_nodes(42, None, None) == {
        "queued_nodes": 2, "status": "queued",
    }
    assert await core_api.apply_saved_core_to_assigned_nodes(43, None, None) == {
        "queued_nodes": 0, "status": "no_assigned_nodes",
    }
    assert accepted == [(42, False, False), (43, False, False)]
