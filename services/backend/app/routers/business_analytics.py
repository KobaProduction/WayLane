"""Read-only VPN business analytics from WayLane database tables.

No schema migrations, new collectors, persistence preferences, or scheduler.
NodeUserUsage stores aggregate user traffic only; NodeUsage stores direction
totals. Never infer per-user upload/download from those unrelated aggregates.
"""
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.db.models import Node, NodeUsage, NodeUserUsage, User, UserHWID, UserSubscriptionUpdate
from app.models.admin import AdminDetails

from .authentication import require_owner

router = APIRouter(prefix="/api/business-analytics", tags=["Business Analytics"])
MAX_SPAN_DAYS = 366
MATRIX_LIMIT = 1500
HISTORY_LIMIT = 8000


def _range(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    finish = end or datetime.now(UTC)
    if finish.tzinfo is None:
        raise HTTPException(422, "End date must include timezone")
    begin = start or finish - timedelta(days=30)
    if begin.tzinfo is None:
        raise HTTPException(422, "Start date must include timezone")
    begin = begin.astimezone(UTC)
    finish = finish.astimezone(UTC)
    if begin >= finish or finish - begin > timedelta(days=MAX_SPAN_DAYS):
        raise HTTPException(422, "Choose an increasing interval of up to 366 days")
    if finish > datetime.now(UTC) + timedelta(days=1):
        raise HTTPException(422, "End date is too far in the future")
    return begin, finish


def _bucket(db: AsyncSession, value, period: str):
    dialect = db.bind.dialect.name
    if dialect == "sqlite":
        return func.strftime("%Y-%m-%dT%H:00:00", value) if period == "hour" else func.date(value)
    if dialect in ("mysql", "mariadb"):
        return func.date_format(value, "%Y-%m-%d %H:00:00" if period == "hour" else "%Y-%m-%d")
    return func.date_trunc(period, value)


def _ids(raw: str | None, *, maximum: int = 200) -> set[int]:
    if not raw:
        return set()
    try:
        parts = [part.strip() for part in raw.split(",")]
        if not parts or any(not p.isdigit() for p in parts):
            raise ValueError()
        result = {int(value) for value in parts}
    except ValueError as exc:
        raise HTTPException(422, "IDs must be comma-separated positive integers") from exc
    if not result or len(result) > maximum or any(value <= 0 for value in result):
        raise HTTPException(422, f"Select 1–{maximum} positive IDs")
    return result


@router.get("/catalog")
async def catalog(
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_owner),
):
    """Read-only user/node discovery, no modified User/Node columns."""
    user_rows = (await db.execute(select(
        User.id, User.username, User.status, User.online_at
    ).order_by(User.username).limit(5000))).all()
    node_rows = (await db.execute(select(
        Node.id, Node.name, Node.status, Node.core_config_id
    ).order_by(Node.name).limit(500))).all()
    return {
        "users": [
            {"id": u.id, "name": u.username, "status": u.status.value,
             "online_at": u.online_at.isoformat() if u.online_at else None}
            for u in user_rows
        ],
        "nodes": [
            {"id": n.id, "name": n.name, "status": n.status.value,
             "core_id": n.core_config_id}
            for n in node_rows
        ],
        "catalog_truncated": len(user_rows) == 5000 or len(node_rows) == 500,
    }


