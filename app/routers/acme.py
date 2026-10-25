"""Admin-managed DNS-01 ACME accounts, issuance and renewal jobs."""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.db.acme import acme_dns_accounts, acme_orders
from app.db.tls import tls_presets
from app.models.admin import AdminDetails
from app.operation.acme import (
    AcmeError,
    due_orders,
    issue_order,
    normalize_domains,
    validate_email,
)
from app.operation.tls_presets import encrypt_private_key

from .authentication import require_owner

router = APIRouter(tags=["ACME"], prefix="/api/acme")


class DNSAccountInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    dns_token: str = Field(min_length=10, max_length=2048)
    zone_token: str | None = Field(default=None, max_length=2048)


class ACMEOrderInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    dns_account_id: str
    email: str
    domains: list[str]
    environment: str = "staging"
    auto_renew: bool = True
    preset_id: str | None = None


class ACMEOrderSettings(BaseModel):
    auto_renew: bool


def _safe_account(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "provider": row["provider"],
        "created_at": row["created_at"],
    }


def _safe_order(row) -> dict:
    return {
        name: row[name]
        for name in (
            "id", "name", "preset_id", "dns_account_id", "email", "domains",
            "environment", "auto_renew", "status", "last_error",
            "last_attempt_at", "next_renewal_at", "created_at",
        )
    }


@router.get("/dns-accounts")
async def list_dns_accounts(
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_owner),
):
    rows = (await db.execute(select(acme_dns_accounts).order_by(acme_dns_accounts.c.name))).mappings().all()
    return [_safe_account(row) for row in rows]


@router.post("/dns-accounts", status_code=201)
async def create_dns_account(
    body: DNSAccountInput,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_owner),
):
    try:
        entry = {
            "id": str(uuid.uuid4()), "name": body.name.strip(),
            "provider": "cloudflare",
            "encrypted_dns_token": encrypt_private_key(body.dns_token.strip()),
            "encrypted_zone_token": encrypt_private_key(body.zone_token.strip()) if body.zone_token else None,
        }
        await db.execute(insert(acme_dns_accounts).values(**entry))
        await db.commit()
        return {"id": entry["id"], "name": entry["name"], "provider": entry["provider"]}
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "DNS account name already exists") from exc


@router.delete("/dns-accounts/{account_id}", status_code=204)
async def remove_dns_account(
    account_id: str,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_owner),
):
    if (await db.execute(select(acme_orders.c.id).where(acme_orders.c.dns_account_id == account_id))).first():
        raise HTTPException(409, "DNS account is used by an ACME order")
    await db.execute(delete(acme_dns_accounts).where(acme_dns_accounts.c.id == account_id))
    await db.commit()


@router.get("/orders")
async def list_acme_orders(
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_owner),
):
    rows = (await db.execute(select(acme_orders).order_by(acme_orders.c.created_at.desc()))).mappings().all()
    return [_safe_order(row) for row in rows]


@router.post("/orders", status_code=202)
async def request_acme_certificate(
    body: ACMEOrderInput,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_owner),
):
    if body.environment not in ("staging", "production"):
        raise HTTPException(422, "Choose staging or production")
    try:
        domains = normalize_domains(body.domains)
        email = validate_email(body.email)
    except AcmeError as exc:
        raise HTTPException(422, str(exc)) from exc
    account = (await db.execute(
        select(acme_dns_accounts.c.id).where(acme_dns_accounts.c.id == body.dns_account_id)
    )).scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Cloudflare DNS account not found")
    if body.preset_id:
        preset = (await db.execute(
            select(tls_presets.c.id).where(tls_presets.c.id == body.preset_id)
        )).scalar_one_or_none()
        if not preset:
            raise HTTPException(404, "TLS preset not found")
    entry = {
        "id": str(uuid.uuid4()), "name": body.name.strip(), "dns_account_id": body.dns_account_id,
        "email": email, "domains": domains, "environment": body.environment,
        "auto_renew": body.auto_renew, "preset_id": body.preset_id,
        "status": "pending",
    }
    try:
        await db.execute(insert(acme_orders).values(**entry))
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "ACME order name or preset is already managed") from exc
    background.add_task(issue_order, entry["id"])
    return {"id": entry["id"], "status": "pending"}


@router.get("/orders/{order_id}")
async def get_acme_order(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_owner),
):
    row = (await db.execute(select(acme_orders).where(acme_orders.c.id == order_id))).mappings().first()
    if row is None:
        raise HTTPException(404, "ACME order not found")
    return _safe_order(row)


@router.patch("/orders/{order_id}")
async def update_acme_settings(
    order_id: str,
    body: ACMEOrderSettings,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_owner),
):
    row = (await db.execute(select(acme_orders.c.id).where(acme_orders.c.id == order_id))).first()
    if not row:
        raise HTTPException(404, "ACME order not found")
    await db.execute(update(acme_orders).where(acme_orders.c.id == order_id).values(auto_renew=body.auto_renew))
    await db.commit()
    return {"id": order_id, "auto_renew": body.auto_renew}


@router.post("/orders/{order_id}/renew", status_code=202)
async def renew_acme_certificate(
    order_id: str,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_owner),
):
    row = (await db.execute(select(acme_orders.c.id).where(acme_orders.c.id == order_id))).first()
    if not row:
        raise HTTPException(404, "ACME order not found")
    background.add_task(issue_order, order_id)
    return {"id": order_id, "status": "queued"}


@router.post("/renew-due", status_code=202)
async def renew_due_certificates(
    background: BackgroundTasks,
    _: AdminDetails = Depends(require_owner),
):
    ids = await due_orders()
    for order_id in ids:
        background.add_task(issue_order, order_id)
    return {"queued": len(ids)}


@router.delete("/orders/{order_id}", status_code=204)
async def remove_acme_order(
    order_id: str,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_owner),
):
    row = (await db.execute(select(acme_orders).where(acme_orders.c.id == order_id))).mappings().first()
    if row and row["status"] == "running":
        raise HTTPException(409, "Cannot remove an ACME order during issuance")
    await db.execute(delete(acme_orders).where(acme_orders.c.id == order_id))
    await db.commit()
    # Issued TLS presets and node bindings stay intact when unlinking automation.
