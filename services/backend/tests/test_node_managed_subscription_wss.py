"""Autonomous WSS subscription admission uses current UUID-first product state."""
import asyncio
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from google.protobuf.json_format import MessageToDict
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import node_binary_control as control
from app.db.base import Base
from app.db.models import (
    Group,
    ProxyInbound,
    Subscription,
    SubscriptionStatus,
    inbounds_groups_association,
    node_agent_xray_desired,
    subscriptions_groups_association,
)
from app.node_subscription_desired import (
    ManagedSubscriptionError,
    enqueue_managed_subscription_changes,
    inject_managed_subscriptions,
)
from app.routers.node_registry import (
    ManagedInboundInput,
    XrayDesiredInput,
    XrayReport,
    agent_xray_desired,
    agent_xray_report,
    create_classic_node,
    disable_managed_xray_inbound,
    enable_managed_xray_inbound,
    list_managed_xray_inbounds,
    set_desired_xray,
)


def config(tag="allow-vless", protocol="vless"):
    return {"inbounds": [{"tag": tag, "listen": "127.0.0.1", "port": 18445,
                           "protocol": protocol, "settings": {"clients": []}}],
            "outbounds": [{"tag": "direct", "protocol": "freedom"}]}


async def setup_db(tmp_path, *, protocol="vless"):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/subscriptions-wss.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        inbound = ProxyInbound(tag="allow-vless")
        group = Group(name="paid-group", inbounds=[])
        paid = Subscription(entitlement="active", status=SubscriptionStatus.active,
                            proxy_settings={protocol: {"id": str(uuid4())}})
        unpaid = Subscription(entitlement="payment_required", status=SubscriptionStatus.active,
                              proxy_settings={protocol: {"id": str(uuid4())}})
        db.add_all([inbound, group, paid, unpaid])
        await db.flush()
        await db.execute(insert(inbounds_groups_association).values(inbound_id=inbound.id, group_id=group.id))
        for subscriber in (paid, unpaid):
            await db.execute(insert(subscriptions_groups_association).values(
                subscription_uuid=subscriber.uuid, groups_id=group.id))
        paid_uuid, unpaid_uuid = paid.uuid, unpaid.uuid
        agent = await create_classic_node(db=db, _=object())
        node_uuid = UUID(agent["node_uuid"])
        from app.db.models import node_agent_registrations
        await db.execute(update(node_agent_registrations).where(
            node_agent_registrations.c.node_uuid == str(node_uuid)
        ).values(capabilities=["xray.protobuf.binary"]))
        await db.commit()
        await set_desired_xray(node_uuid, XrayDesiredInput(action="deploy", config=config(protocol=protocol)),
                               db=db, _=object())
        await agent_xray_report(XrayReport(node_uuid=node_uuid, generation=1, status="running"),
                                x_node_secret=agent["node_token"], db=db)
        return engine, factory, agent, node_uuid, paid_uuid, unpaid_uuid


