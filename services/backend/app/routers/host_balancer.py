"""Admin CRUD for named Xray client-side host balancer subscriptions."""

from enum import Enum

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.db.host_balancer import host_balancers
from app.db.models import ProxyHost
from app.models.admin import AdminDetails
from app.subscription.config_cache import clear_sub_config_cache

from .authentication import require_permission

router = APIRouter(prefix="/api/host-balancers", tags=["Host Balancers"])


class BalancerStrategy(str, Enum):
    roundRobin = "roundRobin"
    random = "random"
    leastPing = "leastPing"
    leastLoad = "leastLoad"


class BalancerInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    host_ids: list[int] = Field(min_length=2, max_length=32)
    strategy: BalancerStrategy = BalancerStrategy.roundRobin
    probe_interval: int = Field(default=60, ge=30, le=3600)
    hide_members: bool = False
    is_disabled: bool = False

    @model_validator(mode="after")
    def validate_members(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("Name cannot be blank")
        if any(host_id <= 0 for host_id in self.host_ids):
            raise ValueError("Host IDs must be positive")
        if len(set(self.host_ids)) != len(self.host_ids):
            raise ValueError("Host IDs must be unique")
        return self


class BalancerDetails(BalancerInput):
    id: int


async def _check_members(db: AsyncSession, host_ids: list[int]) -> None:
    found = set((await db.scalars(select(ProxyHost.id).where(ProxyHost.id.in_(host_ids)))).all())
    if found != set(host_ids):
        raise HTTPException(422, "Selected member hosts must all exist")


@router.get("", response_model=list[BalancerDetails])
async def list_balancers(
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("hosts", "read")),
):
    return (await db.execute(select(host_balancers).order_by(host_balancers.c.id))).mappings().all()


@router.post("", response_model=BalancerDetails, status_code=201)
async def create_balancer(
    body: BalancerInput,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("hosts", "create")),
):
    await _check_members(db, body.host_ids)
    try:
        result = await db.execute(
            insert(host_balancers).values(**body.model_dump()).returning(host_balancers.c.id)
        )
        balancer_id = result.scalar_one()
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Balancer name already exists or member host disappeared") from exc
    clear_sub_config_cache()
    return BalancerDetails(id=balancer_id, **body.model_dump())


@router.put("/{balancer_id}", response_model=BalancerDetails)
async def update_balancer(
    balancer_id: int,
    body: BalancerInput,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("hosts", "update")),
):
    await _check_members(db, body.host_ids)
    try:
        result = await db.execute(
            update(host_balancers)
            .where(host_balancers.c.id == balancer_id)
            .values(**body.model_dump())
        )
        if result.rowcount != 1:
            await db.rollback()
            raise HTTPException(404, "Host balancer not found")
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Balancer name already exists") from exc
    clear_sub_config_cache()
    return BalancerDetails(id=balancer_id, **body.model_dump())


@router.delete("/{balancer_id}", status_code=204)
async def delete_balancer(
    balancer_id: int,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("hosts", "update")),
):
    result = await db.execute(delete(host_balancers).where(host_balancers.c.id == balancer_id))
    if result.rowcount != 1:
        await db.rollback()
        raise HTTPException(404, "Host balancer not found")
    await db.commit()
    clear_sub_config_cache()
