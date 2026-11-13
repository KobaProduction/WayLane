"""UUID-first Xray subscription admission over the existing binary WSS Core desired state.

A Node's inbound must be explicitly opted in. No accounts or raw credentials
are persisted in Node desired JSON. The latest committed commercial admission,
Group membership and protocol credentials are read for each authenticated WSS
frame, so an obsolete queued update cannot re-enable a revoked subscription.
"""

from __future__ import annotations

import copy
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import begin_immediate_if_sqlite
from app.db.models import (
    Admin,
    AdminRole,
    AdminStatus,
    Group,
    ProxyInbound,
    Subscription,
    SubscriptionStatus,
    inbounds_groups_association,
    node_agent_registrations,
    node_agent_xray_desired,
    node_agent_xray_managed_inbounds,
    subscriptions_groups_association,
)

SUPPORTED_PROTOCOLS = frozenset({"vless", "vmess", "trojan"})
MAX_MANAGED_INBOUNDS = 16
MAX_USERS_PER_INBOUND = 1024
MAX_TARGET_NODES = 256
MAX_XRAY_PAYLOAD_BYTES = 512_000


class ManagedSubscriptionError(ValueError):
    """Invalid or unsafe autonomous Node subscription admission."""


def _inbound_map(config: dict) -> dict[str, dict]:
    if not isinstance(config, dict) or not isinstance(config.get("inbounds"), list):
        raise ManagedSubscriptionError("Xray configuration must contain an inbound list")
    found: dict[str, dict] = {}
    for inbound in config["inbounds"]:
        if not isinstance(inbound, dict):
            continue
        tag = inbound.get("tag")
        if not isinstance(tag, str) or not tag:
            continue
        if tag in found:
            raise ManagedSubscriptionError("Duplicate Xray inbound tag")
        found[tag] = inbound
    return found


def validate_managed_inbound_baseline(config: dict, tags: list[str]) -> None:
    """Never replace manually managed or unsupported Xray inbound clients."""
    inbounds = _inbound_map(config)
    for tag in tags:
        inbound = inbounds.get(tag)
        if inbound is None or inbound.get("protocol") not in SUPPORTED_PROTOCOLS:
            raise ManagedSubscriptionError("Managed Xray inbound changed or was removed")
        settings = inbound.get("settings")
        if not isinstance(settings, dict) or settings.get("clients") not in (None, []):
            raise ManagedSubscriptionError("Managed Xray inbound must have an empty public client list")


def _subscription_client(protocol: str, subscription_uuid: uuid.UUID, settings: dict) -> dict | None:
    """Map stored credential to Xray's third-party client schema.

    Account.email is strictly a technical subscription UUID, not a user's
    contact email or an alternative WayLane identity.
    """
    field = settings.get(protocol)
    if not isinstance(field, dict):
        return None
    identity = str(subscription_uuid)
    if protocol in {"vless", "vmess"}:
        try:
            raw = field["id"]
            credential = str(uuid.UUID(str(raw)))
        except (ValueError, KeyError, TypeError, AttributeError):
            return None
        return {"id": credential, "email": identity, **({"alterId": 0} if protocol == "vmess" else {})}
    if protocol == "trojan":
        password = field.get("password")
        if isinstance(password, str) and 1 <= len(password) <= 256:
            return {"password": password, "email": identity}
    return None


async def ensure_assigned_editor_core_compatible(db: AsyncSession, core_id: int, config: dict) -> None:
    """Reject editor changes that would corrupt existing managed admission."""
    rows = (await db.execute(
        select(node_agent_xray_managed_inbounds.c.inbound_tag)
        .join(node_agent_xray_desired,
              node_agent_xray_managed_inbounds.c.node_uuid == node_agent_xray_desired.c.node_uuid)
        .where(node_agent_xray_desired.c.source_core_id == core_id)
        .distinct().limit(MAX_MANAGED_INBOUNDS * MAX_TARGET_NODES + 1)
    )).scalars().all()
    if len(rows) > MAX_MANAGED_INBOUNDS * MAX_TARGET_NODES:
        raise ManagedSubscriptionError("Too many managed inbound bindings for this Core")
    validate_managed_inbound_baseline(config, list(rows))


async def managed_inbound_tags(db: AsyncSession, node_uuid: str) -> list[str]:
    return list((await db.execute(
        select(node_agent_xray_managed_inbounds.c.inbound_tag)
        .where(node_agent_xray_managed_inbounds.c.node_uuid == node_uuid)
        .order_by(node_agent_xray_managed_inbounds.c.inbound_tag)
        .limit(MAX_MANAGED_INBOUNDS + 1)
    )).scalars())


