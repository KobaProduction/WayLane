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
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import begin_immediate_if_sqlite, get_db
from app.db.models import node_agent_registrations, node_discovery_invitations, node_agent_xray_desired
from app.models.admin import AdminDetails
from .authentication import require_permission

router = APIRouter(prefix="/api/node-discovery", tags=["Node discovery"])
agent_router = APIRouter(prefix="/api/agent", tags=["Node agent"])


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


class DirectAgentInput(BaseModel):
    display_name: str = Field(min_length=1, max_length=128)


@router.post("/registrations/direct", status_code=201)
async def create_direct_agent(body: DirectAgentInput, db: AsyncSession = Depends(get_db),
                              _: AdminDetails = Depends(require_permission("nodes", "create"))):
    """Create an explicitly approved node with its own one-time agent token."""
    now = datetime.now(UTC)
    node_uuid = uuid4()
    raw = "wl_agent_" + str(node_uuid) + "_" + secrets.token_urlsafe(32)
    await db.execute(insert(node_agent_registrations).values(
        node_uuid=str(node_uuid), secret_hash=digest(raw),
        invitation_id=None, display_name=body.display_name,
        agent_version="unknown", status="approved",
        capabilities=[], created_at=now, last_seen_at=None))
    await db.commit()
    return {"node_uuid": str(node_uuid), "agent_token": raw, "status": "approved"}


class Enrollment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_uuid: UUID
    display_name: str = Field(min_length=1, max_length=128)
    agent_secret: str = Field(min_length=43, max_length=128)
    agent_version: str = Field(min_length=1, max_length=32)

    @field_validator("agent_secret")
    @classmethod
    def secure_identity(cls, raw: str) -> str:
        try:
            decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        except Exception as exc:
            raise ValueError("invalid agent secret") from exc
        if len(decoded) < 32:
            raise ValueError("agent secret must contain 32 random bytes")
        return raw


class Heartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_uuid: UUID
    agent_version: str = Field(min_length=1, max_length=32)
    reported_ipv4: str | None = None
    reported_ipv6: str | None = None
    capabilities: list[str] = Field(default_factory=list, max_length=32)

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


@router.post("/invitations", status_code=201)
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


@router.get("/invitations")
async def list_invitations(db: AsyncSession = Depends(get_db),
                           _: AdminDetails = Depends(require_permission("nodes", "read"))):
    data = await db.execute(select(
        node_discovery_invitations.c.id, node_discovery_invitations.c.label,
        node_discovery_invitations.c.created_at, node_discovery_invitations.c.expires_at,
        node_discovery_invitations.c.revoked, node_discovery_invitations.c.auto_approve,
    ).order_by(node_discovery_invitations.c.id.desc()))
    return {"invitations": [dict(row) for row in data.mappings()]}


@router.post("/invitations/{invitation_id}/revoke")
async def revoke_invitation(invitation_id: int, db: AsyncSession = Depends(get_db),
                            _: AdminDetails = Depends(require_permission("nodes", "update"))):
    result = await db.execute(update(node_discovery_invitations).where(
        node_discovery_invitations.c.id == invitation_id).values(revoked="1"))
    if not result.rowcount:
        raise HTTPException(404, "Unknown invitation")
    await db.commit()
    return {"revoked": True}


@router.get("/registrations")
async def list_registrations(db: AsyncSession = Depends(get_db),
                             _: AdminDetails = Depends(require_permission("nodes", "read"))):
    columns = [
        node_agent_registrations.c.node_uuid, node_agent_registrations.c.display_name,
        node_agent_registrations.c.status, node_agent_registrations.c.agent_version,
        node_agent_registrations.c.legacy_node_id, node_agent_registrations.c.last_seen_at,
        node_agent_registrations.c.observed_ip, node_agent_registrations.c.reported_ipv4,
        node_agent_registrations.c.reported_ipv6, node_agent_registrations.c.capabilities,
        node_agent_registrations.c.invitation_id,
    ]
    rows = (await db.execute(select(*columns).order_by(node_agent_registrations.c.created_at.desc()))).mappings()
    result = []
    for row in rows:
        entry = dict(row)
        entry["enrollment_mode"] = "direct" if entry.pop("invitation_id") is None else "discovery"
        result.append(entry)
    return {"registrations": result}


