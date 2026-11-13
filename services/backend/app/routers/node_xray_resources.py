"""Existing core-editor routing and encrypted TLS presets -> Agent desired-state."""

import copy
import json
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import begin_immediate_if_sqlite
from app.db.models import CoreConfig, CoreType, node_agent_registrations, node_agent_xray_desired
from app.db.tls import agent_tls_bindings, tls_presets
from app.operation.core_presets import hydrate_core_preset_config
from app.operation.tls_presets import decrypt_private_key


def xray_tls_inbound(config: dict, tag: str) -> bool:
    return any(isinstance(item, dict) and item.get("tag") == tag
               and (item.get("streamSettings") or {}).get("security") == "tls"
               for item in config.get("inbounds", []))


def replace_routing(config: dict, routing: dict) -> dict:
    """Same Xray JSON routing format persisted by the WayLane Core Editor."""
    if not isinstance(routing, dict):
        raise ValueError("Routing must be an Xray routing object")
    rules = routing.get("rules")
    if not isinstance(rules, list) or len(rules) > 2048:
        raise ValueError("Routing rules must be a bounded list")
    if len(json.dumps(routing, ensure_ascii=False)) > 256000:
        raise ValueError("Routing rules are too large")
    result = copy.deepcopy(config)
    if not isinstance(result.get("inbounds"), list) or not isinstance(result.get("outbounds"), list):
        raise ValueError("Xray config is missing inbounds/outbounds")
    result["routing"] = copy.deepcopy(routing)
    if len(json.dumps(result, ensure_ascii=False)) > 512000:
        raise ValueError("Xray config exceeds supported limit")
    return result


async def routing_from_editor(db: AsyncSession, core_id: int) -> dict:
    """Read the existing editor's CoreConfig, not a parallel rule registry."""
    core = await db.get(CoreConfig, core_id)
    if core is None or getattr(core, "type", None) != "xray":
        raise ValueError("Xray Core Editor configuration not found")
    source = json.loads(core.config) if isinstance(core.config, str) else core.config
    if not isinstance(source, dict) or not isinstance(source.get("routing"), dict):
        raise ValueError("Saved Xray core has no routing configuration")
    return source["routing"]


def validate_local_proxy_exposure(config: dict) -> None:
    """Do not accidentally create a public unauthenticated HTTP/SOCKS proxy.

    This validates only the exposure boundary; Xray remains responsible for
    validating the complete protocol-specific configuration on deploy.
    """
    for inbound in config.get("inbounds", []):
        if not isinstance(inbound, dict):
            continue
        protocol = inbound.get("protocol")
        if protocol not in {"http", "socks", "mixed"}:
            continue
        listen = str(inbound.get("listen") or "0.0.0.0").strip().lower()
        if listen in {"127.0.0.1", "::1", "localhost"}:
            continue
        settings = inbound.get("settings") or {}
        if not isinstance(settings, dict):
            raise ValueError("Public HTTP/SOCKS proxy settings must be an object")
        accounts = settings.get("accounts")
        protected = (
            protocol != "mixed"
            and isinstance(accounts, list)
            and len(accounts) > 0
            and all(isinstance(item, dict)
                    and isinstance(item.get("user"), str) and item["user"]
                    and isinstance(item.get("pass"), str) and item["pass"]
                    for item in accounts)
        )
        if protocol == "socks":
            protected = protected and settings.get("auth") == "password"
        if not protected:
            raise ValueError(
                "Refusing public unauthenticated HTTP/SOCKS proxy; bind to loopback "
                "or configure explicit per-user authentication"
            )


async def editor_core_json(db: AsyncSession, core_id: int) -> tuple[CoreConfig, dict]:
    """Read a saved Xray editor profile without exposing its managed secrets."""
    core = await db.get(CoreConfig, core_id)
    if core is None or core.type != CoreType.xray:
        raise ValueError("Saved Xray Core Editor profile not found")
    source = json.loads(core.config) if isinstance(core.config, str) else core.config
    if (not isinstance(source, dict) or
            not isinstance(source.get("inbounds"), list) or
            not isinstance(source.get("outbounds"), list) or
            len(json.dumps(source, ensure_ascii=False).encode()) > 512_000):
        raise ValueError("Saved Xray Core Editor profile must contain valid inbounds/outbounds <= 500 KiB")
    validate_local_proxy_exposure(source)
    return core, copy.deepcopy(source)