async def inject_managed_subscriptions(db: AsyncSession, node_uuid: str, config: dict) -> dict:
    """Build an in-memory WSS payload. Never write the result into desired state."""
    tags = await managed_inbound_tags(db, node_uuid)
    if not tags:
        return config
    if len(tags) > MAX_MANAGED_INBOUNDS:
        raise ManagedSubscriptionError("Too many managed Xray inbounds")
    validate_managed_inbound_baseline(config, tags)
    result = copy.deepcopy(config)
    inbounds = _inbound_map(result)

    # Filter against current policy on every WSS generation, never stale
    # user objects. LEFT JOIN preserves admin-less subscriptions, while a
    # suspended admin suppresses access when its role requires disconnection.
    now = datetime.now(UTC)
    stmt = (
        select(ProxyInbound.tag, Subscription.uuid, Subscription.proxy_settings)
        .select_from(Subscription)
        .join(subscriptions_groups_association,
              subscriptions_groups_association.c.subscription_uuid == Subscription.uuid)
        .join(Group, and_(Group.id == subscriptions_groups_association.c.groups_id,
                          Group.is_disabled.is_(False)))
        .join(inbounds_groups_association,
              inbounds_groups_association.c.group_id == Group.id)
        .join(ProxyInbound, and_(ProxyInbound.id == inbounds_groups_association.c.inbound_id,
                                 ProxyInbound.tag.in_(tags)))
        .outerjoin(Admin, Admin.id == Subscription.admin_id)
        .outerjoin(AdminRole, AdminRole.id == Admin.role_id)
        .where(Subscription.entitlement == "active")
        .where(Subscription.status.in_([SubscriptionStatus.active, SubscriptionStatus.on_hold]))
        .where(or_(Subscription._expire.is_(None), Subscription._expire > now))
        .where(or_(Subscription.data_limit.is_(None), Subscription.data_limit <= 0,
                   Subscription.used_traffic < Subscription.data_limit))
        .where(or_(
            Admin.id.is_(None),
            Admin.status == AdminStatus.active,
            and_(Admin.status == AdminStatus.limited,
                 AdminRole.disconnect_users_when_limited.is_(False)),
            and_(Admin.status == AdminStatus.disabled,
                 AdminRole.disconnect_users_when_disabled.is_(False)),
        ))
        .distinct().order_by(ProxyInbound.tag, Subscription.uuid)
        .limit(MAX_MANAGED_INBOUNDS * MAX_USERS_PER_INBOUND + 1)
    )
    rows = (await db.execute(stmt)).all()
    if len(rows) > MAX_MANAGED_INBOUNDS * MAX_USERS_PER_INBOUND:
        raise ManagedSubscriptionError("Too many eligible subscription bindings")

    clients_by_tag: dict[str, list[dict]] = {tag: [] for tag in tags}
    credentials_by_tag: dict[str, set[str]] = {tag: set() for tag in tags}
    for tag, subscription_uuid, credentials in rows:
        protocol = inbounds[tag]["protocol"]
        if not isinstance(credentials, dict):
            continue
        client = _subscription_client(protocol, subscription_uuid, credentials)
        if client is None:
            continue  # Missing credentials never grant admission.
        credential_key = client.get("id") or client.get("password")
        if credential_key in credentials_by_tag[tag]:
            raise ManagedSubscriptionError("Duplicate Xray credential in managed inbound")
        credentials_by_tag[tag].add(credential_key)
        clients_by_tag[tag].append(client)
        if len(clients_by_tag[tag]) > MAX_USERS_PER_INBOUND:
            raise ManagedSubscriptionError("Too many subscriptions on one Xray inbound")

    for tag, clients in clients_by_tag.items():
        inbounds[tag]["settings"]["clients"] = clients
    if len(json.dumps(result, separators=(",", ":")).encode()) > MAX_XRAY_PAYLOAD_BYTES:
        raise ManagedSubscriptionError("Managed Xray payload exceeds the size limit")
    return result


async def enqueue_managed_subscription_changes(db: AsyncSession, *, commit: bool = True) -> int:
    """Queue fresh Xray generations for opted-in Nodes after subscription writes.

    This is event-driven, not polling. It never restarts intentionally stopped
    Cores. The WSS publisher rereads the *latest* entitlement before delivery.
    """
    # Most installations have no opt-in bindings yet. Avoid acquiring a write
    # transaction for every ordinary subscription update in that case.
    if await db.scalar(select(node_agent_xray_managed_inbounds.c.node_uuid).limit(1)) is None:
        return 0
    if commit:
        await begin_immediate_if_sqlite(db)
    rows = (await db.execute(
        select(node_agent_xray_desired)
        .join(node_agent_registrations,
              node_agent_xray_desired.c.node_uuid == node_agent_registrations.c.node_uuid)
        .where(node_agent_registrations.c.status == "approved")
        .where(node_agent_xray_desired.c.runtime_status.not_in(["stopped", "error"]))
        .where(node_agent_xray_desired.c.action != "stop")
        .where(select(node_agent_xray_managed_inbounds.c.node_uuid)
               .where(node_agent_xray_managed_inbounds.c.node_uuid == node_agent_xray_desired.c.node_uuid)
               .exists())
        .order_by(node_agent_xray_desired.c.node_uuid)
        .limit(MAX_TARGET_NODES + 1)
        .with_for_update()
    )).mappings().all()
    if len(rows) > MAX_TARGET_NODES:
        raise ManagedSubscriptionError("Too many Nodes for one subscription reconcile")
    for row in rows:
        # A still-pending deploy/update/restart is an explicit operator action.
        # Preserve it rather than replacing an image update with reconfigure.
        # Once any Xray generation has been applied, admission changes use
        # reconfigure even when a previous reconfigure is still pending.
        action = (
            row["action"]
            if row["runtime_status"] == "pending" and row["action"] in {"deploy", "update", "restart"}
            else "reconfigure" if row["applied_generation"] > 0 else "deploy"
        )
        result = await db.execute(
            update(node_agent_xray_desired)
            .where(node_agent_xray_desired.c.node_uuid == row["node_uuid"],
                   node_agent_xray_desired.c.generation == row["generation"])
            .values(
                generation=row["generation"] + 1,
                action=action,
                runtime_status="pending",
                last_error=None,
                updated_at=datetime.now(UTC),
            )
        )
        if result.rowcount != 1:
            await db.rollback()
            raise ManagedSubscriptionError("Concurrent subscription reconcile; retry with current state")
    if rows and commit:
        await db.commit()
    return len(rows)
