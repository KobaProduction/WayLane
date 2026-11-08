from uuid import UUID

from sqlalchemy import and_, func, or_, select

from app.db import AsyncSession
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
from app.models.protocol import ProxyProtocol
from waylane_node_bridge import create_proxy, create_subscription
from waylane_node_bridge.common.service_pb2 import Subscription as ProtoSubscription

_ALL_PROXY_PROTOCOLS = frozenset(ProxyProtocol)


def _inbounds_from_loaded_groups(subscription: Subscription) -> list[str] | None:
    loaded_groups = subscription.__dict__.get("groups")
    if loaded_groups is None:
        return None

    tags: set[str] = set()
    for group in loaded_groups:
        if group.is_disabled:
            continue

        loaded_inbounds = group.__dict__.get("inbounds")
        if loaded_inbounds is None:
            return None

        for inbound in loaded_inbounds:
            tags.add(inbound.tag)

    return list(tags)


async def _commercial_entitlement(subscription: Subscription) -> str:
    """Read admission without triggering forbidden lazy I/O on async ORM rows."""
    value = subscription.__dict__.get("entitlement")
    return value if value is not None else await subscription.awaitable_attrs.entitlement


async def serialize_subscription(
    subscription: Subscription, allowed_protocols: frozenset[ProxyProtocol] | None = None
) -> ProtoSubscription:
    subscription_settings = subscription.proxy_settings
    inbounds = None
    status = subscription.__dict__.get("status")
    if status is None:
        status = await subscription.awaitable_attrs.status

    if await _commercial_entitlement(subscription) == "active" and status in (
        SubscriptionStatus.active,
        SubscriptionStatus.on_hold,
    ):
        inbounds = _inbounds_from_loaded_groups(subscription)
        if inbounds is None:
            inbounds = await subscription.inbounds()

    return _serialize_subscription_for_node(subscription.uuid, subscription_settings, inbounds, allowed_protocols)


async def node_payload_signature(subscription: Subscription) -> tuple[str, bytes, tuple[str, ...]]:
    """What a node would receive for this subscription, in a form that can be compared before and after a change.

    Uses the same serialization as delivery (credentials, inbounds derived from
    enabled groups, and status), so equal signatures mean no node needs an update.
    """
    proto = await serialize_subscription(subscription)
    return proto.subscription_uuid, proto.proxies.SerializeToString(deterministic=True), tuple(sorted(proto.inbounds))


def _serialize_subscription_for_node(
    subscription_uuid: UUID,
    subscription_settings: dict,
    inbounds: list[str] | None = None,
    allowed_protocols: frozenset[ProxyProtocol] | None = None,
) -> ProtoSubscription:
    """Build native Node Protobuf Subscription from UUID and selected VPN inbounds.

    Include credentials only for ``allowed_protocols``; both ``None`` and an
    empty set select all protocols. MASQUE uses the stored ``password`` as its
    node credential. Bridge serialization errors propagate to the caller.
    """
    allowed_protocols = allowed_protocols or _ALL_PROXY_PROTOCOLS

    proxy_kwargs = {}
    if ProxyProtocol.vmess in allowed_protocols:
        proxy_kwargs["vmess_id"] = subscription_settings.get("vmess", {}).get("id")
    if ProxyProtocol.vless in allowed_protocols:
        proxy_kwargs["vless_id"] = subscription_settings.get("vless", {}).get("id")
    if ProxyProtocol.trojan in allowed_protocols:
        proxy_kwargs["trojan_password"] = subscription_settings.get("trojan", {}).get("password")
    if ProxyProtocol.shadowsocks in allowed_protocols:
        shadowsocks_settings = subscription_settings.get("shadowsocks", {})
        proxy_kwargs["shadowsocks_password"] = shadowsocks_settings.get("password")
        proxy_kwargs["shadowsocks_method"] = shadowsocks_settings.get("method")
    if ProxyProtocol.wireguard in allowed_protocols:
        wireguard_settings = subscription_settings.get("wireguard", {})
        proxy_kwargs["wireguard_public_key"] = wireguard_settings.get("public_key")
        proxy_kwargs["wireguard_peer_ips"] = wireguard_settings.get("peer_ips") or []
    if ProxyProtocol.hysteria in allowed_protocols:
        proxy_kwargs["hysteria_auth"] = subscription_settings.get("hysteria", {}).get("auth")
    if ProxyProtocol.masque in allowed_protocols:
        proxy_kwargs["masque_pass"] = subscription_settings.get("masque", {}).get("password")

    return create_subscription(
        str(subscription_uuid),
        create_proxy(**proxy_kwargs),
        inbounds,
    )


