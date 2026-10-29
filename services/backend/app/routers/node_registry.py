"""Optional WayLane Node Agent enrollment. No production core/host changes are made."""
import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from ipaddress import IPv4Address, IPv6Address, ip_address
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import begin_immediate_if_sqlite, get_db
from app.db.models import node_agent_registrations, node_discovery_invitations, node_agent_xray_desired
from app.db.tls import agent_tls_bindings, tls_presets
from app.models.admin import AdminDetails
from .authentication import require_permission
from .xray_inbound_users import replace_inbound_users
from .node_xray_resources import (agent_tls_material, queue_xray_refresh, replace_routing, routing_from_editor, xray_tls_inbound)

node_control_router = APIRouter(prefix="/api/node-control", tags=["Node control"])
node_registry_router = APIRouter(prefix="/api/node-registry", tags=["Nodes"])


def digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def date(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def observed_ip(request: Request) -> str | None:
    # Deliberately ignore untrusted X-Forwarded-For.
    return request.client.host if request.client else None


class InvitationInput(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    lifetime_hours: int | None = Field(default=None, ge=1, le=8760)
    auto_approve: bool = True


class ClassicNodeInput(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)


@node_registry_router.post("/classic", status_code=201)
async def create_classic_node(body: ClassicNodeInput, db: AsyncSession = Depends(get_db),
                              _: AdminDetails = Depends(require_permission("nodes", "create"))):
    """Create an explicitly approved node with its own one-time agent token."""
    now = datetime.now(UTC)
    node_uuid = uuid4()
    # Before first telemetry, the real host-machine name is not yet known.
    # A temporary UUID-derived label is replaced on the first valid report.
    name = (body.display_name or "").strip() or f"node-{node_uuid}"
    raw = "wl_node_" + str(node_uuid) + "_" + secrets.token_urlsafe(32)
    await db.execute(insert(node_agent_registrations).values(
        node_uuid=str(node_uuid), secret_hash=digest(raw),
        invitation_id=None, display_name=name,
        agent_version="unknown", status="approved",
        capabilities=[], created_at=now, last_seen_at=None))
    await db.commit()
    return {"node_uuid": str(node_uuid), "node_token": raw, "status": "approved"}


class Enrollment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_uuid: UUID
    display_name: str = Field(min_length=1, max_length=128)
    node_secret: str = Field(min_length=43, max_length=128)
    node_version: str = Field(min_length=1, max_length=32)

    @field_validator("node_secret")
    @classmethod
    def secure_identity(cls, raw: str) -> str:
        try:
            decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        except Exception as exc:
            raise ValueError("invalid agent secret") from exc
        if len(decoded) < 32:
            raise ValueError("agent secret must contain 32 random bytes")
        return raw


class NodeMachineMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["agent-runtime"] = "agent-runtime"
    hostname: str = Field(min_length=1, max_length=128)
    os: str = Field(min_length=1, max_length=32)
    arch: str = Field(min_length=1, max_length=32)
    cpu_logical_count: int = Field(ge=1, le=4096)
    memory_total_bytes: int = Field(ge=0)
    memory_available_bytes: int = Field(ge=0)
    disk_total_bytes: int = Field(ge=0)
    disk_free_bytes: int = Field(ge=0)
    load_1: float = Field(ge=0, le=100000)
    uptime_seconds: float = Field(ge=0, le=1000000000)


class Heartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_uuid: UUID
    node_version: str = Field(min_length=1, max_length=32)
    reported_ipv4: str | None = None
    reported_ipv6: str | None = None
    capabilities: list[str] = Field(default_factory=list, max_length=32)
    machine_metrics: NodeMachineMetrics | None = None

    @field_validator("reported_ipv4", "reported_ipv6")
    @classmethod
    def literal_ip(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        try:
            parsed = ip_address(value)
        except ValueError as exc:
            raise ValueError("invalid IP address") from exc
        family = IPv4Address if info.field_name == "reported_ipv4" else IPv6Address
        if not isinstance(parsed, family) or not parsed.is_global:
            raise ValueError("must be a globally routable address of the correct family")
        return str(parsed)

    @field_validator("capabilities")
    @classmethod
    def valid_capabilities(cls, names: list[str]) -> list[str]:
        if len(set(names)) != len(names) or any(not n or len(n) > 64 for n in names):
            raise ValueError("invalid capabilities")
        return names


@node_registry_router.post("/discovery-tokens", status_code=201)
async def create_invitation(body: InvitationInput, db: AsyncSession = Depends(get_db),
                            _: AdminDetails = Depends(require_permission("nodes", "create"))):
    raw = "wl_disc_" + secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    expires = now + timedelta(hours=body.lifetime_hours) if body.lifetime_hours else None
    result = await db.execute(insert(node_discovery_invitations).values(
        token_hash=digest(raw), label=body.label, created_at=now, expires_at=expires,
        revoked="0", auto_approve="1" if body.auto_approve else "0"))
    await db.commit()
    # Token is returned exactly once to an authorized admin; only hash is stored.
    return {"id": result.inserted_primary_key[0], "discovery_token": raw,
            "expires_at": expires, "auto_approve": body.auto_approve}


@node_registry_router.get("/discovery-tokens")
async def list_invitations(db: AsyncSession = Depends(get_db),
                           _: AdminDetails = Depends(require_permission("nodes", "read"))):
    data = await db.execute(select(
        node_discovery_invitations.c.id, node_discovery_invitations.c.label,
        node_discovery_invitations.c.created_at, node_discovery_invitations.c.expires_at,
        node_discovery_invitations.c.revoked, node_discovery_invitations.c.auto_approve,
    ).order_by(node_discovery_invitations.c.id.desc()))
    return {"invitations": [dict(row) for row in data.mappings()]}


@node_registry_router.post("/discovery-tokens/{invitation_id}/revoke")
async def revoke_invitation(invitation_id: int, db: AsyncSession = Depends(get_db),
                            _: AdminDetails = Depends(require_permission("nodes", "update"))):
    result = await db.execute(update(node_discovery_invitations).where(
        node_discovery_invitations.c.id == invitation_id).values(revoked="1"))
    if not result.rowcount:
        raise HTTPException(404, "Unknown invitation")
    await db.commit()
    return {"revoked": True}


@node_registry_router.get("/nodes")
async def list_registrations(db: AsyncSession = Depends(get_db),
                             _: AdminDetails = Depends(require_permission("nodes", "read"))):
    columns = [
        node_agent_registrations.c.node_uuid, node_agent_registrations.c.display_name,
        node_agent_registrations.c.status, node_agent_registrations.c.agent_version,
        node_agent_registrations.c.legacy_node_id, node_agent_registrations.c.last_seen_at,
        node_agent_registrations.c.observed_ip, node_agent_registrations.c.reported_ipv4,
        node_agent_registrations.c.reported_ipv6, node_agent_registrations.c.capabilities,
        node_agent_registrations.c.machine_metrics,
        node_agent_registrations.c.invitation_id,
    ]
    rows = (await db.execute(select(*columns).order_by(node_agent_registrations.c.created_at.desc()))).mappings()
    result = []
    for row in rows:
        entry = dict(row)
        entry["connection_mode"] = "classic" if entry.pop("invitation_id") is None else "discovery"
        entry["node_version"] = entry.pop("agent_version")
        result.append(entry)
    return {"nodes": result}


@node_registry_router.post("/nodes/{node_uuid}/approve")
async def approve(node_uuid: UUID, db: AsyncSession = Depends(get_db),
                  _: AdminDetails = Depends(require_permission("nodes", "update"))):
    await begin_immediate_if_sqlite(db)
    row = (await db.execute(select(node_agent_registrations.c.status).where(
        node_agent_registrations.c.node_uuid == str(node_uuid)))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Unknown node identity")
    if row == "revoked":
        raise HTTPException(409, "Revoked node cannot be reapproved")
    await db.execute(update(node_agent_registrations).where(
        node_agent_registrations.c.node_uuid == str(node_uuid)).values(status="approved"))
    await db.commit()
    return {"status": "approved", "node_uuid": str(node_uuid)}


@node_registry_router.post("/nodes/{node_uuid}/revoke")
async def revoke(node_uuid: UUID, db: AsyncSession = Depends(get_db),
                 _: AdminDetails = Depends(require_permission("nodes", "update"))):
    result = await db.execute(update(node_agent_registrations).where(
        node_agent_registrations.c.node_uuid == str(node_uuid)).values(status="revoked"))
    if not result.rowcount:
        raise HTTPException(404, "Unknown node identity")
    await db.commit()
    return {"status": "revoked"}


@node_control_router.post("/enroll", status_code=201)
async def enroll(body: Enrollment, request: Request, x_discovery_token: str = Header(),
                 db: AsyncSession = Depends(get_db)):
    if not x_discovery_token.startswith("wl_disc_") or len(x_discovery_token) > 100:
        raise HTTPException(401, "Invalid invitation")
    await begin_immediate_if_sqlite(db)
    invitation = (await db.execute(select(node_discovery_invitations).where(
        node_discovery_invitations.c.token_hash == digest(x_discovery_token)
    ).with_for_update())).mappings().one_or_none()
    now = datetime.now(UTC)
    if not invitation or invitation["revoked"] != "0" or (
        invitation["expires_at"] is not None and date(invitation["expires_at"]) <= now
    ):
        raise HTTPException(401, "Invalid invitation")
    prior = (await db.execute(select(node_agent_registrations).where(
        node_agent_registrations.c.node_uuid == str(body.node_uuid)))).mappings().one_or_none()
    if prior is not None:
        if prior["invitation_id"] != invitation["id"] or not hmac.compare_digest(prior["secret_hash"], digest(body.node_secret)):
            raise HTTPException(409, "Identity already belongs to another registration")
        if prior["status"] == "revoked":
            raise HTTPException(403, "Node revoked")
        return {"node_uuid": str(body.node_uuid), "status": prior["status"]}
    registered = await db.scalar(select(func.count()).select_from(node_agent_registrations).where(
        node_agent_registrations.c.invitation_id == invitation["id"]))
    if registered is not None and registered >= 256:
        raise HTTPException(429, "Invitation enrollment limit reached")
    try:
        await db.execute(insert(node_agent_registrations).values(
            node_uuid=str(body.node_uuid), secret_hash=digest(body.node_secret),
            invitation_id=invitation["id"], display_name=body.display_name,
            agent_version=body.node_version,
            status="approved" if invitation["auto_approve"] == "1" else "pending",
            observed_ip=observed_ip(request), capabilities=[], created_at=now, last_seen_at=now))
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Registration collision") from exc
    return {"node_uuid": str(body.node_uuid),
            "status": "approved" if invitation["auto_approve"] == "1" else "pending"}


@node_control_router.post("/heartbeat")
async def heartbeat(body: Heartbeat, request: Request, x_node_secret: str = Header(),
                    db: AsyncSession = Depends(get_db)):
    if len(x_node_secret) > 128 or x_node_secret.startswith("wl_agent_"):
        raise HTTPException(401, "Invalid node credentials")
    row = (await db.execute(select(node_agent_registrations).where(
        node_agent_registrations.c.node_uuid == str(body.node_uuid)))).mappings().one_or_none()
    if (row is None or (row["invitation_id"] is None and not x_node_secret.startswith(f"wl_node_{body.node_uuid}_"))
            or not hmac.compare_digest(row["secret_hash"], digest(x_node_secret))):
        raise HTTPException(401, "Invalid node credentials")
    if row["status"] == "revoked":
        raise HTTPException(403, "Node revoked")
    # A transient IP lookup failure is NOT evidence that the prior public
    # address disappeared. Keep last-known values until a new valid IP arrives.
    changes = {
        "agent_version": body.node_version,
        "observed_ip": observed_ip(request),
        "capabilities": body.capabilities,
        "last_seen_at": datetime.now(UTC),
    }
    if body.machine_metrics is not None:
        changes["machine_metrics"] = body.machine_metrics.model_dump()
    if body.reported_ipv4 is not None:
        changes["reported_ipv4"] = body.reported_ipv4
    if body.reported_ipv6 is not None:
        changes["reported_ipv6"] = body.reported_ipv6
    await db.execute(update(node_agent_registrations).where(
        node_agent_registrations.c.node_uuid == str(body.node_uuid)).values(**changes))
    await db.commit()
    # No VPN core management or host-IP rewrite until acceptance.
    return {"status": row["status"], "allowed_operations": []}


# The desired-state protocol is intentionally independent of legacy PasarGuard
# nodes/cores. One monotonically increasing generation prevents replaying stale
# updates and retains the last desired state after Agent reconnection.
class XrayDesiredInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["deploy", "update", "restart", "stop", "reconfigure"]
    image_version: str | None = None
    config: dict[str, Any] | None = None
    region: str | None = Field(default=None, max_length=64)
    expected_generation: int | None = Field(default=None, ge=1)

    @field_validator("image_version")
    @classmethod
    def official_version(cls, value: str | None) -> str | None:
        import re
        if value is not None and not re.fullmatch(r"\d{2}\.\d{1,2}\.\d{1,2}", value):
            raise ValueError("Xray image version must be a pinned official release tag")
        return value

    @field_validator("config")
    @classmethod
    def xray_json(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        import json
        if value is not None and (len(json.dumps(value)) > 512_000
                                  or not isinstance(value.get("inbounds"), list)
                                  or not isinstance(value.get("outbounds"), list)):
            raise ValueError("Xray config must contain inbounds/outbounds and be <= 500 KiB")
        return value


class XrayReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_uuid: UUID
    generation: int = Field(ge=1)
    status: Literal["running", "stopped", "error", "requires_restart"]
    error: str | None = Field(default=None, max_length=300)


async def valid_agent(db: AsyncSession, node_uuid: UUID, agent_secret: str):
    if not agent_secret or len(agent_secret) > 128 or agent_secret.startswith("wl_agent_"):
        raise HTTPException(401, "Invalid node credential")
    row = (await db.execute(select(node_agent_registrations).where(
        node_agent_registrations.c.node_uuid == str(node_uuid)))).mappings().one_or_none()
    if (row is None or (row["invitation_id"] is None and not agent_secret.startswith(f"wl_node_{node_uuid}_"))
            or not hmac.compare_digest(row["secret_hash"], digest(agent_secret))):
        raise HTTPException(401, "Invalid node credential")
    if row["status"] != "approved":
        raise HTTPException(403, "Node not approved")
    return row


@node_registry_router.put("/nodes/{node_uuid}/xray")
async def set_desired_xray(
    node_uuid: UUID, body: XrayDesiredInput, db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("nodes", "update_core")),
):
    await begin_immediate_if_sqlite(db)
    node = (await db.execute(select(node_agent_registrations.c.status).where(
        node_agent_registrations.c.node_uuid == str(node_uuid)))).scalar_one_or_none()
    if node != "approved":
        raise HTTPException(409, "Only approved Nodes accept Xray changes")
    previous = (await db.execute(select(node_agent_xray_desired).where(
        node_agent_xray_desired.c.node_uuid == str(node_uuid)))).mappings().one_or_none()
    if body.action == "reconfigure":
        if not previous or body.expected_generation is None or body.expected_generation != previous["generation"]:
            raise HTTPException(409, "Reload current Agent generation before a live change")
        if previous["runtime_status"] == "pending":
            raise HTTPException(409, "An Xray operation is already pending")
    config = body.config if body.config is not None else (previous["config"] if previous else None)
    if body.action in {"deploy", "update", "reconfigure"} and not config:
        raise HTTPException(422, "Xray configuration required on first deployment")
    if body.action in {"restart", "stop", "reconfigure"} and previous is None:
        raise HTTPException(409, "Xray has not been configured")
    version = body.image_version or (previous["image_version"] if previous else "26.10.21")
    generation = int(previous["generation"]) + 1 if previous else 1
    region = body.region if body.region is not None else (previous["region"] if previous else None)
    now = datetime.now(UTC)
    values = dict(generation=generation, action=body.action, image_version=version,
                  config=config, region=region, runtime_status="pending",
                  last_error=None, updated_at=now)
    if previous:
        await db.execute(update(node_agent_xray_desired).where(
            node_agent_xray_desired.c.node_uuid == str(node_uuid)).values(**values))
    else:
        await db.execute(insert(node_agent_xray_desired).values(node_uuid=str(node_uuid), **values))
    await db.commit()
    return {"generation": generation, "status": "pending", "action": body.action,
            "image_version": version, "region": region}


class EditorRoutingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    core_id: int = Field(gt=0)
    expected_generation: int = Field(ge=1)


class AgentTLSBindingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preset_id: UUID
    expected_generation: int = Field(ge=1)


async def _mutable_xray(db: AsyncSession, node_uuid: UUID, expected_generation: int):
    node = await db.scalar(select(node_agent_registrations.c.status).where(
        node_agent_registrations.c.node_uuid == str(node_uuid)))
    if node != "approved":
        raise HTTPException(409, "Only approved Agents can change Xray resources")
    desired = (await db.execute(select(node_agent_xray_desired).where(
        node_agent_xray_desired.c.node_uuid == str(node_uuid)))).mappings().one_or_none()
    if not desired or not isinstance(desired["config"], dict):
        raise HTTPException(409, "Xray must be configured first")
    if desired["generation"] != expected_generation or desired["runtime_status"] == "pending":
        raise HTTPException(409, "Xray state is stale or a change is pending")
    if desired["runtime_status"] == "stopped":
        raise HTTPException(409, "Xray is stopped; explicitly start it before applying resources")
    return desired


@node_registry_router.put("/nodes/{node_uuid}/xray/routing/from-core")
async def sync_editor_routing(
    node_uuid: UUID, body: EditorRoutingInput,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("nodes", "update_core")),
):
    """Reuse saved Xray Core Editor routing, including balancers/rules."""
    await begin_immediate_if_sqlite(db)
    desired = await _mutable_xray(db, node_uuid, body.expected_generation)
    try:
        routing = await routing_from_editor(db, body.core_id)
        config = replace_routing(desired["config"], routing)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    await db.execute(update(node_agent_xray_desired).where(
        node_agent_xray_desired.c.node_uuid == str(node_uuid)).values(
        config=config, generation=desired["generation"] + 1, action="reconfigure",
        last_error=None, runtime_status="pending", updated_at=datetime.now(UTC)))
    await db.commit()
    return {"generation": desired["generation"] + 1, "status": "pending",
            "core_id": body.core_id, "rule_count": len(routing["rules"])}


@node_registry_router.get("/nodes/{node_uuid}/xray/tls")
async def list_agent_tls_bindings(
    node_uuid: UUID, db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("nodes", "read")),
):
    rows = (await db.execute(select(
        agent_tls_bindings.c.inbound_tag, agent_tls_bindings.c.preset_id,
        tls_presets.c.name, tls_presets.c.revision, tls_presets.c.fingerprint_sha256,
        tls_presets.c.not_after,
    ).select_from(agent_tls_bindings.join(
        tls_presets, agent_tls_bindings.c.preset_id == tls_presets.c.id
    )).where(agent_tls_bindings.c.node_uuid == str(node_uuid)))).all()
    return {"bindings": [dict(zip(("inbound_tag", "preset_id", "preset_name",
                                   "revision", "fingerprint_sha256", "not_after"), row)) for row in rows]}


@node_registry_router.put("/nodes/{node_uuid}/xray/tls/{inbound_tag}")
async def bind_agent_tls(
    node_uuid: UUID, inbound_tag: str, body: AgentTLSBindingInput,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("cores", "update")),
):
    await begin_immediate_if_sqlite(db)
    desired = await _mutable_xray(db, node_uuid, body.expected_generation)
    if len(inbound_tag) > 256 or not xray_tls_inbound(desired["config"], inbound_tag):
        raise HTTPException(422, "Inbound must exist and use TLS")
    preset = await db.scalar(select(tls_presets.c.id).where(
        tls_presets.c.id == str(body.preset_id)))
    if not preset:
        raise HTTPException(404, "TLS preset not found")
    prior = await db.scalar(select(agent_tls_bindings.c.preset_id).where(
        agent_tls_bindings.c.node_uuid == str(node_uuid),
        agent_tls_bindings.c.inbound_tag == inbound_tag))
    if prior:
        await db.execute(update(agent_tls_bindings).where(
            agent_tls_bindings.c.node_uuid == str(node_uuid),
            agent_tls_bindings.c.inbound_tag == inbound_tag).values(preset_id=str(body.preset_id)))
    else:
        await db.execute(insert(agent_tls_bindings).values(
            node_uuid=str(node_uuid), inbound_tag=inbound_tag, preset_id=str(body.preset_id)))
    await queue_xray_refresh(db, str(node_uuid))
    await db.commit()
    return {"inbound_tag": inbound_tag, "preset_id": str(body.preset_id),
            "generation": desired["generation"] + 1, "status": "pending"}


@node_registry_router.delete("/nodes/{node_uuid}/xray/tls/{inbound_tag}")
async def unbind_agent_tls(
    node_uuid: UUID, inbound_tag: str, expected_generation: int,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("cores", "update")),
):
    await begin_immediate_if_sqlite(db)
    desired = await _mutable_xray(db, node_uuid, expected_generation)
    deleted = await db.execute(delete(agent_tls_bindings).where(
        agent_tls_bindings.c.node_uuid == str(node_uuid),
        agent_tls_bindings.c.inbound_tag == inbound_tag))
    if deleted.rowcount != 1:
        raise HTTPException(404, "Agent TLS binding not found")
    await queue_xray_refresh(db, str(node_uuid))
    await db.commit()
    return {"removed": inbound_tag, "generation": desired["generation"] + 1}