def test_opt_in_to_managed_inbound_and_revocation_over_binary_wss(tmp_path, monkeypatch):
    async def run():
        engine, factory, agent, node_uuid, paid_uuid, unpaid_uuid = await setup_db(tmp_path)
        monkeypatch.setattr(control, "SessionLocal", factory)
        try:
            async with factory() as db:
                desired = (await db.execute(select(node_agent_xray_desired))).mappings().one()
                assert (await inject_managed_subscriptions(db, str(node_uuid), desired["config"])) == config()
                first = await enable_managed_xray_inbound(
                    node_uuid, "allow-vless", ManagedInboundInput(expected_generation=1), db=db, _=object())
                assert first == {"inbound_tag": "allow-vless", "generation": 2, "status": "pending"}
                assert (await list_managed_xray_inbounds(node_uuid, db=db, _=object())) == {
                    "inbound_tags": ["allow-vless"]}
                stored = (await db.execute(select(node_agent_xray_desired))).mappings().one()
                assert stored["config"]["inbounds"][0]["settings"]["clients"] == []
                frame = await agent_xray_desired(node_uuid, x_node_secret=agent["node_token"], db=db)
                clients = frame["config"]["inbounds"][0]["settings"]["clients"]
                assert len(clients) == 1
                assert clients[0]["email"] == str(paid_uuid)
                assert clients[0]["email"] != str(unpaid_uuid)
                session = control.AgentBinarySession(str(node_uuid), agent["node_token"])
                await session.receive(control.new_message("hello", str(node_uuid)))
                assert (await session.outgoing.get()).kind == "hello_ack"
                await session.push_desired()
                desired_frame = await session.outgoing.get()
                assert desired_frame.kind == "desired"
                payload = MessageToDict(desired_frame.payload, preserving_proto_field_name=True)
                assert payload["config"]["inbounds"][0]["settings"]["clients"][0]["email"] == str(paid_uuid)
                await agent_xray_report(XrayReport(node_uuid=node_uuid, generation=2, status="running"),
                                        x_node_secret=agent["node_token"], db=db)
                await db.execute(update(Subscription).where(Subscription.uuid == paid_uuid).values(
                    entitlement="payment_required"))
                await db.commit()
                assert await enqueue_managed_subscription_changes(db) == 1
                await session.push_desired()
                revoked = await session.outgoing.get()
                assert revoked.generation == 3
                actual = MessageToDict(revoked.payload, preserving_proto_field_name=True)
                assert actual["config"]["inbounds"][0]["settings"]["clients"] == []
                db_state = (await db.execute(select(node_agent_xray_desired))).mappings().one()
                assert db_state["config"]["inbounds"][0]["settings"]["clients"] == []
                await session.close()
                await agent_xray_report(XrayReport(node_uuid=node_uuid, generation=3, status="running"),
                                        x_node_secret=agent["node_token"], db=db)
                removed = await disable_managed_xray_inbound(node_uuid, "allow-vless", expected_generation=3,
                                                              db=db, _=object())
                assert removed["generation"] == 4
                assert (await list_managed_xray_inbounds(node_uuid, db=db, _=object()))["inbound_tags"] == []
                assert await enqueue_managed_subscription_changes(db) == 0
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_socks_and_manually_managed_client_list_cannot_be_auto_replaced(tmp_path):
    async def run():
        engine, factory, _agent, node_uuid, _, _ = await setup_db(tmp_path)
        try:
            async with factory() as db:
                with pytest.raises(HTTPException) as missing:
                    await enable_managed_xray_inbound(node_uuid, "unregistered", ManagedInboundInput(
                        expected_generation=1), db=db, _=object())
                assert missing.value.status_code == 422
                await db.rollback()
                await db.execute(update(node_agent_xray_desired).where(
                    node_agent_xray_desired.c.node_uuid == str(node_uuid)).values(
                    config={"inbounds": [{"tag": "allow-vless", "protocol": "vless", "settings": {
                        "clients": [{"id": str(uuid4())}]}}], "outbounds": []}))
                await db.commit()
                with pytest.raises(HTTPException) as populated:
                    await enable_managed_xray_inbound(node_uuid, "allow-vless", ManagedInboundInput(
                        expected_generation=1), db=db, _=object())
                assert populated.value.status_code == 409
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_current_subscription_filters_on_entitlement_status_quota_expiry_and_group(tmp_path):
    from datetime import UTC, datetime, timedelta

    from app.db.models import Group

    async def run():
        engine, factory, agent, uid, paid, _ = await setup_db(tmp_path)
        try:
            async with factory() as db:
                await enable_managed_xray_inbound(uid, "allow-vless", ManagedInboundInput(
                    expected_generation=1), db=db, _=object())
                async def permitted() -> bool:
                    payload = await agent_xray_desired(uid, x_node_secret=agent["node_token"], db=db)
                    return any(c.get("email") == str(paid) for c in
                               payload["config"]["inbounds"][0]["settings"]["clients"])

                assert await permitted()
                await db.execute(update(Subscription).where(Subscription.uuid == paid).values(
                    status=SubscriptionStatus.disabled))
                await db.commit()
                assert not await permitted()
                await db.execute(update(Subscription).where(Subscription.uuid == paid).values(
                    status=SubscriptionStatus.active, data_limit=10, used_traffic=11))
                await db.commit()
                assert not await permitted()
                await db.execute(update(Subscription).where(Subscription.uuid == paid).values(
                    data_limit=None, used_traffic=0, _expire=datetime.now(UTC) - timedelta(minutes=5)))
                await db.commit()
                assert not await permitted()
                await db.execute(update(Subscription).where(Subscription.uuid == paid).values(_expire=None))
                await db.commit()
                assert await permitted()
                await db.execute(update(Group).values(is_disabled=True))
                await db.commit()
                assert not await permitted()
                await db.execute(update(Group).values(is_disabled=False))
                await db.commit()
                assert await permitted()
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_managed_inbound_rejects_manual_api_replacement_and_public_baseline_changes(tmp_path):
    from app.routers.node_registry import XrayInboundSubscriptionsInput, sync_xray_inbound_users

    async def run():
        engine, factory, _, uid, _, _ = await setup_db(tmp_path)
        try:
            async with factory() as db:
                await enable_managed_xray_inbound(uid, "allow-vless", ManagedInboundInput(
                    expected_generation=1), db=db, _=object())
                with pytest.raises(HTTPException) as managed:
                    await sync_xray_inbound_users(uid, "allow-vless", XrayInboundSubscriptionsInput(
                        expected_generation=2, users=[]), db=db, _=object())
                assert managed.value.status_code == 409
                await db.rollback()
                populated = config()
                populated["inbounds"][0]["settings"]["clients"] = [{"id": str(uuid4())}]
                with pytest.raises(HTTPException) as tampering:
                    await set_desired_xray(uid, XrayDesiredInput(action="deploy", config=populated),
                                           db=db, _=object())
                assert tampering.value.status_code == 409
                await db.rollback()
                missing_inbound = config("unexpected-tag")
                with pytest.raises(HTTPException) as missing:
                    await set_desired_xray(uid, XrayDesiredInput(action="deploy", config=missing_inbound),
                                           db=db, _=object())
                assert missing.value.status_code == 409
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_proto_client_adapter_uses_only_subscription_uuid_as_technical_email():
    from app.node_subscription_desired import _subscription_client
    subscriber = uuid4()
    for protocol, key in (("vless", "id"), ("vmess", "id"), ("trojan", "password")):
        credential = str(uuid4()) if protocol != "trojan" else "synthetic-trojan-password"
        rendered = _subscription_client(protocol, subscriber, {protocol: {key: credential}})
        assert rendered["email"] == str(subscriber)
        assert rendered[key] == credential
        assert all(k not in rendered for k in ("user_id", "subscription_id", "contact_email"))
        assert _subscription_client(protocol, subscriber, {protocol: {key: ""}}) is None


