"""Independent AWG desired generation and WSS routing, without real VPN traffic."""
import asyncio
import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import node_binary_control as control
from app.db.base import Base
from app.db.models import node_agent_awg_desired, node_agent_registrations, node_agent_xray_desired
from app.routers.node_registry import AWGDesiredInput, get_desired_awg, set_desired_awg
from app.utils.crypto import generate_wireguard_keypair


def sample_config():
    private, _ = generate_wireguard_keypair()
    _, peer_public = generate_wireguard_keypair()
    return {
        "engine": "amneziawg", "interface_name": "awg0", "private_key": private,
        "listen_port": 51820, "address": ["10.90.0.1/24"],
        "amnezia": {"jc": 4, "jmin": 24, "jmax": 48, "s1": 16, "s2": 16,
                    "s3": 16, "s4": 16, "h1": "100", "h2": "200", "h3": "300", "h4": "400"},
        "peers": [{"public_key": peer_public, "allowed_ips": ["10.90.0.2/32"]}],
    }


def test_awg_backend_never_accepts_invalid_peer_or_mislabeled_profile():
    config = sample_config()
    invalids = [
        {**config, "engine": "wireguard"},  # Must reject obfuscation inherited from AWG.
        {**config, "amnezia": {"h1": "100-200", "h2": "150-300"}},
        {**config, "peers": [{"public_key": "bogus", "allowed_ips": ["10.90.0.2/32"]}]},
        {**config, "peers": [config["peers"][0], config["peers"][0]]},
        {**config, "peers": [{**config["peers"][0], "allowed_ips": ["bad"]}]},
        {**config, "pre_shared_key": generate_wireguard_keypair()[0]},
        {**config, "shell_command": "unsafe"},
        {**config, "interface_name": "a" * 40},
        {**config, "address": ["10.90.0.1/24"] * 17},
    ]
    for invalid in invalids:
        with pytest.raises((ValidationError, ValueError)):
            AWGDesiredInput(action="deploy", config=invalid)
    plain = {**config, "engine": "wireguard"}
    plain.pop("amnezia")
    accepted = AWGDesiredInput(action="deploy", config=plain)
    assert accepted.config["engine"] == "wireguard"


def test_awg_wss_desired_independence_and_capability_gate(tmp_path, monkeypatch):
    async def check():
        node = str(uuid4())
        secret = f"wl_node_{node}_synthetic_only"
        engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "desired.db"))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(control, "SessionLocal", sessions)
        try:
            async with sessions() as db:
                await db.execute(insert(node_agent_registrations).values(
                    node_uuid=node, secret_hash=hashlib.sha256(secret.encode()).hexdigest(),
                    invitation_id=None, display_name="qa", agent_version="0.1.0", status="approved",
                    capabilities=[], created_at=datetime.now(UTC)))
                await db.commit()
                with pytest.raises(HTTPException) as denied:
                    await set_desired_awg(uuid4_from(node), AWGDesiredInput(
                        action="deploy", config=sample_config()), db=db, _=object())
                assert denied.value.status_code == 409
                assert (await db.execute(select(node_agent_awg_desired))).first() is None
                await db.rollback()
                await db.execute(update(node_agent_registrations).where(
                    node_agent_registrations.c.node_uuid == node).values(
                    capabilities=["awg.protobuf.binary"]))
                await db.commit()
            desired_config = sample_config()
            async with sessions() as db:
                created = await set_desired_awg(uuid4_from(node), AWGDesiredInput(
                    action="deploy", config=desired_config), db=db, _=object())
                assert created["generation"] == 1
                visible = await get_desired_awg(uuid4_from(node), db=db, _=object())
                assert "config" not in visible and "private_key" not in str(visible)
                assert visible["status"] == "pending"
                with pytest.raises(HTTPException):
                    await set_desired_awg(uuid4_from(node), AWGDesiredInput(
                        action="reconfigure", config=desired_config, expected_generation=1),
                        db=db, _=object())
                await db.rollback()
            session = control.AgentBinarySession(node, secret)
            await session.receive(control.new_message("hello", node))
            await session.outgoing.get()  # authenticated hello acknowledgement
            await session.push_desired()
            assert session.outgoing.empty(), "stale registration capability sent AWG before fresh telemetry"
            await session.receive(control.new_message("telemetry", node, payload={
                "node_version": "0.1.0", "capabilities": ["awg.protobuf.binary"],
            }))
            await session.push_desired()
            pushed = await session.outgoing.get()
            assert pushed.kind == "desired" and pushed.core_id == "awg/default"
            assert pushed.generation == 1 and pushed.operation == "deploy"
            assert pushed.payload.fields["config"].struct_value.fields["amnezia"]
            await session.receive(control.new_message(
                "result", node, core_id="awg/default", generation=1, status="running"))
            ack = await session.outgoing.get()
            assert ack.kind == "ack" and ack.status == "accepted" and ack.core_id == "awg/default"
            async with sessions() as db:
                xray = (await db.execute(select(node_agent_xray_desired))).first()
                assert xray is None
                row = (await db.execute(select(node_agent_awg_desired))).mappings().one()
                assert row["applied_generation"] == 1 and row["runtime_status"] == "running"
                config2 = sample_config()
                queued = await set_desired_awg(uuid4_from(node), AWGDesiredInput(
                    action="reconfigure", expected_generation=1, config=config2), db=db, _=object())
                assert queued["generation"] == 2
            await session.push_desired()
            update_frame = await session.outgoing.get()
            assert update_frame.core_id == "awg/default" and update_frame.generation == 2
            await session.receive(control.new_message(
                "result", node, core_id="awg/default", generation=2,
                status="error", error_code="awg_apply_failed"))
            await session.outgoing.get()
            async with sessions() as db:
                row = (await db.execute(select(node_agent_awg_desired))).mappings().one()
                assert row["generation"] == 2 and row["applied_generation"] == 1
                assert row["last_error"] == "awg_apply_failed"
            await session.close()
        finally:
            await engine.dispose()

    asyncio.run(check())


def uuid4_from(raw: str):
    from uuid import UUID
    return UUID(raw)


def test_awg_migration_adds_and_removes_only_its_own_table(tmp_path):
    """Exercise the additive Alembic operation on disposable SQLite only."""
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect

    source = (Path(__file__).parents[1] / "app/db/migrations/versions" /
              "a7e36d1b4f90_agent_awg_desired.py")
    spec = importlib.util.spec_from_file_location("waylane_awg_migration_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.down_revision == "f9d82a8c6e40"

    engine = create_engine("sqlite:///" + str(tmp_path / "migration.db"))
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE node_agent_registrations (node_uuid VARCHAR(36) PRIMARY KEY)")
            module.op = Operations(MigrationContext.configure(conn))
            module.upgrade()
            columns = {column["name"] for column in inspect(conn).get_columns("node_agent_awg_desired")}
            assert {"node_uuid", "generation", "action", "config", "applied_generation",
                    "runtime_status", "last_error", "updated_at", "reported_at"} <= columns
            module.downgrade()
            assert "node_agent_awg_desired" not in inspect(conn).get_table_names()
            assert "node_agent_registrations" in inspect(conn).get_table_names()
    finally:
        engine.dispose()