@router.get("/matrix")
async def user_node_matrix(
    start: datetime | None = None,
    end: datetime | None = None,
    all_time: bool = False,
    node_id: int | None = Query(None, gt=0),
    user_id: int | None = Query(None, gt=0),
    status: str | None = None,
    exclude_node_ids: str | None = None,
    exclude_user_ids: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_owner),
):
    """One SQL aggregation showing both user→nodes and node→users.

    User-level historical upload/download is not collected. Values are the
    sum of stored 10-minute user/node traffic samples, not cumulative quotas.
    """
    begin, finish = (None, None) if all_time else _range(start, end)
    excluded_nodes = _ids(exclude_node_ids)
    excluded_users = _ids(exclude_user_ids)
    terms = []
    if begin is not None:
        terms.extend((NodeUserUsage.created_at >= begin, NodeUserUsage.created_at < finish))
    if user_id is not None:
        terms.append(User.id == user_id)
    if node_id is not None:
        terms.append(Node.id == node_id)
    if excluded_nodes:
        terms.append(~Node.id.in_(excluded_nodes))
    if excluded_users:
        terms.append(~User.id.in_(excluded_users))
    if status:
        if status not in {"active", "disabled", "on_hold", "limited", "expired"}:
            raise HTTPException(422, "Unknown user status")
        terms.append(User.status == status)

    query = (
        select(
            User.id.label("user_id"), User.username, User.status,
            Node.id.label("node_id"), Node.name.label("node_name"),
            func.sum(NodeUserUsage.used_traffic).label("total_bytes"),
            func.max(NodeUserUsage.created_at).label("last_recorded_at"),
        )
        .select_from(NodeUserUsage)
        .join(User, User.id == NodeUserUsage.user_id)
        .join(Node, Node.id == NodeUserUsage.node_id)
        .where(*terms)
        .group_by(User.id, User.username, User.status, Node.id, Node.name)
        .order_by(func.sum(NodeUserUsage.used_traffic).desc(), User.id, Node.id)
        .limit(MATRIX_LIMIT + 1)
    )
    rows = (await db.execute(query)).all()
    return {
        "rows": [
            {
                "user_id": row.user_id,
                "username": row.username,
                "status": row.status.value,
                "node_id": row.node_id,
                "node_name": row.node_name,
                "total_bytes": int(row.total_bytes or 0),
                "last_recorded_at": row.last_recorded_at.isoformat() if row.last_recorded_at else None,
            }
            for row in rows[:MATRIX_LIMIT]
        ],
        "truncated": len(rows) > MATRIX_LIMIT,
        "all_time": all_time,
        "direction_available": False,
        "note": "User/node history contains only total bytes; no per-user upload/download breakdown.",
    }


@router.get("/nodes")
async def node_history(
    start: datetime | None = None,
    end: datetime | None = None,
    period: Literal["hour", "day"] = "day",
    node_ids: str | None = None,
    exclude_node_ids: str | None = None,
    exclude_user_ids: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_owner),
):
    """Historical node traffic by direction, or bridge-adjusted total only."""
    begin, finish = _range(start, end)
    selected = _ids(node_ids, maximum=20)
    excluded_nodes = _ids(exclude_node_ids)
    excluded_users = _ids(exclude_user_ids)
    # node_usages has total uplink/downlink. When bridge users are excluded,
    # it is invalid to claim those directional totals exclude their bytes.
    user_adjusted = bool(excluded_users)
    table = NodeUserUsage if user_adjusted else NodeUsage
    bucket = _bucket(db, table.created_at, period)
    terms = [table.created_at >= begin, table.created_at < finish]
    if selected:
        terms.append(Node.id.in_(selected))
    if excluded_nodes:
        terms.append(~Node.id.in_(excluded_nodes))
    if user_adjusted:
        terms.append(~User.id.in_(excluded_users))
        query = (
            select(bucket.label("period_start"), Node.id.label("node_id"), Node.name.label("node_name"),
                   func.sum(NodeUserUsage.used_traffic).label("total_bytes"))
            .select_from(NodeUserUsage)
            .join(Node, Node.id == NodeUserUsage.node_id)
            .join(User, User.id == NodeUserUsage.user_id)
            .where(*terms)
            .group_by(bucket, Node.id, Node.name)
        )
    else:
        query = (
            select(bucket.label("period_start"), Node.id.label("node_id"), Node.name.label("node_name"),
                   func.sum(NodeUsage.uplink).label("upload"),
                   func.sum(NodeUsage.downlink).label("download"))
            .select_from(NodeUsage)
            .join(Node, Node.id == NodeUsage.node_id)
            .where(*terms)
            .group_by(bucket, Node.id, Node.name)
        )
    rows = (await db.execute(query.order_by(bucket, Node.id).limit(HISTORY_LIMIT + 1))).all()
    return {
        "rows": [
            {
                "period_start": str(row.period_start),
                "node_id": row.node_id,
                "node_name": row.node_name,
                "total_bytes": int(row.total_bytes or 0) if user_adjusted else int((row.upload or 0) + (row.download or 0)),
                "upload": None if user_adjusted else int(row.upload or 0),
                "download": None if user_adjusted else int(row.download or 0),
            }
            for row in rows[:HISTORY_LIMIT]
        ],
        "truncated": len(rows) > HISTORY_LIMIT,
        "direction_available": not user_adjusted,
        "source": "node_user_usages" if user_adjusted else "node_usages",
    }


