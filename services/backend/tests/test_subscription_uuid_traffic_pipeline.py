"""UUIDv4-only Xray control traffic, without historic numeric Node RPC."""
import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Subscription, node_agent_xray_managed_inbounds, node_agent_xray_usage_cursors
from app.node_traffic_reporting import XrayTrafficSample, XrayTrafficSnapshot, record_xray_traffic
from app.routers.node_registry import create_classic_node


def test_uuid_only_counters_and_idempotent_retransmission(tmp_path):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/uuid-stats.db")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as db:
                node = await create_classic_node(db=db, _=object())
                uid = UUID(node['node_uuid'])
                subscriber = Subscription()
                db.add(subscriber)
                await db.flush()
                sid = subscriber.uuid
                await db.execute(insert(node_agent_xray_managed_inbounds).values(
                    node_uuid=str(uid), inbound_tag='synthetic-vless', created_at=datetime.now(UTC)))
                await db.commit()
                epoch = datetime.now(UTC) - timedelta(minutes=2)
                frame = XrayTrafficSnapshot(epoch=epoch, samples=[XrayTrafficSample(
                    subscription_uuid=sid, uplink=12, downlink=8)])
                await record_xray_traffic(db, str(uid), frame)
                await record_xray_traffic(db, str(uid), frame)
                row = (await db.execute(select(node_agent_xray_usage_cursors))).mappings().one()
                assert isinstance(row['subscription_uuid'], UUID)
                assert row['subscription_uuid'] == sid
                assert row['total_uplink'] == 12
                assert row['total_downlink'] == 8
                with pytest.raises(ValueError):
                    XrayTrafficSample(subscription_uuid=123, uplink=10, downlink=1)
        finally:
            await engine.dispose()
    asyncio.run(run())
