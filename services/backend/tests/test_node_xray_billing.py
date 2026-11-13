"""Commercial Xray usage acceptance tests (run only after the full RPC cutover).

All data is synthetic and isolated. The production runtime flag defaults off.
"""
import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.db.models import (
    Admin,
    AdminRole,
    Subscription,
    node_agent_xray_desired,
    node_agent_xray_usage_cursors,
    node_agent_xray_usage_hours,
)
from app.node_traffic_reporting import (
    XrayTrafficSample,
    XrayTrafficSnapshot,
    record_xray_traffic,
)
from config import usage_settings
from tests.test_node_managed_subscription_wss import setup_db


def sample(epoch, sid, up, down):
    return XrayTrafficSnapshot(
        epoch=epoch,
        samples=[XrayTrafficSample(subscription_uuid=sid, uplink=up, downlink=down)],
    )


def test_commercial_cursors_are_opt_in_idempotent_and_reset_safe(tmp_path, monkeypatch):
    async def run():
        engine, factory, _agent, node_uuid, paid_uuid, _ = await setup_db(tmp_path)
        try:
            monkeypatch.setattr(usage_settings, "xray_usage_billing_enabled", True)
            from app.routers.node_registry import ManagedInboundInput, enable_managed_xray_inbound
            async with factory() as db:
                await enable_managed_xray_inbound(
                    node_uuid, "allow-vless", ManagedInboundInput(expected_generation=1), db=db, _=object()
                )
                role = AdminRole(name="billing-test-role")
                db.add(role)
                await db.flush()
                owner = Admin(username="billing-test-admin", hashed_password="synthetic", role_id=role.id)
                db.add(owner)
                await db.flush()
                owner_id = owner.id
                await db.execute(
                    update(Subscription).where(Subscription.uuid == paid_uuid).values(admin_id=owner_id)
                )
                await db.commit()
                epoch1 = datetime.now(UTC) - timedelta(hours=2)
                first = await record_xray_traffic(db, str(node_uuid), sample(epoch1, paid_uuid, 100, 200))
                assert first["billed_bytes"] == 0  # Initial baseline, no back billing.
                await record_xray_traffic(db, str(node_uuid), sample(epoch1, paid_uuid, 120, 230))
                await record_xray_traffic(db, str(node_uuid), sample(epoch1, paid_uuid, 120, 230))
                epoch2 = epoch1 + timedelta(hours=1)
                await record_xray_traffic(db, str(node_uuid), sample(epoch2, paid_uuid, 25, 35))
                await record_xray_traffic(db, str(node_uuid), sample(epoch1, paid_uuid, 999, 999))
                assert (await db.get(Subscription, paid_uuid)).used_traffic == 110
                assert (await db.get(Admin, owner_id)).used_traffic == 110
                cursor = (await db.execute(select(node_agent_xray_usage_cursors))).mappings().one()
                assert cursor["total_uplink"] == 145
                assert cursor["total_downlink"] == 265
                assert cursor["billing_ready"] is True
                hours = (await db.execute(select(node_agent_xray_usage_hours))).mappings().all()
                assert sum(h["uplink"] for h in hours) == 145
                assert sum(h["downlink"] for h in hours) == 265

                monkeypatch.setattr(usage_settings, "xray_usage_billing_enabled", False)
                await record_xray_traffic(db, str(node_uuid), sample(epoch2, paid_uuid, 100, 100))
                assert (await db.get(Subscription, paid_uuid)).used_traffic == 110
                monkeypatch.setattr(usage_settings, "xray_usage_billing_enabled", True)
                await record_xray_traffic(db, str(node_uuid), sample(epoch2, paid_uuid, 130, 140))
                assert (await db.get(Subscription, paid_uuid)).used_traffic == 110
                await record_xray_traffic(db, str(node_uuid), sample(epoch2, paid_uuid, 150, 150))
                assert (await db.get(Subscription, paid_uuid)).used_traffic == 140
                assert (await db.get(Admin, owner_id)).used_traffic == 140
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_quota_transition_queues_new_wss_generation_atomically(tmp_path, monkeypatch):
    async def run():
        engine, factory, _agent, node_uuid, paid_uuid, _ = await setup_db(tmp_path)
        try:
            monkeypatch.setattr(usage_settings, "xray_usage_billing_enabled", True)
            from app.routers.node_registry import (
                ManagedInboundInput,
                enable_managed_xray_inbound,
            )
            async with factory() as db:
                await enable_managed_xray_inbound(
                    node_uuid, "allow-vless", ManagedInboundInput(expected_generation=1), db=db, _=object()
                )
                await db.execute(
                    update(Subscription).where(Subscription.uuid == paid_uuid).values(data_limit=100)
                )
                await db.commit()
                old = datetime.now(UTC) - timedelta(hours=1)
                await record_xray_traffic(db, str(node_uuid), sample(old, paid_uuid, 10, 10))
                desired_before = (await db.execute(select(node_agent_xray_desired))).mappings().one()
                await record_xray_traffic(db, str(node_uuid), sample(old, paid_uuid, 50, 60))
                desired_after = (await db.execute(select(node_agent_xray_desired))).mappings().one()
                assert (await db.get(Subscription, paid_uuid)).used_traffic == 90
                assert desired_after["generation"] == desired_before["generation"]
                await record_xray_traffic(db, str(node_uuid), sample(old, paid_uuid, 70, 90))
                desired_quota = (await db.execute(select(node_agent_xray_desired))).mappings().one()
                assert (await db.get(Subscription, paid_uuid)).used_traffic == 140
                assert desired_quota["generation"] == desired_before["generation"] + 1
                assert desired_quota["runtime_status"] == "pending"
        finally:
            await engine.dispose()
    asyncio.run(run())
