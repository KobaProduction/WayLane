"""Managed REALITY, VLESS encryption and routing presets for Xray cores."""

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.manager import core_manager
from app.db import get_db
from app.db.core_presets import core_preset_bindings, core_presets
from app.db.models import CoreConfig, CoreType
from app.models.admin import AdminDetails
from app.operation.core_presets import (
    ROUTING_TAG, CorePresetError, build_preset, project_public_config,
)
from app.operation.tls_presets import TlsPresetError

from .authentication import require_permission

router = APIRouter(prefix="/api/core-presets", tags=["Core Presets"])


class PresetInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(pattern="^(reality|vless|routing)$")
    data: dict


class BindingInput(BaseModel):
    core_id: int = Field(gt=0)
    preset_id: str = Field(min_length=36, max_length=36)
    inbound_tag: str = Field(min_length=1, max_length=256)


def _summary(row):
    return {
        "id": row["id"], "name": row["name"],
        "kind": row["kind"], "data": row["public_data"],
        "revision": row["revision"],
    }


async def _lookup(db: AsyncSession, preset_id: str):
    result = (await db.execute(select(core_presets).where(core_presets.c.id == preset_id))).mappings().one_or_none()
    if not result:
        raise HTTPException(404, "Core preset not found")
    return result


async def _project_binding(db: AsyncSession, core_id: int, kind: str, tag: str, preset_id: str, data: dict,
                           *, config: dict | None = None):
    core = await db.get(CoreConfig, core_id)
    if not core:
        raise HTTPException(404, "Core not found")
    if core.type not in (None, CoreType.xray, "xray"):
        raise HTTPException(422, "Managed presets only support Xray cores")
    config = config if config is not None else core.config
    try:
        projected = project_public_config(config, kind, tag, preset_id, data)
        if kind == "routing":
            valid_outbounds = {out.get("tag") for out in projected.get("outbounds", [])}
            for rule in projected.get("routing", {}).get("rules", []):
                if isinstance(rule, dict) and rule.get("outboundTag") and rule["outboundTag"] not in valid_outbounds:
                    raise CorePresetError("Routing references an unknown outbound tag")
        validated = core_manager.validate_core(
            projected, core.exclude_inbound_tags, core.fallbacks_inbound_tags, core.type
        )
    except (CorePresetError, ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return core, projected, validated


async def _refresh_core(core, validated):
    # Call after commit: replace the in-memory/public projection and update
    # subscription metadata, without forcing a live node restart.
    await core_manager.update_core(core, validated)


@router.get("")
async def list_presets(
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("cores", "read")),
):
    rows = (await db.execute(select(core_presets).order_by(core_presets.c.kind, core_presets.c.name))).mappings().all()
    return [_summary(row) for row in rows]


