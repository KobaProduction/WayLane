"""TLS certificate presets, scoped webhook rotation and explicit node bindings."""

import asyncio
import hashlib
import hmac
import json
import ssl
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.db.models import CoreConfig, Node
from app.db.tls import tls_bindings, tls_presets
from app.models.admin import AdminDetails
from app.operation import OperatorType
from app.operation.node import NodeOperation
from app.operation.tls_presets import (
    TlsPresetError,
    encrypt_private_key,
    generate_service_token,
    list_bindings,
    service_token_matches,
    tls_inbounds,
    token_digest,
    validate_certificate_pair,
)

from .authentication import require_permission

router = APIRouter(tags=["TLS Presets"], prefix="/api/tls-presets")


class CertificateInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    certificate: str = Field(min_length=30, max_length=65536)
    private_key: str = Field(min_length=30, max_length=16384)


class CertificateRotation(BaseModel):
    certificate: str = Field(min_length=30, max_length=65536)
    private_key: str = Field(min_length=30, max_length=16384)


class TLSBindingInput(BaseModel):
    node_id: int = Field(gt=0)
    inbound_tag: str = Field(min_length=1, max_length=256)



class PresetRename(BaseModel):
    name: str = Field(min_length=1, max_length=120)


def _summary(row) -> dict:
    d = dict(row)
    return {k: d[k] for k in ("id", "name", "certificate_pem", "fingerprint_sha256", "subject", "not_after", "revision", "updated_at")}


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


async def _apply(db: AsyncSession, node_ids: list[int]) -> list[dict]:
    """Apply only to the explicitly selected nodes, never to other running nodes."""
    operator = NodeOperation(operator_type=OperatorType.API)
    outcomes = []
    for node_id in sorted(set(node_ids)):
        try:
            await operator.connect_single_node(db, node_id, force_start=True)
            node = await db.get(Node, node_id)
            if node is None:
                raise ValueError("Node missing after apply")
            await db.refresh(node)
            status_value = node.status.value if hasattr(node.status, "value") else str(node.status)
            outcomes.append({"node_id": node_id, "status": status_value})
        except Exception:
            # Do not print RPC payloads or private keys in HTTP responses/logs.
            outcomes.append({"node_id": node_id, "status": "error", "detail": "Node apply failed"})
    return outcomes


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
        "id": str(uuid.uuid4()), "name": body.name.strip(),
        "certificate_pem": body.certificate, "encrypted_private_key": cipher,
        **info,
    }
    try:
        await db.execute(insert(tls_presets).values(**entry))
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Certificate preset name already exists") from exc
    return _summary({**entry, "revision": 1, "updated_at": datetime.now(UTC)})


@router.get("/targets")
async def available_tls_targets(
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("cores", "read")),
):
    nodes = (await db.execute(select(Node.id, Node.name, Node.core_config_id))).all()
    configs = {c.id: c for c in (await db.execute(select(CoreConfig))).scalars().all()}
    result = []
    for node_id, name, core_id in nodes:
        effective_core_id = core_id or 1
        core = configs.get(effective_core_id)
        if not core:
            continue
        for tag in tls_inbounds(core.config):
            result.append({"node_id": node_id, "node_name": name, "core_id": effective_core_id, "inbound_tag": tag})
    return result


@router.patch("/{preset_id}/name")
async def rename_tls_preset(
    preset_id: str,
    body: PresetRename,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("cores", "update")),
):
    await _preset(db, preset_id)
    try:
        await db.execute(update(tls_presets).where(tls_presets.c.id == preset_id).values(
            name=body.name.strip(), updated_at=datetime.now(UTC)
        ))
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
    await db.execute(delete(tls_presets).where(tls_presets.c.id == preset_id))
    await db.commit()


@router.get("/{preset_id}/bindings")
async def get_tls_bindings(
    preset_id: str,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("cores", "read")),
):
    await _preset(db, preset_id)
    return await list_bindings(db, preset_id)