async def node_facing_editor_core_json(db: AsyncSession, core_id: int, public_config: dict) -> dict:
    """Hydrate bound secrets only while preparing the authenticated Node payload.

    Desired state persists the public Core Editor projection, never decrypted
    REALITY or VLESS preset keys. The returned object must not be logged.
    """
    hydrated = await hydrate_core_preset_config(db, json.dumps(public_config), core_id)
    config = json.loads(hydrated)
    if len(json.dumps(config, ensure_ascii=False).encode()) > 512_000:
        raise ValueError("Hydrated Xray Core Editor profile exceeds the control limit")
    return config


async def queue_assigned_editor_cores(
    db: AsyncSession, core_id: int, *, restart_only: bool = False, live_update: bool = False
) -> int:
    """Dispatch a saved Xray profile to its explicitly assigned autonomous Nodes.

    A Core Editor save with restart_nodes=true queues the new public snapshot;
    an explicit Core restart reuses the already-applied snapshot instead.
    Routing/outbound-only edits use live reconfigure, not a disruptive deploy.
    Deliberately stopped Nodes are never implicitly started by an edit.
    """
    await begin_immediate_if_sqlite(db)
    rows = (
        (await db.execute(
            select(node_agent_xray_desired)
            .join(node_agent_registrations,
                  node_agent_xray_desired.c.node_uuid == node_agent_registrations.c.node_uuid)
            .where(node_agent_xray_desired.c.source_core_id == core_id,
                   node_agent_registrations.c.status == "approved")
            .order_by(node_agent_xray_desired.c.node_uuid)
            .limit(257)
            .with_for_update()
        )).mappings().all()
    )
    if len(rows) > 256:
        raise ValueError("Too many Nodes are assigned to the same saved Core profile")
    applicable = [row for row in rows if row["runtime_status"] != "stopped"]
    if not applicable:
        return 0
    public_config = None
    if not restart_only:
        _, public_config = await editor_core_json(db, core_id)
        # Validate all managed references before accepting the new generation.
        await node_facing_editor_core_json(db, core_id, public_config)
    for row in applicable:
        live_reconfigure = (live_update and row["runtime_status"] == "running"
                            and int(row["applied_generation"] or 0) > 0)
        values = {
            "generation": int(row["generation"]) + 1,
            "action": "restart" if restart_only else "reconfigure" if live_reconfigure else "deploy",
            "runtime_status": "pending",
            "last_error": None,
            "updated_at": datetime.now(UTC),
        }
        if not restart_only:
            values["config"] = copy.deepcopy(public_config)
        result = await db.execute(
            update(node_agent_xray_desired)
            .where(node_agent_xray_desired.c.node_uuid == row["node_uuid"],
                   node_agent_xray_desired.c.generation == row["generation"])
            .values(**values)
        )
        if result.rowcount != 1:
            await db.rollback()
            raise ValueError("Node generation changed while saving the Core profile")
    await db.commit()
    return len(applicable)


async def agent_tls_material(db: AsyncSession, uuid: str) -> list[dict]:
    """Private certificates go ONLY to the authenticated Agent endpoint."""
    rows = (await db.execute(select(
        agent_tls_bindings.c.inbound_tag,
        tls_presets.c.revision, tls_presets.c.certificate_pem,
        tls_presets.c.encrypted_private_key,
    ).select_from(agent_tls_bindings.join(
        tls_presets, agent_tls_bindings.c.preset_id == tls_presets.c.id
    )).where(agent_tls_bindings.c.node_uuid == uuid))).all()
    # Limit the secret response. A misconfigured large certificate registry
    # must fail closed rather than overflow the Agent response decoder.
    if len(rows) > 16 or sum(len(cert) + len(cipher) for _, _, cert, cipher in rows) > 1_000_000:
        raise ValueError("Too many or oversized TLS certificates for a single Agent")
    return [{"inbound_tag": tag, "revision": revision,
             "certificate": certificate, "private_key": decrypt_private_key(ciphertext)}
            for tag, revision, certificate, ciphertext in rows]


async def queue_xray_refresh(db: AsyncSession, uuid: str) -> bool:
    """Schedule a new generation without altering user-managed Xray JSON."""
    entry = (await db.execute(select(node_agent_xray_desired).where(
        node_agent_xray_desired.c.node_uuid == uuid))).mappings().one_or_none()
    if not entry or not entry["config"] or entry["runtime_status"] == "stopped":
        # Never reactivate an intentionally stopped Xray during TLS rotation.
        # The next explicit deploy/update will reconcile its active bindings.
        return False
    from datetime import UTC, datetime
    await db.execute(update(node_agent_xray_desired).where(
        node_agent_xray_desired.c.node_uuid == uuid).values(
            generation=entry["generation"] + 1,
            action="reconfigure", runtime_status="pending",
            last_error=None, updated_at=datetime.now(UTC)))
    return True