@router.post("", status_code=201)
async def create_preset(
    body: PresetInput,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("cores", "create")),
):
    try:
        public, cipher = build_preset(body.kind, body.data)
    except (CorePresetError, TlsPresetError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    entry = {
        "id": str(uuid4()), "kind": body.kind,
        "name": body.name.strip(),
        "public_data": public, "encrypted_secret": cipher,
    }
    if not entry["name"]:
        raise HTTPException(422, "Preset name cannot be blank")
    try:
        await db.execute(insert(core_presets).values(**entry))
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Preset name already exists within kind") from exc
    return {"id": entry["id"], "name": entry["name"], "kind": body.kind, "data": public, "revision": 1}


@router.put("/{preset_id}")
async def replace_preset(
    preset_id: str,
    body: PresetInput,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("cores", "update")),
):
    old = await _lookup(db, preset_id)
    if body.kind != old["kind"]:
        raise HTTPException(422, "Preset kind cannot change")
    try:
        if not body.name.strip():
            raise CorePresetError("Preset name cannot be blank")
        public, cipher = build_preset(body.kind, body.data)
        bindings = (await db.execute(select(core_preset_bindings).where(
            core_preset_bindings.c.preset_id == preset_id
        ))).mappings().all()
        projections_by_core = {}
        for binding in bindings:
            core_id = binding["core_id"]
            current = projections_by_core.get(core_id)
            core, config, validated = await _project_binding(
                db, core_id, body.kind, binding["inbound_tag"], preset_id, public,
                config=current[1] if current else None,
            )
            projections_by_core[core_id] = (core, config, validated)
        projections = list(projections_by_core.values())
    except (CorePresetError, TlsPresetError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        await db.execute(update(core_presets).where(core_presets.c.id == preset_id).values(
            name=body.name.strip(), public_data=public, encrypted_secret=cipher,
            revision=core_presets.c.revision + 1,
        ))
        for core, config, _ in projections:
            core.config = config
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Preset name already exists within kind") from exc
    for core, _, validated in projections:
        await _refresh_core(core, validated)
    return {"id": preset_id, "name": body.name.strip(), "kind": body.kind,
            "data": public, "affected_core_ids": sorted({x[0].id for x in projections}),
            "requires_node_apply": bool(projections)}


@router.delete("/{preset_id}", status_code=204)
async def delete_preset(
    preset_id: str,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("cores", "delete")),
):
    await _lookup(db, preset_id)
    existing = (await db.execute(select(core_preset_bindings.c.id).where(
        core_preset_bindings.c.preset_id == preset_id
    ).limit(1))).first()
    if existing:
        raise HTTPException(409, "Preset is attached to a core; replace or remove its managed references first")
    await db.execute(delete(core_presets).where(core_presets.c.id == preset_id))
    await db.commit()


@router.get("/bindings")
async def list_core_bindings(
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("cores", "read")),
):
    rows = (await db.execute(select(core_preset_bindings).order_by(
        core_preset_bindings.c.core_id, core_preset_bindings.c.kind, core_preset_bindings.c.inbound_tag
    ))).mappings().all()
    return [dict(row) for row in rows]


@router.post("/bindings")
async def bind_preset(
    body: BindingInput,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("cores", "update")),
):
    preset = await _lookup(db, body.preset_id)
    if preset["kind"] == "routing" and body.inbound_tag != ROUTING_TAG:
        raise HTTPException(422, "Routing binding must use __routing__ as the tag")
    if preset["kind"] != "routing" and body.inbound_tag == ROUTING_TAG:
        raise HTTPException(422, "An inbound tag is required")
    core, config, validated = await _project_binding(
        db, body.core_id, preset["kind"], body.inbound_tag, body.preset_id, preset["public_data"]
    )
    existing = (await db.execute(select(core_preset_bindings).where(
        core_preset_bindings.c.core_id == body.core_id,
        core_preset_bindings.c.kind == preset["kind"],
        core_preset_bindings.c.inbound_tag == body.inbound_tag,
    ))).mappings().one_or_none()
    if existing:
        await db.execute(update(core_preset_bindings).where(
            core_preset_bindings.c.id == existing["id"]
        ).values(preset_id=body.preset_id))
        binding_id = existing["id"]
    else:
        binding_id = str(uuid4())
        await db.execute(insert(core_preset_bindings).values(
            id=binding_id, core_id=body.core_id, preset_id=body.preset_id,
            kind=preset["kind"], inbound_tag=body.inbound_tag
        ))
    core.config = config
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Concurrent binding modification") from exc
    await _refresh_core(core, validated)
    return {"id": binding_id, "core_id": core.id, "preset_id": body.preset_id,
            "inbound_tag": body.inbound_tag, "kind": preset["kind"],
            "requires_node_apply": True}


@router.get("/targets")
async def core_targets(
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("cores", "read")),
):
    rows = (await db.execute(select(CoreConfig).order_by(CoreConfig.name))).scalars().all()
    targets = []
    for core in rows:
        if core.type not in (None, CoreType.xray, "xray"):
            continue
        targets.append({
            "core_id": core.id,
            "core_name": core.name,
            "inbounds": [
                {"tag": x.get("tag"), "protocol": x.get("protocol"),
                 "security": (x.get("streamSettings") or {}).get("security")}
                for x in core.config.get("inbounds", []) if x.get("tag")
            ],
        })
    return targets
