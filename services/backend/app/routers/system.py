import time
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.db import AsyncSession, get_db
from app.db.crud.wireguard import get_subnet_usage
from app.db.models import node_agent_registrations
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
async def get_workers_health(
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission("system", "read")),
):
    """Scheduler NATS health and independent UUID-first Node WSS heartbeat.

    A Node heartbeat is not a Node RPC health check; stale/absent heartbeat
    cannot be reported as a healthy live transport.
    """
    scheduler_health = WorkerHealth(status="disabled")
    if is_nats_enabled():
        scheduler_health = await _measure_worker_health(
            scheduler_nats_client.request("health_check", {}, 5.0)
        )
    cutoff = datetime.now(UTC) - timedelta(seconds=90)
    registrations = (await db.execute(
        select(node_agent_registrations.c.status, node_agent_registrations.c.last_seen_at)
    )).all()
    approved = [row for row in registrations if row.status == "approved"]
    if not approved:
        node_health = WorkerHealth(status="disabled", error="No approved WayLane Nodes")
    else:
        recent = [
            row for row in approved
            if row.last_seen_at is not None
            and (row.last_seen_at.replace(tzinfo=UTC) if row.last_seen_at.tzinfo is None
                 else row.last_seen_at.astimezone(UTC)) >= cutoff
        ]
        node_health = (
            WorkerHealth(status="ok") if len(recent) == len(approved)
            else WorkerHealth(status="down", error="One or more approved Nodes have stale WSS telemetry")
        )
    return WorkersHealth(scheduler=scheduler_health, node=node_health)
