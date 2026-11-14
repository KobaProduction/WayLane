"""Node-scoped OCI Registry settings with encrypted write-only credentials."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import begin_immediate_if_sqlite
from app.db.models import node_agent_operations, node_agent_registrations, node_agent_registry_settings
from app.node_image_repositories import GHCR_NODE_REPOSITORY, image_repository, valid_repository
from app.operation.tls_presets import decrypt_private_key, encrypt_private_key


class RegistryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repository: str
    username: str = Field(default="", max_length=128)
    password: str | None = Field(default=None, max_length=1024)
    set_default: bool = False

    @field_validator("repository")
    @classmethod
    def approved_repository(cls, value: str) -> str:
        if not valid_repository(value):
            raise ValueError("Expected a complete OCI image repository, not a URL or mutable tag")
        return value

    @field_validator("username", "password")
    @classmethod
    def safe_credentials(cls, value: str | None) -> str | None:
        if value is not None and any(character in value for character in ("\r", "\n", "\x00")):
            raise ValueError("Registry credentials must not contain control line separators")
        return value


class RegistryDefault(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repository: str

    @field_validator("repository")
    @classmethod
    def approved_repository(cls, value: str) -> str:
        if not valid_repository(value):
            raise ValueError("Invalid OCI repository")
        return value


class RegistryDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repository: str

    @field_validator("repository")
    @classmethod
    def approved_repository(cls, value: str) -> str:
        if not valid_repository(value):
            raise ValueError("Invalid OCI repository")
        return value


async def ensure_node_exists(db: AsyncSession, node_uuid: UUID, lock: bool = False) -> None:
    query = select(node_agent_registrations.c.node_uuid).where(node_agent_registrations.c.node_uuid == str(node_uuid))
    if lock:
        await begin_immediate_if_sqlite(db)
        query = query.with_for_update()
    if not await db.scalar(query):
        raise HTTPException(404, "Node not found")


async def public_registry_list(db: AsyncSession, node_uuid: UUID) -> dict:
    await ensure_node_exists(db, node_uuid)
    preferred = await db.scalar(select(node_agent_registrations.c.preferred_registry_repository).where(
        node_agent_registrations.c.node_uuid == str(node_uuid)
    )) or GHCR_NODE_REPOSITORY
    stored = (await db.execute(select(node_agent_registry_settings).where(
        node_agent_registry_settings.c.node_uuid == str(node_uuid)
    ).order_by(node_agent_registry_settings.c.repository))).mappings().all()
    values = {row["repository"]: {
        "repository": row["repository"],
        "username": row["username"] or "",
        "has_credentials": bool(row["encrypted_password"]),
        "official": row["repository"] == GHCR_NODE_REPOSITORY,
        "is_default": row["repository"] == preferred,
    } for row in stored}
    values.setdefault(GHCR_NODE_REPOSITORY, {"repository": GHCR_NODE_REPOSITORY,
                    "username": "", "has_credentials": False, "official": True,
                    "is_default": preferred == GHCR_NODE_REPOSITORY})
    return {"registries": sorted(values.values(), key=lambda item: (not item["official"], item["repository"])),
            "default_repository": preferred}


async def save_node_registry(db: AsyncSession, node_uuid: UUID, input: RegistryUpdate) -> dict:
    await ensure_node_exists(db, node_uuid, lock=True)
    operation = (await db.execute(select(node_agent_operations.c.status, node_agent_operations.c.image).where(
        node_agent_operations.c.node_uuid == str(node_uuid)
    ))).one_or_none()
    if operation and operation.status in {"pending", "queued", "running"} and image_repository(operation.image) == input.repository:
        raise HTTPException(409, "Cannot edit registry credentials while its Node update is active")
    current = (await db.execute(select(node_agent_registry_settings).where(
        node_agent_registry_settings.c.node_uuid == str(node_uuid),
        node_agent_registry_settings.c.repository == input.repository,
    ))).mappings().one_or_none()
    if not current:
        count = await db.scalar(select(func.count()).select_from(node_agent_registry_settings).where(
            node_agent_registry_settings.c.node_uuid == str(node_uuid),
            node_agent_registry_settings.c.repository != GHCR_NODE_REPOSITORY,
        ))
        if input.repository != GHCR_NODE_REPOSITORY and (count or 0) >= 16:
            raise HTTPException(409, "Maximum 16 custom registries per Node")
    if input.password is None:
        if current and input.username != (current["username"] or ""):
            raise HTTPException(422, "Changing a registry username requires a replacement password")
        encrypted = current["encrypted_password"] if current else None
    elif input.password:
        if not input.username:
            raise HTTPException(422, "Registry password requires a username")
        encrypted = encrypt_private_key(input.password)
    else:
        encrypted = None
    if bool(input.username) != bool(encrypted):
        raise HTTPException(422, "Provide both username and password or neither")
    now = datetime.now(UTC)
    if current:
        await db.execute(update(node_agent_registry_settings).where(
            node_agent_registry_settings.c.node_uuid == str(node_uuid),
            node_agent_registry_settings.c.repository == input.repository,
        ).values(username=input.username or None, encrypted_password=encrypted, updated_at=now))
    else:
        await db.execute(insert(node_agent_registry_settings).values(node_uuid=str(node_uuid),
            repository=input.repository, username=input.username or None,
            encrypted_password=encrypted, updated_at=now))
    if input.set_default:
        await db.execute(update(node_agent_registrations).where(
            node_agent_registrations.c.node_uuid == str(node_uuid)
        ).values(preferred_registry_repository=input.repository))
    await db.commit()
    return await public_registry_list(db, node_uuid)


async def select_default_registry(db: AsyncSession, node_uuid: UUID, input: RegistryDefault) -> dict:
    await ensure_node_exists(db, node_uuid, lock=True)
    if input.repository != GHCR_NODE_REPOSITORY:
        saved = await db.scalar(select(node_agent_registry_settings.c.repository).where(
            node_agent_registry_settings.c.node_uuid == str(node_uuid),
            node_agent_registry_settings.c.repository == input.repository,
        ))
        if not saved:
            raise HTTPException(422, "Registry not configured for selected Node")
    await db.execute(update(node_agent_registrations).where(
        node_agent_registrations.c.node_uuid == str(node_uuid)
    ).values(preferred_registry_repository=input.repository))
    await db.commit()
    return await public_registry_list(db, node_uuid)


async def delete_node_registry(db: AsyncSession, node_uuid: UUID, input: RegistryDelete) -> dict:
    await ensure_node_exists(db, node_uuid, lock=True)
    # Never revoke the credentials of an in-flight pull operation.
    from app.db.models import node_agent_operations
    operation = (await db.execute(select(node_agent_operations.c.status, node_agent_operations.c.image).where(
        node_agent_operations.c.node_uuid == str(node_uuid)
    ))).one_or_none()
    if operation and operation.status in {"pending", "queued", "running"} and image_repository(operation.image) == input.repository:
        raise HTTPException(409, "Registry is used by an active Node update")
    await db.execute(delete(node_agent_registry_settings).where(
        node_agent_registry_settings.c.node_uuid == str(node_uuid),
        node_agent_registry_settings.c.repository == input.repository,
    ))
    await db.execute(update(node_agent_registrations).where(
        node_agent_registrations.c.node_uuid == str(node_uuid),
        node_agent_registrations.c.preferred_registry_repository == input.repository,
    ).values(preferred_registry_repository=GHCR_NODE_REPOSITORY))
    await db.commit()
    return await public_registry_list(db, node_uuid)


async def transport_registry(db: AsyncSession, node_uuid: str, image: str) -> dict:
    repository = image_repository(image)
    if not repository:
        raise ValueError("Invalid registry image")
    row = (await db.execute(select(node_agent_registry_settings).where(
        node_agent_registry_settings.c.node_uuid == node_uuid,
        node_agent_registry_settings.c.repository == repository,
    ))).mappings().one_or_none()
    if row is None and repository != GHCR_NODE_REPOSITORY:
        raise ValueError("Registry is no longer configured for this Node")
    return {"repository": repository, "username": (row["username"] or "") if row else "",
            "password": decrypt_private_key(row["encrypted_password"]) if row and row["encrypted_password"] else ""}
