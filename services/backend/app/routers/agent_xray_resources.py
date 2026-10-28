"""Existing core-editor routing and encrypted TLS presets -> Agent desired-state."""

import copy
import json

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CoreConfig, node_agent_xray_desired
from app.db.tls import agent_tls_bindings, tls_presets
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