@node_registry_router.get("/nodes/{node_uuid}/xray/inbounds")
async def list_xray_inbounds(
    node_uuid: UUID, db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("nodes", "read")),
):
    """Return sanitized inbound account identities, never raw user secrets."""
    entry = (await db.execute(select(node_agent_xray_desired.c.config).where(
        node_agent_xray_desired.c.node_uuid == str(node_uuid)))).scalar_one_or_none()
    if not isinstance(entry, dict):
        return {"inbounds": []}
    result = []
    for inbound in entry.get("inbounds", []):
        if not isinstance(inbound, dict):
            continue
        protocol = inbound.get("protocol")
        settings = inbound.get("settings") or {}
        users = settings.get("accounts" if protocol == "socks" else "clients", [])
        if not isinstance(users, list):
            users = []
        identities = [
            user.get("user" if protocol == "socks" else "email") or (
                user.get("id") if protocol in {"vless", "vmess"} else None)
            for user in users if isinstance(user, dict)
        ]
        result.append({"tag": inbound.get("tag"), "protocol": protocol,
                       "port": inbound.get("port"), "user_count": len(users),
                       "identities": [name for name in identities if isinstance(name, str)]})
    return {"inbounds": result}


class XrayInboundUsersInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    users: list[dict[str, Any]] = Field(max_length=1024)
    expected_generation: int = Field(ge=1)


