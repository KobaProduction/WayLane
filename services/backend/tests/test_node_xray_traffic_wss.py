"""Authenticated, read-only Xray traffic is monotonic and epoch-aware."""
import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import node_binary_control as control
from app.db.base import Base
from app.db.models import (
    Subscription,
    node_agent_xray_managed_inbounds,
    node_agent_xray_usage_cursors,
)
from app.node_traffic_reporting import XrayTrafficSnapshot
from app.routers.node_registry import create_classic_node, read_node_xray_usage


def packet(node_uuid: UUID, subscriber: UUID, epoch: datetime, uplink: int, downlink: int):
    return control.new_message("usage", str(node_uuid), core_id="xray/default", payload={
        "epoch": epoch.isoformat(),
        "samples": [{"subscription_uuid": str(subscriber), "uplink": uplink, "downlink": downlink}],
    })


def test_authenticated_xray_traffic_idempotency_and_restart_epochs(tmp_path, monkeypatch):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/xray-usage.db")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(control, "SessionLocal", sessions)
        try:
            async with sessions() as db:
                agent = await create_classic_node(db=db, _=object())
                node_uuid = UUID(agent["node_uuid"])
                subscription = Subscription()
                db.add(subscription)
                await db.flush()
                sid = subscription.uuid
                await db.execute(insert(node_agent_xray_managed_inbounds).values(
                    node_uuid=str(node_uuid), inbound_tag="private-vless", created_at=datetime.now(UTC)))
                await db.commit()

            session = control.AgentBinarySession(str(node_uuid), agent["node_token"])
            await session.receive(control.new_message("hello", str(node_uuid)))
            assert (await session.outgoing.get()).kind == "hello_ack"
            old_epoch = datetime.now(UTC) - timedelta(hours=3)
            first = packet(node_uuid, sid, old_epoch, 100, 150)
            with pytest.raises(ValueError, match="not negotiated"):
                await session.receive(first)
            await session.receive(control.new_message("telemetry", str(node_uuid), payload={
                "node_version": "0.1.0", "capabilities": ["xray.protobuf.binary", "xray.stats.readonly"],
            }))
            await session.receive(first)
            await session.receive(first)  # Protobuf message-id dedup in session.
            await session.receive(packet(node_uuid, sid, old_epoch, 100, 150))  # new ID, same counters
            await session.receive(packet(node_uuid, sid, old_epoch, 140, 190))
            epoch2 = datetime.now(UTC) - timedelta(hours=2)
            await session.receive(packet(node_uuid, sid, epoch2, 25, 35))
            await session.receive(packet(node_uuid, sid, old_epoch, 200, 400))  # stale epoch ignored
            async with sessions() as db:
                cursor = (await db.execute(select(node_agent_xray_usage_cursors))).mappings().one()
                assert cursor["uplink"] == 25 and cursor["downlink"] == 35
                assert cursor["total_uplink"] == 165  # 100 + 40 + 25
                assert cursor["total_downlink"] == 225  # 150 + 40 + 35
                assert (await db.get(Subscription, sid)).used_traffic == 0  # NOT billing.
                public = await read_node_xray_usage(node_uuid, db=db, _=object())
                assert public["billing_authoritative"] is False
                assert public["samples"][0]["total_uplink"] == "165"
                assert "secret" not in str(public).lower()
            await session.close()
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_decreasing_counters_and_bad_usage_payload_fail_closed(tmp_path, monkeypatch):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/xray-usage-errors.db")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(control, "SessionLocal", sessions)
        try:
            async with sessions() as db:
                agent = await create_classic_node(db=db, _=object())
                uid = UUID(agent["node_uuid"])
                subscriber = Subscription()
                db.add(subscriber)
                await db.flush()
                sid = subscriber.uuid
                await db.execute(insert(node_agent_xray_managed_inbounds).values(
                    node_uuid=str(uid), inbound_tag="private-vless", created_at=datetime.now(UTC)))
                await db.commit()
            session = control.AgentBinarySession(str(uid), agent["node_token"])
            await session.receive(control.new_message("hello", str(uid)))
            await session.outgoing.get()
            await session.receive(control.new_message("telemetry", str(uid), payload={
                "node_version": "0.1.0", "capabilities": ["xray.protobuf.binary", "xray.stats.readonly"],
            }))
            epoch = datetime.now(UTC) - timedelta(hours=1)
            await session.receive(packet(uid, sid, epoch, 99, 100))
            await session.receive(packet(uid, sid, epoch, 98, 100))  # ignored without WSS failure
            with pytest.raises(ValueError, match="invalid Xray traffic"):
                await session.receive(control.new_message("usage", str(uid), core_id="xray/default", payload={
                    "epoch": epoch.isoformat(),
                    "samples": [{"subscription_uuid": str(sid), "uplink": 9_007_199_254_740_992,
                                 "downlink": 0}],
                }))
            async with sessions() as db:
                cursor = (await db.execute(select(node_agent_xray_usage_cursors))).mappings().one()
                assert cursor["uplink"] == 99 and cursor["downlink"] == 100
            await session.close()
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_xray_traffic_validation_rejects_invalid_uuid_duplicate_and_future_epoch():
    uid = str(uuid4())
    stamp = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    item = {"subscription_uuid": uid, "uplink": 7, "downlink": 9}
    with pytest.raises(ValidationError):
        XrayTrafficSnapshot.model_validate({"epoch": stamp, "samples": [item, item]})
    with pytest.raises(ValidationError):
        XrayTrafficSnapshot.model_validate({"epoch": stamp, "samples": [
            {**item, "subscription_uuid": "not-a-uuid"}]})
    with pytest.raises(ValidationError):
        XrayTrafficSnapshot.model_validate({"epoch": stamp, "samples": [{**item, "uplink": 1.5}]})
    with pytest.raises(ValidationError):
        XrayTrafficSnapshot.model_validate({"epoch": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                                            "samples": [item]})