def test_suspended_admin_and_role_disconnect_revoke_managed_xray_clients(tmp_path):
    from app.db.models import Admin, AdminRole, AdminStatus

    async def run():
        engine, factory, agent, uid, paid, _ = await setup_db(tmp_path)
        try:
            async with factory() as db:
                role = AdminRole(name="suspended-test", disconnect_users_when_disabled=True)
                db.add(role)
                await db.flush()
                admin = Admin(username="synthetic-policy-admin", hashed_password="not-a-real-hash",
                              role_id=role.id, status=AdminStatus.disabled)
                db.add(admin)
                await db.flush()
                await db.execute(update(Subscription).where(Subscription.uuid == paid).values(admin_id=admin.id))
                await db.commit()
                await enable_managed_xray_inbound(uid, "allow-vless", ManagedInboundInput(
                    expected_generation=1), db=db, _=object())
                envelope = await agent_xray_desired(uid, x_node_secret=agent["node_token"], db=db)
                assert envelope["config"]["inbounds"][0]["settings"]["clients"] == []
                await db.execute(update(AdminRole).where(AdminRole.id == role.id).values(
                    disconnect_users_when_disabled=False))
                await db.commit()
                envelope = await agent_xray_desired(uid, x_node_secret=agent["node_token"], db=db)
                assert len(envelope["config"]["inbounds"][0]["settings"]["clients"]) == 1
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_subscription_dispatch_queues_new_node_before_old_transport(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from app.node import sync as subscription_sync

    async def run():
        engine, factory, agent, uid, _, _ = await setup_db(tmp_path)
        try:
            async with factory() as db:
                await enable_managed_xray_inbound(uid, "allow-vless", ManagedInboundInput(
                    expected_generation=1), db=db, _=object())
                await agent_xray_report(XrayReport(node_uuid=uid, generation=2, status="running"),
                                        x_node_secret=agent["node_token"], db=db)

            class LocalDB:
                async def __aenter__(self):
                    self.db = factory()
                    return self.db

                async def __aexit__(self, *args):
                    await self.db.close()

            monkeypatch.setattr(subscription_sync, "GetDB", LocalDB)
            old_transport = AsyncMock()
            monkeypatch.setattr(subscription_sync.node_manager, "update_subscription", old_transport)
            await subscription_sync._dispatch_subscription_update(object())
            old_transport.assert_awaited_once()
            async with factory() as db:
                entry = (await db.execute(select(node_agent_xray_desired))).mappings().one()
                assert entry["generation"] == 3 and entry["action"] == "reconfigure"
                assert entry["config"]["inbounds"][0]["settings"]["clients"] == []
                await db.execute(update(node_agent_xray_desired).where(
                    node_agent_xray_desired.c.node_uuid == str(uid)
                ).values(generation=4, action="stop", runtime_status="pending"))
                await db.commit()
                assert await enqueue_managed_subscription_changes(db) == 0
                stopped = (await db.execute(select(node_agent_xray_desired))).mappings().one()
                assert stopped["generation"] == 4 and stopped["action"] == "stop"
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_core_editor_cannot_replace_a_managed_client_list(tmp_path):
    from app.db.models import CoreConfig, CoreType
    from app.node_subscription_desired import ensure_assigned_editor_core_compatible

    async def run():
        engine, factory, _, uid, _, _ = await setup_db(tmp_path)
        try:
            async with factory() as db:
                await enable_managed_xray_inbound(uid, "allow-vless", ManagedInboundInput(
                    expected_generation=1), db=db, _=object())
                core = CoreConfig(name="managed-source", type=CoreType.xray, config=config())
                db.add(core)
                await db.flush()
                core_id = core.id
                await db.execute(update(node_agent_xray_desired).where(
                    node_agent_xray_desired.c.node_uuid == str(uid)).values(source_core_id=core_id))
                await db.commit()
                await ensure_assigned_editor_core_compatible(db, core_id, config())
                changed = config()
                changed["inbounds"][0]["settings"]["clients"] = [{"id": str(uuid4())}]
                with pytest.raises(ManagedSubscriptionError):
                    await ensure_assigned_editor_core_compatible(db, core_id, changed)
                with pytest.raises(ManagedSubscriptionError):
                    await ensure_assigned_editor_core_compatible(db, core_id, config("other-inbound"))
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_pending_operator_image_update_survives_subscription_reconcile(tmp_path):
    """A payment event cannot silently downgrade pending image update to reload."""
    async def run():
        engine, factory, agent, uid, _, _ = await setup_db(tmp_path)
        try:
            async with factory() as db:
                await enable_managed_xray_inbound(uid, "allow-vless", ManagedInboundInput(
                    expected_generation=1), db=db, _=object())
                await agent_xray_report(XrayReport(node_uuid=uid, generation=2, status="running"),
                                        x_node_secret=agent["node_token"], db=db)
                await db.execute(update(node_agent_xray_desired).where(
                    node_agent_xray_desired.c.node_uuid == str(uid)
                ).values(generation=3, action="update", runtime_status="pending"))
                await db.commit()
                assert await enqueue_managed_subscription_changes(db) == 1
                entry = (await db.execute(select(node_agent_xray_desired))).mappings().one()
                assert entry["generation"] == 4 and entry["action"] == "update"
                # A second payment event with a pending reconfigure must not
                # issue a full Core deploy, which would disrupt active users.
                await db.execute(update(node_agent_xray_desired).where(
                    node_agent_xray_desired.c.node_uuid == str(uid)
                ).values(generation=5, action="reconfigure", runtime_status="pending"))
                await db.commit()
                assert await enqueue_managed_subscription_changes(db) == 1
                entry = (await db.execute(select(node_agent_xray_desired))).mappings().one()
                assert entry["generation"] == 6 and entry["action"] == "reconfigure"
        finally:
            await engine.dispose()
    asyncio.run(run())