async def core_subscriptions(
    db: AsyncSession,
    inbound_tags: list[str] | set[str] | None = None,
    allowed_protocols: frozenset[ProxyProtocol] | None = None,
    subscription_uuids: list[UUID] | set[UUID] | None = None,
):
    """Serialize subscriptions for a core, optionally limited to UUIDs (one indexed query)."""
    if subscription_uuids is not None and not subscription_uuids:
        return []
    dialect = db.bind.dialect.name
    inbound_tags = list(dict.fromkeys(inbound_tags or []))

    # Use dialect-specific aggregation and grouping
    if dialect == "postgresql":
        inbound_agg = func.string_agg(ProxyInbound.tag.distinct(), ",").label("inbound_tags")
    else:
        # MySQL and SQLite use group_concat
        inbound_agg = func.group_concat(ProxyInbound.tag.distinct()).label("inbound_tags")

    stmt = (
        select(
            Subscription.uuid,
            Subscription.proxy_settings,
            inbound_agg,
        )
        .outerjoin(
            subscriptions_groups_association, Subscription.uuid == subscriptions_groups_association.c.subscription_uuid
        )
        .outerjoin(
            Group,
            and_(
                subscriptions_groups_association.c.groups_id == Group.id,
                Group.is_disabled.is_(False),
            ),
        )
        .outerjoin(inbounds_groups_association, Group.id == inbounds_groups_association.c.group_id)
        .outerjoin(
            ProxyInbound,
            and_(
                inbounds_groups_association.c.inbound_id == ProxyInbound.id,
                ProxyInbound.tag.in_(inbound_tags) if inbound_tags else True,
            ),
        )
        # Exclude subscriptions whose admin role blocks subscription sync for the admin's current status.
        .outerjoin(Admin, Admin.id == Subscription.admin_id)
        .outerjoin(AdminRole, AdminRole.id == Admin.role_id)
        .where(Subscription.status.in_([SubscriptionStatus.active, SubscriptionStatus.on_hold]))
        .where(Subscription.entitlement == "active")
        .where(
            or_(
                Admin.id.is_(None),
                Admin.status.not_in([AdminStatus.limited, AdminStatus.disabled]),
                and_(Admin.status == AdminStatus.limited, AdminRole.disconnect_users_when_limited.is_not(True)),
                and_(Admin.status == AdminStatus.disabled, AdminRole.disconnect_users_when_disabled.is_not(True)),
            )
        )
        .group_by(Subscription.uuid)
    )
    if subscription_uuids is not None:
        stmt = stmt.where(Subscription.uuid.in_(list(subscription_uuids)))

    results = (await db.execute(stmt)).all()
    bridge_subscriptions: list = []

    for row in results:
        inbound_tags = row.inbound_tags.split(",") if row.inbound_tags else []
        if inbound_tags:
            bridge_subscriptions.append(
                _serialize_subscription_for_node(
                    row.uuid,
                    row.proxy_settings,
                    inbound_tags,
                    allowed_protocols,
                )
            )
    return bridge_subscriptions


async def removal_subscriptions(
    db: AsyncSession, subscription_uuids: list[UUID] | set[UUID]
) -> list[ProtoSubscription]:
    """Removal payloads (no inbounds) with the stored credentials of the given subscriptions that still exist.

    Nodes may need the credentials to find what to remove: node v0.5.4's
    WireGuard backend ignores a removal without a public key and peer IPs.
    """
    if not subscription_uuids:
        return []
    stmt = select(Subscription.uuid, Subscription.proxy_settings).where(Subscription.uuid.in_(list(subscription_uuids)))
    return [
        _serialize_subscription_for_node(row.uuid, row.proxy_settings or {}) for row in (await db.execute(stmt)).all()
    ]


async def serialize_subscriptions_for_node(
    subscriptions: list[Subscription],
    allowed_protocols: frozenset[ProxyProtocol] | None = None,
) -> list[ProtoSubscription]:
    """Serialize subscriptions for node dispatch."""
    bridge_subscriptions: list = []

    for subscription in subscriptions:
        inbounds_list = []
        if await _commercial_entitlement(subscription) == "active" and subscription.status in [
            SubscriptionStatus.active,
            SubscriptionStatus.on_hold,
        ]:
            loaded_inbounds = _inbounds_from_loaded_groups(subscription)
            if loaded_inbounds is None:
                inbounds_list = await subscription.inbounds()
            else:
                inbounds_list = loaded_inbounds

        bridge_subscriptions.append(
            _serialize_subscription_for_node(
                subscription.uuid, subscription.proxy_settings, inbounds_list, allowed_protocols
            )
        )

    return bridge_subscriptions