def test_unmanaged_node_traffic_is_ignored_without_disconnecting_wss(tmp_path, monkeypatch):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/unmanaged-usage.db")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(control, "SessionLocal", factory)
        try:
            async with factory() as db:
                agent = await create_classic_node(db=db, _=object())
                uid = UUID(agent["node_uuid"])
                sub = Subscription()
                db.add(sub)
                await db.flush()
                sid = sub.uuid
                await db.commit()
            session = control.AgentBinarySession(str(uid), agent["node_token"])
            await session.receive(control.new_message("hello", str(uid)))
            await session.outgoing.get()
            await session.receive(control.new_message("telemetry", str(uid), payload={
                "node_version": "0.1.0", "capabilities": ["xray.protobuf.binary", "xray.stats.readonly"],
            }))
            await session.receive(packet(uid, sid, datetime.now(UTC)-timedelta(minutes=1), 500, 600))
            assert not session.stopped.is_set()
            async with factory() as db:
                assert (await db.execute(select(node_agent_xray_usage_cursors))).mappings().first() is None
            await session.close()
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_usage_requires_current_session_readonly_capability(tmp_path, monkeypatch):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/usage-capability.db")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(control, "SessionLocal", factory)
        try:
            async with factory() as db:
                agent = await create_classic_node(db=db, _=object())
                uid = UUID(agent["node_uuid"])
                sub = Subscription()
                db.add(sub)
                await db.flush()
                sid = sub.uuid
                await db.execute(insert(node_agent_xray_managed_inbounds).values(
                    node_uuid=str(uid), inbound_tag="limited-qa", created_at=datetime.now(UTC)))
                await db.commit()
            session = control.AgentBinarySession(str(uid), agent["node_token"])
            await session.receive(control.new_message("hello", str(uid)))
            await session.outgoing.get()
            await session.receive(control.new_message("telemetry", str(uid), payload={
                "node_version": "0.1.0", "capabilities": ["xray.protobuf.binary"],
            }))
            epoch = datetime.now(UTC) - timedelta(minutes=1)
            with pytest.raises(ValueError, match="not negotiated"):
                await session.receive(packet(uid, sid, epoch, 1, 2))
            async with factory() as db:
                assert (await db.execute(select(node_agent_xray_usage_cursors))).mappings().first() is None
            await session.close()
        finally:
            await engine.dispose()
    asyncio.run(run())