@node_registry_router.put("/nodes/{node_uuid}/xray/inbounds/{inbound_tag}/users")
async def sync_xray_inbound_users(
    node_uuid: UUID, inbound_tag: str, body: XrayInboundUsersInput,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("nodes", "update_core")),
):
    """Replace one inbound's accounts, preserving the remaining Xray config.

    A single desired-state generation is queued. The Agent validates the
    entire resulting config and preserves the last running core on failure.
    """
    await begin_immediate_if_sqlite(db)
    node_status = await db.scalar(select(node_agent_registrations.c.status).where(
        node_agent_registrations.c.node_uuid == str(node_uuid)))
    if node_status != "approved":
        raise HTTPException(409, "Only approved Agents can synchronize Xray users")
    previous = (await db.execute(select(node_agent_xray_desired).where(
        node_agent_xray_desired.c.node_uuid == str(node_uuid)))).mappings().one_or_none()
    if previous is None or not previous["config"]:
        raise HTTPException(409, "Xray must be deployed before synchronizing users")
    if previous["generation"] != body.expected_generation:
        raise HTTPException(409, "Stale Xray generation; reload status")
    if previous["runtime_status"] == "pending":
        raise HTTPException(409, "Previous Xray operation still pending")
    try:
        config = replace_inbound_users(previous["config"], inbound_tag, body.users)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    generation = int(previous["generation"]) + 1
    await db.execute(update(node_agent_xray_desired).where(
        node_agent_xray_desired.c.node_uuid == str(node_uuid)).values(
            generation=generation, action="reconfigure", config=config,
            runtime_status="pending", last_error=None, updated_at=datetime.now(UTC)))
    await db.commit()
    return {"generation": generation, "status": "pending",
            "inbound_tag": inbound_tag, "user_count": len(body.users)}


