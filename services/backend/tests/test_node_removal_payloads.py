"""Removals delivered from the current-state refresh keep the identity a node needs to remove the user.

Released node v0.5.4's WireGuard backend ignores a user whose payload has no
public key or no peer IPs before it computes which peers to remove, so a
removal without them leaves the peer in place.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import (
    Admin,
    AdminRole,
    AdminStatus,
    Group,
    ProxyInbound,
    Subscription,
    SubscriptionStatus,
    inbounds_groups_association,
    subscriptions_groups_association,
)
from app.jobs import review_subscriptions
from app.node import bridge as bridge_module, sync as node_sync
from app.node.subscription import serialize_subscription
from tests.test_node_bridge import FakeNodeState, attach_fake_transport, close_node, make_node, wait_until
from waylane_node_bridge import InMemoryUserSyncStore
from waylane_node_bridge.common.service_pb2 import Subscription as ProtoSubscription

PUBLIC_KEY = "x3rO3jkvA0vlN8kHBXnCEW3OQ7bU+Z1tK1Ow4FbMexM="
PEER_IPS = ["10.30.0.2/32"]


class _Session:
    def __init__(self, maker):
        self._session = maker()

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        await self._session.close()
        return False


@pytest.fixture
async def maker(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(node_sync, "GetDB", lambda: _Session(factory))
    # A reader bound to this test's loop, and the panel's refresh handler (other tests reset it).
    monkeypatch.setattr(node_sync, "_current_state", node_sync.CurrentStateReader())
    monkeypatch.setattr(bridge_module, "_refresh_handler", node_sync.refresh_node_subscriptions)
    yield factory
    await engine.dispose()


async def seed_wireguard_subscription(maker, *, admin_id=None, entitlement="active") -> UUID:
    async with maker() as db:
        inbound_id = (await db.execute(insert(ProxyInbound).values(tag="wg0").returning(ProxyInbound.id))).scalar_one()
        group_id = (
            await db.execute(insert(Group).values(name="g", is_disabled=False).returning(Group.id))
        ).scalar_one()
        await db.execute(insert(inbounds_groups_association).values(inbound_id=inbound_id, group_id=group_id))
        subscription_uuid = (
            await db.execute(
                insert(Subscription)
                .values(
                    uuid=uuid4(),
                    status=SubscriptionStatus.active,
                    entitlement=entitlement,
                    created_at=datetime.now(UTC),
                    admin_id=admin_id,
                    proxy_settings={
                        "wireguard": {"public_key": PUBLIC_KEY, "private_key": "", "peer_ips": PEER_IPS},
                        "vless": {"id": str(uuid4())},
                    },
                )
                .returning(Subscription.uuid)
            )
        ).scalar_one()
        await db.execute(
            insert(subscriptions_groups_association).values(subscription_uuid=subscription_uuid, groups_id=group_id)
        )
        await db.commit()
    return subscription_uuid


def wireguard(payload: ProtoSubscription) -> tuple[str, list[str]]:
    return payload.proxies.wireguard.public_key, list(payload.proxies.wireguard.peer_ips)


async def test_active_subscription_refresh_keeps_inbounds_and_credentials(maker):
    subscription_uuid = await seed_wireguard_subscription(maker)
    [payload] = await node_sync.refresh_node_subscriptions("n1", [str(subscription_uuid)])
    assert list(payload.inbounds) == ["wg0"]
    assert wireguard(payload) == (PUBLIC_KEY, PEER_IPS)


async def test_unpaid_subscription_refresh_is_removal_with_stored_wireguard_credentials(maker):
    """A present but unpaid subscription must yield an immediate node removal."""
    subscription_uuid = await seed_wireguard_subscription(maker, entitlement="payment_required")
    [payload] = await node_sync.refresh_node_subscriptions("n1", [str(subscription_uuid)])
    assert payload.subscription_uuid == str(subscription_uuid)
    assert list(payload.inbounds) == []
    assert wireguard(payload) == (PUBLIC_KEY, PEER_IPS)


@pytest.mark.parametrize(
    "status", [SubscriptionStatus.disabled, SubscriptionStatus.expired, SubscriptionStatus.limited]
)
async def test_inactive_subscription_refresh_is_a_removal_with_stored_credentials(maker, status):
    subscription_uuid = await seed_wireguard_subscription(maker)
    async with maker() as db:
        await db.execute(update(Subscription).where(Subscription.uuid == subscription_uuid).values(status=status))
        await db.commit()

    [payload] = await node_sync.refresh_node_subscriptions("n1", [str(subscription_uuid)])
    assert payload.subscription_uuid == str(subscription_uuid)
    assert list(payload.inbounds) == []
    assert wireguard(payload) == (PUBLIC_KEY, PEER_IPS)


async def test_subscription_without_inbounds_refresh_is_a_removal_with_stored_credentials(maker):
    subscription_uuid = await seed_wireguard_subscription(maker)
    async with maker() as db:
        await db.execute(update(Group).values(is_disabled=True))
        await db.commit()

    [payload] = await node_sync.refresh_node_subscriptions("n1", [str(subscription_uuid)])
    assert list(payload.inbounds) == []
    assert wireguard(payload) == (PUBLIC_KEY, PEER_IPS)


async def test_admin_blocked_subscription_refresh_is_a_removal_with_stored_credentials(maker):
    async with maker() as db:
        role = AdminRole(name="r", disconnect_users_when_disabled=True)
        db.add(role)
        await db.flush()
        admin = Admin(username="a", hashed_password="x", status=AdminStatus.disabled, role_id=role.id)
        db.add(admin)
        await db.commit()
        admin_id = admin.id
    subscription_uuid = await seed_wireguard_subscription(maker, admin_id=admin_id)

    [payload] = await node_sync.refresh_node_subscriptions("n1", [str(subscription_uuid)])
    assert list(payload.inbounds) == []
    assert wireguard(payload) == (PUBLIC_KEY, PEER_IPS)


async def test_deleted_subscription_refresh_carries_no_inbounds(maker):
    subscription_uuid = await seed_wireguard_subscription(maker)
    async with maker() as db:
        await db.execute(delete(Subscription).where(Subscription.uuid == subscription_uuid))
        await db.commit()

    [payload] = await node_sync.refresh_node_subscriptions("n1", [str(subscription_uuid)])
    assert payload.subscription_uuid == str(subscription_uuid) and list(payload.inbounds) == []


def _capturing_node(monkeypatch):
    """A real queued adapter whose transport records every delivered payload."""
    node = make_node("grpc", InMemoryUserSyncStore())
    seen: list[ProtoSubscription] = []
    state = FakeNodeState()
    original = state.apply_delta

    async def capture(users):
        seen.extend(users)
        return await original(users)

    state.apply_delta = capture
    attach_fake_transport(monkeypatch, node, state)
    return node, seen


async def _deliver(monkeypatch, queued: ProtoSubscription) -> ProtoSubscription:
    node, seen = _capturing_node(monkeypatch)
    try:
        await node.connect("0.5.4", "26.10.10")
        await node.update_user(queued)
        await wait_until(lambda: seen)
    finally:
        await close_node(node)
    [delivered] = seen
    return delivered


async def test_deleted_user_removal_keeps_the_queued_credentials_on_the_wire(maker, monkeypatch):
    subscription_uuid = await seed_wireguard_subscription(maker)
    async with maker() as db:
        db_user = (await db.execute(select(Subscription).where(Subscription.uuid == subscription_uuid))).scalar_one()
        settings = dict(db_user.proxy_settings)
        await db.execute(delete(Subscription).where(Subscription.uuid == subscription_uuid))
        await db.commit()

    # What remove_user() enqueues after the delete commits: stored credentials, no inbounds.
    delivered = await _deliver(monkeypatch, node_sync._serialize_subscription_for_node(subscription_uuid, settings))
    assert delivered.subscription_uuid == str(subscription_uuid)
    assert list(delivered.inbounds) == []
    assert wireguard(delivered) == (PUBLIC_KEY, PEER_IPS)


async def test_deleted_user_with_a_stale_queued_add_is_still_removed(maker, monkeypatch):
    subscription_uuid = await seed_wireguard_subscription(maker)
    async with maker() as db:
        db_user = (await db.execute(select(Subscription).where(Subscription.uuid == subscription_uuid))).scalar_one()
        stale_add = await serialize_subscription(db_user)
        await db.execute(delete(Subscription).where(Subscription.uuid == subscription_uuid))
        await db.commit()
    assert list(stale_add.inbounds) == ["wg0"]

    # An older edit's payload queued after the removal must not re-add the user.
    delivered = await _deliver(monkeypatch, stale_add)
    assert list(delivered.inbounds) == []
    assert wireguard(delivered) == (PUBLIC_KEY, PEER_IPS)


async def test_disabled_user_removal_on_the_wire_keeps_credentials(maker, monkeypatch):
    subscription_uuid = await seed_wireguard_subscription(maker)
    async with maker() as db:
        await db.execute(
            update(Subscription)
            .where(Subscription.uuid == subscription_uuid)
            .values(status=SubscriptionStatus.disabled)
        )
        await db.commit()
        db_user = (await db.execute(select(Subscription).where(Subscription.uuid == subscription_uuid))).scalar_one()
        queued = await serialize_subscription(db_user)

    delivered = await _deliver(monkeypatch, queued)
    assert list(delivered.inbounds) == []
    assert wireguard(delivered) == (PUBLIC_KEY, PEER_IPS)


async def test_core_owned_on_hold_subscription_is_never_locally_deleted(maker):
    """On-hold access is Core-managed, not removed by WayLane's local scheduler."""
    subscription_uuid = await seed_wireguard_subscription(maker)
    async with maker() as db:
        await db.execute(
            update(Subscription).where(Subscription.uuid == subscription_uuid).values(status=SubscriptionStatus.on_hold)
        )
        await db.commit()
    assert not hasattr(review_subscriptions, "remove_on_hold_users")
    [payload] = await node_sync.refresh_node_subscriptions("n1", [str(subscription_uuid)])
    assert payload.subscription_uuid == str(subscription_uuid)
    assert list(payload.inbounds) == ["wg0"]
    assert wireguard(payload) == (PUBLIC_KEY, PEER_IPS)
    async with maker() as db:
        assert (
            await db.scalar(select(Subscription.uuid).where(Subscription.uuid == subscription_uuid))
            == subscription_uuid
        )