@router.post("/{preset_id}/bindings", status_code=201)
async def add_tls_binding(
    preset_id: str,
    body: TLSBindingInput,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("cores", "update")),
):
    await _preset(db, preset_id)
    node = await db.get(Node, body.node_id)
    if not node:
        raise HTTPException(404, "Node not found")
    core_id = node.core_config_id or 1
    core = await db.get(CoreConfig, core_id)
    if not core or body.inbound_tag not in tls_inbounds(core.config):
        raise HTTPException(422, "Selected node has no matching TLS inbound")
    binding = {
        "id": str(uuid.uuid4()), "preset_id": preset_id,
        "node_id": node.id, "core_id": core_id, "inbound_tag": body.inbound_tag,
    }
    try:
        await db.execute(insert(tls_bindings).values(**binding))
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Node TLS inbound is already assigned") from exc
    return {**binding, "apply": await _apply(db, [node.id])}


@router.get("/{preset_id}/bindings/{binding_id}/check")
async def check_tls_binding(
    preset_id: str,
    binding_id: str,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("cores", "read")),
):
    """Verify the certificate actually served by the selected TLS inbound."""
    preset = await _preset(db, preset_id)
    binding = (await db.execute(select(tls_bindings).where(
        tls_bindings.c.id == binding_id, tls_bindings.c.preset_id == preset_id
    ))).mappings().first()
    if not binding:
        raise HTTPException(404, "TLS binding not found")
    node = await db.get(Node, binding["node_id"])
    core = await db.get(CoreConfig, binding["core_id"])
    if not node or not core or node.core_config_id != core.id:
        raise HTTPException(409, "Node is no longer assigned to the bound core")
    config = json.loads(core.config) if isinstance(core.config, str) else core.config
    inbound = next((item for item in config.get("inbounds", []) if item.get("tag") == binding["inbound_tag"]), None)
    if not inbound or (inbound.get("streamSettings") or {}).get("security") != "tls":
        raise HTTPException(409, "TLS inbound no longer exists")
    try:
        port = int(inbound["port"])
        if not 1 <= port <= 65535:
            raise ValueError()
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, "TLS inbound does not have a single TCP port") from exc

    # This is a diagnostic fingerprint check, not a public-trust validation:
    # private local test certificates may be self-signed. Do not label a
    # certificate trusted merely because its fingerprint matches the preset.
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    writer = None
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(node.address, port, ssl=ctx, server_hostname=None),
            timeout=6,
        )
        peer_der = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
        fingerprint = hashlib.sha256(peer_der).hexdigest()
        matches = hmac.compare_digest(fingerprint, preset["fingerprint_sha256"])
        return {
            "node_id": node.id,
            "inbound_tag": binding["inbound_tag"],
            "reachable": True,
            "fingerprint_match": matches,
            "presented_fingerprint_sha256": fingerprint,
        }
    except (OSError, TimeoutError, ssl.SSLError, AttributeError):
        return {"node_id": node.id, "inbound_tag": binding["inbound_tag"], "reachable": False, "fingerprint_match": False}
    finally:
        if writer:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass


@router.delete("/{preset_id}/bindings/{binding_id}")
async def delete_tls_binding(
    preset_id: str,
    binding_id: str,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("cores", "update")),
):
    await _preset(db, preset_id)
    row = (await db.execute(select(tls_bindings).where(
        tls_bindings.c.id == binding_id, tls_bindings.c.preset_id == preset_id
    ))).mappings().first()
    if not row:
        raise HTTPException(404, "Binding not found")
    await db.execute(delete(tls_bindings).where(tls_bindings.c.id == binding_id))
    await db.commit()
    return {"removed": binding_id, "apply": await _apply(db, [row["node_id"]])}


@router.post("/{preset_id}/apply")
async def apply_tls_preset(
    preset_id: str,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("cores", "update")),
):
    await _preset(db, preset_id)
    return {"nodes": await _apply(db, [r["node_id"] for r in await list_bindings(db, preset_id)])}


async def _rotate(db: AsyncSession, preset_id: str, body: CertificateRotation) -> dict:
    row = await _preset(db, preset_id)
    info, cipher = _validated_pair(body.certificate, body.private_key)
    await db.execute(
        update(tls_presets).where(tls_presets.c.id == preset_id).values(
            certificate_pem=body.certificate,
            encrypted_private_key=cipher,
            revision=row["revision"] + 1,
            updated_at=datetime.now(UTC),
            **info,
        )
    )
    await db.commit()
    bindings = await list_bindings(db, preset_id)
    return {"revision": row["revision"] + 1, "nodes": await _apply(db, [b["node_id"] for b in bindings])}


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
    await db.execute(update(tls_presets).where(tls_presets.c.id == preset_id).values(webhook_token_hash=token_digest(token)))
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
