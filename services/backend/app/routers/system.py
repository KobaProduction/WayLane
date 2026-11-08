import asyncio
import time

from fastapi import APIRouter, Depends

from app.db import AsyncSession, get_db
from app.db.crud.wireguard import get_subnet_usage
from app.models.admin import AdminDetails
from app.models.system import (
    InboundSummary,
    SystemResourceStats,
    SystemStats,
    SystemSubscriptionsStats,
    WireGuardSubnetUsage,
    WorkerHealth,
    WorkersHealth,
)
from app.nats import is_nats_enabled
from app.nats.node_rpc import node_nats_client
from app.nats.scheduler_rpc import scheduler_nats_client
from app.operation import OperatorType
from app.operation.system import SystemOperation
from app.utils import responses

from .authentication import require_permission

system_operator = SystemOperation(operator_type=OperatorType.API)
router = APIRouter(tags=["System"], prefix="/api", responses={401: responses._401})


@router.get("/system", response_model=SystemStats)
async def get_system_stats(
    admin_username: str | None = None,
    db: AsyncSession = Depends(get_db),
    admin: AdminDetails = Depends(require_permission("system", "read")),
):
    """Fetch system stats including memory, CPU, disk, and subscription metrics."""
    return await system_operator.get_system_stats(db, admin=admin, admin_username=admin_username)


@router.get("/system/resources", response_model=SystemResourceStats)
async def get_system_resource_stats(
    _: AdminDetails = Depends(require_permission("system", "read")),
):
    """Fetch system resource stats without subscription metrics."""
    return await system_operator.get_system_resource_stats()


@router.get("/system/subscriptions", response_model=SystemSubscriptionsStats)
async def get_system_subscriptions_stats(
    admin_username: str | None = None,
    db: AsyncSession = Depends(get_db),
    admin: AdminDetails = Depends(require_permission("subscriptions", "read")),
):
    """Fetch subscription stats and traffic metrics without system resource stats."""
    return await system_operator.get_system_subscriptions_stats(db, admin=admin, admin_username=admin_username)


@router.get("/inbounds", response_model=list[str])
async def get_inbounds(_: AdminDetails = Depends(require_permission("system", "read"))):
    """Retrieve inbound configurations grouped by protocol."""
    return await system_operator.get_inbounds()


@router.get("/inbounds/details", response_model=list[InboundSummary])
async def get_inbound_details(_: AdminDetails = Depends(require_permission("system", "read"))):
    """Retrieve lightweight inbound metadata for dashboard forms."""
    return await system_operator.get_inbound_details()


@router.get("/wireguard/subnets", response_model=list[WireGuardSubnetUsage])
async def get_wireguard_subnets(
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("cores", "read")),
):
    """Per-subnet WireGuard address usage: capacity, used/free counts and the first free IPs."""
    return await get_subnet_usage(db)


async def _measure_worker_health(request_coro) -> WorkerHealth:
    start = time.monotonic()
    try:
        await request_coro
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return WorkerHealth(status="ok", response_time_ms=elapsed_ms)
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        error_msg = str(exc) or exc.__class__.__name__
        return WorkerHealth(status="down", response_time_ms=elapsed_ms, error=error_msg)


@router.get("/workers/health", response_model=WorkersHealth)
async def get_workers_health(_: AdminDetails = Depends(require_permission("system", "read"))):
    if not is_nats_enabled():
        disabled = WorkerHealth(status="disabled")
        return WorkersHealth(scheduler=disabled, node=disabled)

    timeout = 5.0
    scheduler_task = _measure_worker_health(scheduler_nats_client.request("health_check", {}, timeout))
    node_task = _measure_worker_health(node_nats_client.request("health_check", {}, timeout))
    scheduler_health, node_health = await asyncio.gather(scheduler_task, node_task)

    return WorkersHealth(scheduler=scheduler_health, node=node_health)