@router.post("/registrations/{node_uuid}/approve")
async def approve(node_uuid: UUID, db: AsyncSession = Depends(get_db),
                  _: AdminDetails = Depends(require_permission("nodes", "update"))):
    await begin_immediate_if_sqlite(db)
    row = (await db.execute(select(node_agent_registrations.c.status).where(
        node_agent_registrations.c.node_uuid == str(node_uuid)))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Unknown agent identity")
    if row == "revoked":
        raise HTTPException(409, "Revoked agent cannot be reapproved")
    await db.execute(update(node_agent_registrations).where(
        node_agent_registrations.c.node_uuid == str(node_uuid)).values(status="approved"))
    await db.commit()
    return {"status": "approved", "node_uuid": str(node_uuid)}


@router.post("/registrations/{node_uuid}/revoke")
async def revoke(node_uuid: UUID, db: AsyncSession = Depends(get_db),
                 _: AdminDetails = Depends(require_permission("nodes", "update"))):
    result = await db.execute(update(node_agent_registrations).where(
        node_agent_registrations.c.node_uuid == str(node_uuid)).values(status="revoked"))
    if not result.rowcount:
        raise HTTPException(404, "Unknown agent identity")
    await db.commit()
    return {"status": "revoked"}


@agent_router.post("/enroll", status_code=201)
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
        if prior["invitation_id"] != invitation["id"] or not hmac.compare_digest(prior["secret_hash"], digest(body.agent_secret)):
            raise HTTPException(409, "Identity already belongs to another registration")
        if prior["status"] == "revoked":
            raise HTTPException(403, "Agent revoked")
        return {"node_uuid": str(body.node_uuid), "status": prior["status"]}
    registered = await db.scalar(select(func.count()).select_from(node_agent_registrations).where(
        node_agent_registrations.c.invitation_id == invitation["id"]))
    if registered is not None and registered >= 256:
        raise HTTPException(429, "Invitation enrollment limit reached")
    try:
        await db.execute(insert(node_agent_registrations).values(
            node_uuid=str(body.node_uuid), secret_hash=digest(body.agent_secret),
            invitation_id=invitation["id"], display_name=body.display_name,
            agent_version=body.agent_version,
            status="approved" if invitation["auto_approve"] == "1" else "pending",
            observed_ip=observed_ip(request), capabilities=[], created_at=now, last_seen_at=now))
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Registration collision") from exc
    return {"node_uuid": str(body.node_uuid),
            "status": "approved" if invitation["auto_approve"] == "1" else "pending"}


@agent_router.post("/heartbeat")
async def heartbeat(body: Heartbeat, request: Request, x_agent_secret: str = Header(),
                    db: AsyncSession = Depends(get_db)):
    if len(x_agent_secret) > 128:
        raise HTTPException(401, "Invalid agent credentials")
    row = (await db.execute(select(node_agent_registrations).where(
        node_agent_registrations.c.node_uuid == str(body.node_uuid)))).mappings().one_or_none()
    if row is None or not hmac.compare_digest(row["secret_hash"], digest(x_agent_secret)):
        raise HTTPException(401, "Invalid agent credentials")
    if row["status"] == "revoked":
        raise HTTPException(403, "Agent revoked")
    # A transient IP lookup failure is NOT evidence that the prior public
    # address disappeared. Keep last-known values until a new valid IP arrives.
    changes = {
        "agent_version": body.agent_version,
        "observed_ip": observed_ip(request),
        "capabilities": body.capabilities,
        "last_seen_at": datetime.now(UTC),
    }
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
    action: Literal["deploy", "update", "restart", "stop"]
    image_version: str | None = None
    config: dict[str, Any] | None = None
    region: str | None = Field(default=None, max_length=64)

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
    status: Literal["running", "stopped", "error"]
    error: str | None = Field(default=None, max_length=300)


async def valid_agent(db: AsyncSession, node_uuid: UUID, agent_secret: str):
    if not agent_secret or len(agent_secret) > 128:
        raise HTTPException(401, "Invalid Agent credential")
    row = (await db.execute(select(node_agent_registrations).where(
        node_agent_registrations.c.node_uuid == str(node_uuid)))).mappings().one_or_none()
    if row is None or not hmac.compare_digest(row["secret_hash"], digest(agent_secret)):
        raise HTTPException(401, "Invalid Agent credential")
    if row["status"] != "approved":
        raise HTTPException(403, "Agent not approved")
    return row


@router.put("/registrations/{node_uuid}/xray")
async def set_desired_xray(
    node_uuid: UUID, body: XrayDesiredInput, db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("nodes", "update_core")),
):
    await begin_immediate_if_sqlite(db)
    node = (await db.execute(select(node_agent_registrations.c.status).where(
        node_agent_registrations.c.node_uuid == str(node_uuid)))).scalar_one_or_none()
    if node != "approved":
        raise HTTPException(409, "Only approved autonomous Agents accept Xray changes")
    previous = (await db.execute(select(node_agent_xray_desired).where(
        node_agent_xray_desired.c.node_uuid == str(node_uuid)))).mappings().one_or_none()
    config = body.config if body.config is not None else (previous["config"] if previous else None)
    if body.action in {"deploy", "update"} and not config:
        raise HTTPException(422, "Xray configuration required on first deployment")
    if body.action in {"restart", "stop"} and previous is None:
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


@router.get("/registrations/{node_uuid}/xray")
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


@agent_router.get("/xray/desired")
async def agent_xray_desired(
    node_uuid: UUID, x_agent_secret: str = Header(),
    db: AsyncSession = Depends(get_db),
):
    await valid_agent(db, node_uuid, x_agent_secret)
    entry = (await db.execute(select(node_agent_xray_desired).where(
        node_agent_xray_desired.c.node_uuid == str(node_uuid)))).mappings().one_or_none()
    if entry is None:
        return {"configured": False}
    # Only an authenticated Agent receives the raw Xray config.
    return {"configured": True, "generation": entry["generation"], "action": entry["action"],
            "image_version": entry["image_version"], "config": entry["config"],
            "region": entry["region"], "applied_generation": entry["applied_generation"]}


@agent_router.post("/xray/report")
async def agent_xray_report(
    body: XrayReport, x_agent_secret: str = Header(),
    db: AsyncSession = Depends(get_db),
):
    await valid_agent(db, body.node_uuid, x_agent_secret)
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