@node_registry_router.get("/nodes/{node_uuid}/xray")
async def get_desired_xray(
    node_uuid: UUID, db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("nodes", "read")),
):
    entry = (await db.execute(select(node_agent_xray_desired).where(
        node_agent_xray_desired.c.node_uuid == str(node_uuid)))).mappings().one_or_none()
    if entry is None:
        return {"configured": False}
    return {"configured": True, "generation": entry["generation"], "action": entry["action"],
            "image_version": entry["image_version"], "region": entry["region"],
            "applied_generation": entry["applied_generation"],
            "runtime_status": entry["runtime_status"], "last_error": entry["last_error"],
            "reported_at": entry["reported_at"]}


async def agent_xray_desired(
    node_uuid: UUID, x_node_secret: str = Header(),
    db: AsyncSession = Depends(get_db),
):
    await valid_agent(db, node_uuid, x_node_secret)
    entry = (await db.execute(select(node_agent_xray_desired).where(
        node_agent_xray_desired.c.node_uuid == str(node_uuid)))).mappings().one_or_none()
    if entry is None:
        return {"configured": False}
    # Only an authenticated Agent receives the raw Xray config.
    return {"configured": True, "generation": entry["generation"], "action": entry["action"],
            "image_version": entry["image_version"], "config": entry["config"],
            "region": entry["region"], "applied_generation": entry["applied_generation"],
            "tls_certificates": await agent_tls_material(db, str(node_uuid))}


async def agent_xray_report(
    body: XrayReport, x_node_secret: str = Header(),
    db: AsyncSession = Depends(get_db),
):
    await valid_agent(db, body.node_uuid, x_node_secret)
    await begin_immediate_if_sqlite(db)
    entry = (await db.execute(select(node_agent_xray_desired).where(
        node_agent_xray_desired.c.node_uuid == str(body.node_uuid)))).mappings().one_or_none()
    if entry is None or entry["generation"] != body.generation:
        raise HTTPException(409, "Stale Xray desired generation")
    now = datetime.now(UTC)
    await db.execute(update(node_agent_xray_desired).where(
        node_agent_xray_desired.c.node_uuid == str(body.node_uuid)).values(
            applied_generation=body.generation if body.status != "error" else entry["applied_generation"],
            runtime_status=body.status, last_error=body.error if body.status == "error" else None,
            reported_at=now))
    await db.commit()
    return {"accepted": True, "generation": body.generation}
