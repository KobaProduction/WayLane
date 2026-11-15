"""Write-only, shared GHCR PAT with effective fallback for every Node."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import begin_immediate_if_sqlite
from app.db.models import node_shared_registry_credentials
from app.node_image_repositories import GHCR_NODE_REPOSITORY
from app.operation.tls_presets import decrypt_private_key, encrypt_private_key

REGISTRY_KEY = "ghcr"


class SharedGHCRWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=128)
    token: str = Field(min_length=1, max_length=1024, repr=False)

    @field_validator("username", "token")
    @classmethod
    def reject_delimiters(cls, value: str) -> str:
        if not value.strip() or any(char in value for char in ("\r", "\n", "\x00")):
            raise ValueError("Empty values and line separators are not valid Registry credentials")
        return value


async def shared_ghcr_metadata(db: AsyncSession) -> dict:
    row = (await db.execute(select(
        node_shared_registry_credentials.c.username,
        node_shared_registry_credentials.c.updated_at,
    ).where(node_shared_registry_credentials.c.registry == REGISTRY_KEY))).one_or_none()
    return {
        "repository": GHCR_NODE_REPOSITORY,
        "configured": row is not None,
        "username": row.username if row is not None else "",
        "updated_at": row.updated_at if row is not None else None,
    }


async def save_shared_ghcr(db: AsyncSession, data: SharedGHCRWrite) -> dict:
    await begin_immediate_if_sqlite(db)
    encrypted = encrypt_private_key(data.token)
    now = datetime.now(UTC)
    exists = await db.scalar(select(node_shared_registry_credentials.c.registry).where(
        node_shared_registry_credentials.c.registry == REGISTRY_KEY))
    if exists:
        await db.execute(update(node_shared_registry_credentials).where(
            node_shared_registry_credentials.c.registry == REGISTRY_KEY,
        ).values(username=data.username, encrypted_token=encrypted, updated_at=now))
    else:
        await db.execute(insert(node_shared_registry_credentials).values(
            registry=REGISTRY_KEY, username=data.username,
            encrypted_token=encrypted, updated_at=now,
        ))
    await db.commit()
    return await shared_ghcr_metadata(db)


async def clear_shared_ghcr(db: AsyncSession) -> dict:
    await begin_immediate_if_sqlite(db)
    await db.execute(delete(node_shared_registry_credentials).where(
        node_shared_registry_credentials.c.registry == REGISTRY_KEY))
    await db.commit()
    return await shared_ghcr_metadata(db)


async def effective_shared_ghcr(db: AsyncSession) -> tuple[str, str] | None:
    row = (await db.execute(select(
        node_shared_registry_credentials.c.username,
        node_shared_registry_credentials.c.encrypted_token,
    ).where(node_shared_registry_credentials.c.registry == REGISTRY_KEY))).one_or_none()
    if row is None:
        return None
    return row.username, decrypt_private_key(row.encrypted_token)