@router.get("/devices")
async def device_summary(
    start: datetime | None = None,
    end: datetime | None = None,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_owner),
):
    """Current HWID breakdown + observed subscription User-Agents for period.

    Last-used data is not a complete history of changes or actual VPN session
    traffic; never equate subscription updates to connected VPN clients.
    """
    begin, finish = _range(start, end)
    def group_by(field, *, date_limit=False):
        q = select(field, func.count()).select_from(UserHWID if not date_limit else UserSubscriptionUpdate)
        if date_limit:
            q = q.where(UserSubscriptionUpdate.created_at >= begin, UserSubscriptionUpdate.created_at < finish)
        return q.group_by(field).order_by(func.count().desc()).limit(40)

    systems = (await db.execute(group_by(UserHWID.device_os))).all()
    versions = (await db.execute(group_by(UserHWID.os_version))).all()
    models = (await db.execute(group_by(UserHWID.device_model))).all()
    clients = (await db.execute(group_by(UserSubscriptionUpdate.user_agent, date_limit=True))).all()
    latest = (await db.execute(
        select(User.username, UserHWID.device_os, UserHWID.os_version,
               UserHWID.device_model, UserHWID.last_used_at)
        .select_from(UserHWID)
        .join(User, User.id == UserHWID.user_id)
        .order_by(UserHWID.last_used_at.desc()).limit(100)
    )).all()
    def as_items(rows):
        return [{"name": name or "Неизвестно", "count": int(count)} for name, count in rows]
    return {
        "systems": as_items(systems),
        "versions": as_items(versions),
        "models": as_items(models),
        "user_agents": as_items(clients),
        "recent": [
            {"username": r.username, "system": r.device_os,
             "version": r.os_version, "model": r.device_model,
             "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None}
            for r in latest
        ],
        "note": "HWID is a current snapshot; User-Agent counts are subscription refreshes, not VPN sessions.",
    }


@router.get("/traffic-series")
async def user_node_traffic_series(
    start: datetime | None = None,
    end: datetime | None = None,
    period: Literal["hour", "day"] = "day",
    user_id: int | None = Query(None, gt=0),
    node_id: int | None = Query(None, gt=0),
    exclude_node_ids: str | None = None,
    exclude_user_ids: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_owner),
):
    """Top 12 per-user/per-node traffic time series for one selected party."""
    if user_id is None and node_id is None:
        raise HTTPException(422, "Select a user or node to see detailed history")
    begin, finish = _range(start, end)
    excluded_nodes = _ids(exclude_node_ids)
    excluded_users = _ids(exclude_user_ids)
    terms = [
        NodeUserUsage.created_at >= begin,
        NodeUserUsage.created_at < finish,
    ]
    if user_id is not None:
        terms.append(User.id == user_id)
    if node_id is not None:
        terms.append(Node.id == node_id)
    if excluded_nodes:
        terms.append(~Node.id.in_(excluded_nodes))
    if excluded_users:
        terms.append(~User.id.in_(excluded_users))
    by_user = user_id is None
    dimension_id = User.id if by_user else Node.id
    dimension_name = User.username if by_user else Node.name
    base = (
        select(
            dimension_id.label("series_id"), dimension_name.label("series_name"),
            func.sum(NodeUserUsage.used_traffic).label("total_bytes"),
        )
        .select_from(NodeUserUsage)
        .join(User, User.id == NodeUserUsage.user_id)
        .join(Node, Node.id == NodeUserUsage.node_id)
        .where(*terms)
        .group_by(dimension_id, dimension_name)
        .order_by(func.sum(NodeUserUsage.used_traffic).desc())
        .limit(12)
    )
    series = (await db.execute(base)).all()
    if not series:
        return {"series": [], "rows": [], "truncated": False, "direction_available": False}
    bucket = _bucket(db, NodeUserUsage.created_at, period)
    rows = (await db.execute(
        select(
            bucket.label("period_start"),
            dimension_id.label("series_id"),
            func.sum(NodeUserUsage.used_traffic).label("total_bytes"),
        )
        .select_from(NodeUserUsage)
        .join(User, User.id == NodeUserUsage.user_id)
        .join(Node, Node.id == NodeUserUsage.node_id)
        .where(*terms, dimension_id.in_([s.series_id for s in series]))
        .group_by(bucket, dimension_id)
        .order_by(bucket, dimension_id)
        .limit(HISTORY_LIMIT + 1)
    )).all()
    return {
        "series": [{"id": s.series_id, "name": s.series_name, "total_bytes": int(s.total_bytes or 0)} for s in series],
        "rows": [
            {"period_start": str(r.period_start), "series_id": r.series_id, "total_bytes": int(r.total_bytes or 0)}
            for r in rows[:HISTORY_LIMIT]
        ],
        "truncated": len(rows) > HISTORY_LIMIT,
        "direction_available": False,
    }
