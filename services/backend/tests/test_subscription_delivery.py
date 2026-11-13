"""No bridge Protobuf/NATS delivery remains in subscription admission."""
import asyncio

from sqlalchemy import select

from app import subscription_delivery
from app.db.models import Subscription, node_agent_xray_desired
from app.routers.node_registry import (
    ManagedInboundInput,
    XrayReport,
    agent_xray_report,
    enable_managed_xray_inbound,
)
from tests.test_node_managed_subscription_wss import setup_db


def test_commercial_signature_and_reconcile_without_node_rpc(tmp_path, monkeypatch):
    async def run():
        engine, factory, agent, uid, paid_uuid, _ = await setup_db(tmp_path)
        try:
            async with factory() as db:
                await enable_managed_xray_inbound(
                    uid, "allow-vless", ManagedInboundInput(expected_generation=1), db=db, _=object()
                )
                await agent_xray_report(
                    XrayReport(node_uuid=uid, generation=2, status="running"),
                    x_node_secret=agent["node_token"], db=db,
                )

            class DB:
                async def __aenter__(self):
                    self.session = factory()
                    return self.session

                async def __aexit__(self, *args):
                    await self.session.close()

            monkeypatch.setattr(subscription_delivery, "GetDB", DB)
            async with factory() as db:
                user = await db.get(Subscription, paid_uuid)
                before = await subscription_delivery.node_payload_signature(user)
                user.entitlement = "payment_required"
                await db.commit()
                after = await subscription_delivery.node_payload_signature(user)
                assert after != before
                assert str(paid_uuid) == before[0] == after[0]

            assert await subscription_delivery.reconcile_subscriptions() == 1
            async with factory() as db:
                state = (await db.execute(select(node_agent_xray_desired))).mappings().one()
                assert state["generation"] == 3
                assert state["action"] == "reconfigure"
                assert state["source_core_id"] is None
                assert state["config"]["inbounds"][0]["settings"]["clients"] == []
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_empty_subscription_batch_never_queues_node_generations(tmp_path, monkeypatch):
    async def run():
        engine, factory, _agent, _uid, _paid_uuid, _ = await setup_db(tmp_path)
        try:
            class DB:
                async def __aenter__(self):
                    self.session = factory()
                    return self.session

                async def __aexit__(self, *args):
                    await self.session.close()
            monkeypatch.setattr(subscription_delivery, "GetDB", DB)
            await subscription_delivery.sync_subscriptions([])
            await subscription_delivery.remove_subscriptions([], await_dispatch=True)
            async with factory() as db:
                state = (await db.execute(select(node_agent_xray_desired))).mappings().one()
                assert state["generation"] == 1
        finally:
            await engine.dispose()
    asyncio.run(run())