@pytest.mark.parametrize(
    "wireguard_now",
    [None, {"public_key": PUBLIC_KEY, "private_key": "", "peer_ips": []}],
    ids=["settings-removed", "peer-ips-cleared"],
)
async def test_removal_keeps_the_queued_wireguard_identity_when_the_stored_one_is_gone(
    maker, monkeypatch, wireguard_now
):
    subscription_uuid = await seed_wireguard_subscription(maker)
    async with maker() as db:
        await db.execute(
            update(Subscription)
            .where(Subscription.uuid == subscription_uuid)
            .values(status=SubscriptionStatus.disabled)
        )
        await db.commit()
        db_user = (await db.execute(select(Subscription).where(Subscription.uuid == subscription_uuid))).scalar_one()
        queued = await serialize_subscription(db_user)
        # The WireGuard settings change before the queued removal is delivered; VLESS stays.
        settings = {"vless": dict(db_user.proxy_settings["vless"])}
        if wireguard_now is not None:
            settings["wireguard"] = wireguard_now
        await db.execute(
            update(Subscription).where(Subscription.uuid == subscription_uuid).values(proxy_settings=settings)
        )
        await db.commit()

    delivered = await _deliver(monkeypatch, queued)
    assert list(delivered.inbounds) == []
    # The node can only find the peer by the identity it was added with.
    assert wireguard(delivered) == (PUBLIC_KEY, PEER_IPS)
    assert delivered.proxies.vless.id == settings["vless"]["id"]
