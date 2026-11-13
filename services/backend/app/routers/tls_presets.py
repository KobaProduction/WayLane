"""TLS certificate presets, scoped webhook rotation and explicit node bindings."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.db.tls import agent_tls_bindings, tls_presets
from app.models.admin import AdminDetails
from app.operation.tls_presets import (
    TlsPresetError,
    encrypt_private_key,
    generate_service_token,
    list_bindings,
    service_token_matches,
    token_digest,
    validate_certificate_pair,
)

from .authentication import require_permission
from .node_xray_resources import queue_xray_refresh

router = APIRouter(tags=["TLS Presets"], prefix="/api/tls-presets")


class CertificateInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    certificate: str = Field(min_length=30, max_length=65536)
    private_key: str = Field(min_length=30, max_length=16384)


class CertificateRotation(BaseModel):
    certificate: str = Field(min_length=30, max_length=65536)
    private_key: str = Field(min_length=30, max_length=16384)


class PresetRename(BaseModel):
    name: str = Field(min_length=1, max_length=120)


def _summary(row) -> dict:
    d = dict(row)
    return {
        k: d[k]
        for k in (
            "id",
            "name",
            "certificate_pem",
            "fingerprint_sha256",
            "subject",
            "not_after",
            "revision",
            "updated_at",
        )
    }


async def _preset(db: AsyncSession, preset_id: str):
    row = (await db.execute(select(tls_presets).where(tls_presets.c.id == preset_id))).mappings().first()
    if not row:
        raise HTTPException(404, "TLS preset not found")
    return row


def _validated_pair(certificate: str, private_key: str):
    try:
        info = validate_certificate_pair(certificate, private_key)
        cipher = encrypt_private_key(private_key)
    except TlsPresetError as exc:
        raise HTTPException(422, str(exc)) from exc
    return info, cipher



@router.get("")
async def get_tls_presets(
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("cores", "read")),
):
    rows = (await db.execute(select(tls_presets).order_by(tls_presets.c.name))).mappings().all()
    return [_summary(row) for row in rows]


@router.post("", status_code=201)
async def create_tls_preset(
    body: CertificateInput,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("cores", "create")),
):
    info, cipher = _validated_pair(body.certificate, body.private_key)
    entry = {
        "id": str(uuid.uuid4()),
        "name": body.name.strip(),
        "certificate_pem": body.certificate,
        "encrypted_private_key": cipher,
        **info,
    }
    try:
        await db.execute(insert(tls_presets).values(**entry))
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Certificate preset name already exists") from exc
    return _summary({**entry, "revision": 1, "updated_at": datetime.now(UTC)})


@router.patch("/{preset_id}/name")
async def rename_tls_preset(
    preset_id: str,
    body: PresetRename,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("cores", "update")),
):
    await _preset(db, preset_id)
    try:
        await db.execute(
            update(tls_presets)
            .where(tls_presets.c.id == preset_id)
            .values(name=body.name.strip(), updated_at=datetime.now(UTC))
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Certificate preset name already exists") from exc
    return {"id": preset_id, "name": body.name.strip()}


@router.delete("/{preset_id}", status_code=204)
async def remove_tls_preset(
    preset_id: str,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("cores", "delete")),
):
    await _preset(db, preset_id)
    if await list_bindings(db, preset_id):
        raise HTTPException(409, "Unbind nodes before deleting this TLS preset")
    if await db.scalar(
        select(agent_tls_bindings.c.node_uuid).where(agent_tls_bindings.c.preset_id == preset_id).limit(1)
    ):
        raise HTTPException(409, "Unbind autonomous Agents before deleting this TLS preset")
    await db.execute(delete(tls_presets).where(tls_presets.c.id == preset_id))
    await db.commit()


async def _rotate(db: AsyncSession, preset_id: str, body: CertificateRotation) -> dict:
    """Rotate secrets and queue only UUID-first autonomous Node bindings."""
    row = await _preset(db, preset_id)
    info, cipher = _validated_pair(body.certificate, body.private_key)
    await db.execute(
        update(tls_presets)
        .where(tls_presets.c.id == preset_id)
        .values(
            certificate_pem=body.certificate,
            encrypted_private_key=cipher,
            revision=row["revision"] + 1,
            updated_at=datetime.now(UTC),
            **info,
        )
    )
    agents = (
        (await db.execute(select(agent_tls_bindings.c.node_uuid)
                          .where(agent_tls_bindings.c.preset_id == preset_id)
                          .distinct())).scalars().all()
    )
    for node_uuid in agents:
        await queue_xray_refresh(db, node_uuid)
    await db.commit()
    return {"revision": row["revision"] + 1, "agent_count": len(agents)}


@router.put("/{preset_id}")
async def rotate_certificate(
    preset_id: str,
    body: CertificateRotation,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("cores", "update")),
):
    return await _rotate(db, preset_id, body)


@router.post("/{preset_id}/webhook-token")
async def rotate_webhook_token(
    preset_id: str,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("cores", "update")),
):
    await _preset(db, preset_id)
    token = generate_service_token()
    await db.execute(
        update(tls_presets).where(tls_presets.c.id == preset_id).values(webhook_token_hash=token_digest(token))
    )
    await db.commit()
    return {"token": token, "webhook_path": f"/api/tls-presets/{preset_id}/webhook"}


@router.post("/{preset_id}/webhook")
async def webhook_certificate_update(
    preset_id: str,
    body: CertificateRotation,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    row = await _preset(db, preset_id)
    token = authorization.removeprefix("Bearer ") if authorization and authorization.startswith("Bearer ") else ""
    if not service_token_matches(token, row["webhook_token_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid certificate update token")
    return await _rotate(db, preset_id, body)
