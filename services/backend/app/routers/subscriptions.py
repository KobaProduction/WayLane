"""Product administration uses only canonical UUIDv4 subscription identity.

Node identifiers remain separate, numeric machine identities. The legacy
Xray/Protobuf account-key field carries the same subscription UUID.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import UUID4, BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.db import AsyncSession, get_db
from app.db.models import Admin, Subscription, SubscriptionStatus
from app.models.admin import AdminDetails
from app.models.stats import (
    Period,
    SubscriptionCountMetric,
    SubscriptionCountMetricStatsList,
    SubscriptionUsageStatsList,
)
from app.models.subscription_models import (
    BulkSubscriptionsSelection,
    SubscriptionCreate as InternalSubscriptionCreate,
    SubscriptionModify,
    SubscriptionsUsageBreakdownQuery,
    SubscriptionsUsageQuery,
    SubscriptionUpdateChart,
)
from app.operation import OperatorType
from app.operation.hwid import HWIDOperation
from app.operation.permissions import PermissionDenied, enforce_permission, is_scope_all
from app.operation.subscription_management import SubscriptionManagementOperation
from app.settings import subscription_settings
from app.subscription.entitlements import SubscriptionEntitlement
from app.subscription.identity import subscription_token
from app.subscription_delivery import reconcile_subscriptions, sync_subscription_access

from .authentication import get_current, require_permission
from .dependencies.subscription_queries import get_subscriptions_usage_breakdown_query, get_subscriptions_usage_query

router = APIRouter(prefix="/api/subscriptions", tags=["Subscriptions"])


class SubscriptionSummary(BaseModel):
    uuid: UUID
    managed: bool
    entitlement: SubscriptionEntitlement
    entitlement_revision: int
    policy_revision: int
    technical_status: str
    group_ids: list[int]
    expire_at: datetime | None
    traffic_limit: int | None
    device_limit: int | None
    used_traffic: int
    automatic_reset: bool
    last_traffic_at: datetime | None


class SubscriptionPage(BaseModel):
    items: list[SubscriptionSummary]
    total: int
    limit: int
    offset: int


class SubscriptionEntitlementChange(BaseModel):
    entitlement: SubscriptionEntitlement
    revision: int = Field(ge=1)


class CreateSubscriptionRequest(BaseModel):
    uuid: UUID4 = Field(default_factory=uuid4)
    group_ids: list[int] = Field(min_length=1)
    expire_at: datetime | None = None
    traffic_limit: int | None = Field(default=None, ge=0)
    device_limit: int | None = Field(default=None, ge=0)
    # Commercial entitlement is always initially unpaid. Only the Core
    # service identity can activate it after successful business authorization.
    model_config = ConfigDict(extra="forbid")


class SubscriptionPolicyChange(BaseModel):
    revision: int = Field(ge=0)
    group_ids: list[int] | None = None
    expire_at: datetime | None = None
    traffic_limit: int | None = Field(default=None, ge=0)
    device_limit: int | None = Field(default=None, ge=0)
    enabled: bool | None = None


class SubscriptionDevice(BaseModel):
    hwid: str
    device_os: str | None
    os_version: str | None
    device_model: str | None
    created_at: datetime
    last_used_at: datetime


class SubscriptionDeviceList(BaseModel):
    devices: list[SubscriptionDevice]
    count: int


class SubscriptionLink(BaseModel):
    subscription_uuid: UUID
    url: str


def _summary(subscription: Subscription) -> SubscriptionSummary:
    return SubscriptionSummary(
        uuid=subscription.uuid,
        managed=True,
        entitlement=subscription.entitlement,
        entitlement_revision=subscription.entitlement_revision,
        policy_revision=subscription.policy_revision,
        technical_status=subscription.status.value,
        group_ids=subscription.group_ids,
        expire_at=subscription.expire,
        traffic_limit=subscription.data_limit,
        device_limit=subscription.hwid_limit,
        used_traffic=subscription.used_traffic,
        automatic_reset=subscription.data_limit_reset_strategy.value != "no_reset",
        last_traffic_at=subscription.online_at,
    )


def _scoped_statement(admin: AdminDetails, *, resource: str = "subscriptions", action: str = "read"):
    statement = select(Subscription).options(selectinload(Subscription.groups))
    if not is_scope_all(admin, resource, action):
        statement = statement.where(Subscription.admin_id == admin.id)
    return statement


async def _find_subscription(
    db: AsyncSession,
    subscription_uuid: UUID,
    admin: AdminDetails,
    *,
    resource: str = "subscriptions",
    action: str = "read",
    lock: bool = False,
):
    statement = _scoped_statement(admin, resource=resource, action=action).where(Subscription.uuid == subscription_uuid)
    if resource == "hwids":
        # Device-specific grants never widen subscription visibility. A role
        # with hwids.delete=ALL and subscriptions.read=OWN must not be able
        # to reset another admin's devices by guessing a UUID.
        try:
            enforce_permission(admin, "subscriptions", "read")
        except PermissionDenied as exc:
            raise HTTPException(status_code=403, detail="Subscription read permission required") from exc
        if not is_scope_all(admin, "subscriptions", "read"):
            statement = statement.where(Subscription.admin_id == admin.id)
    if lock:
        statement = statement.with_for_update(of=Subscription)
    subscription = (await db.execute(statement)).scalar_one_or_none()
    if subscription is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return subscription


@router.post("", response_model=SubscriptionSummary, status_code=status.HTTP_201_CREATED)
async def create_subscription(
    body: CreateSubscriptionRequest,
    db: AsyncSession = Depends(get_db),
    admin: AdminDetails = Depends(require_permission("subscriptions", "create")),
):
    # The same UUID identifies the subscription in storage, API and node account payloads.
    operation = SubscriptionManagementOperation(operator_type=OperatorType.API)
    await operation.create_subscription(
        db,
        new_user=InternalSubscriptionCreate(
            uuid=body.uuid,
            status=SubscriptionStatus.active,
            group_ids=body.group_ids,
            expire=body.expire_at,
            data_limit=body.traffic_limit,
            hwid_limit=body.device_limit,
        ),
        admin=admin,
    )
    subscription = await _find_subscription(db, body.uuid, admin)
    # The model defaults to payment_required, including on administrative
    # creation; no request field can bypass the commercial admission gate.
    await sync_subscription_access(subscription)
    return _summary(subscription)


async def require_core_entitlement_key(admin: AdminDetails = Depends(get_current)) -> AdminDetails:
    """Accept only explicitly scoped custom API keys for Core's billing decisions.

    Neither human admin sessions (including the owner) nor inheriting keys
    can decide commercial entitlement. This uses the existing WayLane API key,
    with an additional grant, rather than another shared secret or token.
    """
    if admin.custom_api_key_id is None:
        raise HTTPException(status_code=403, detail="A scoped Core service API key is required")
    try:
        enforce_permission(admin, "subscriptions", "set_entitlement")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail="Core entitlement grant is required") from exc
    return admin


async def require_core_policy_key(
    admin: AdminDetails = Depends(require_core_entitlement_key),
) -> AdminDetails:
    """Commercial subscription policy has the same service authority as entitlement."""
    try:
        enforce_permission(admin, "subscriptions", "update")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail="Core subscription policy grant is required") from exc
    return admin


def _policy_matches(subscription: Subscription, body: SubscriptionPolicyChange) -> bool:
    """A replay at the committed revision must not change policy or node access."""
    fields = body.model_fields_set
    return (
        ("group_ids" not in fields or body.group_ids is None or set(subscription.group_ids) == set(body.group_ids))
        and (
            "expire_at" not in fields
            or (subscription.expire is None and body.expire_at is None)
            or (
                subscription.expire is not None
                and body.expire_at is not None
                and subscription.expire.astimezone(UTC) == body.expire_at.astimezone(UTC)
            )
        )
        and ("traffic_limit" not in fields or subscription.data_limit == (body.traffic_limit or None))
        and ("device_limit" not in fields or subscription.hwid_limit == body.device_limit)
        and (
            "enabled" not in fields
            or body.enabled is None
            or (subscription.status != SubscriptionStatus.disabled) == body.enabled
        )
    )


@router.put("/{subscription_uuid}/policy", response_model=SubscriptionSummary)
async def update_subscription_policy(
    subscription_uuid: UUID,
    body: SubscriptionPolicyChange,
    db: AsyncSession = Depends(get_db),
    admin: AdminDetails = Depends(require_core_policy_key),
):
    subscription = await _find_subscription(db, subscription_uuid, admin, action="update", lock=True)
    if body.revision < subscription.policy_revision:
        raise HTTPException(status_code=409, detail="Stale subscription policy revision")
    if "group_ids" in body.model_fields_set and body.group_ids is None:
        raise HTTPException(status_code=422, detail="group_ids must be an array")
    if body.revision == subscription.policy_revision:
        if not _policy_matches(subscription, body):
            raise HTTPException(status_code=409, detail="Conflicting subscription policy revision")
        await sync_subscription_access(subscription)
        return _summary(subscription)

    changes = {}
    fields = body.model_fields_set
    if "group_ids" in fields:
        changes["group_ids"] = body.group_ids
    if "expire_at" in fields:
        changes["expire"] = body.expire_at or 0
    if "traffic_limit" in fields:
        changes["data_limit"] = body.traffic_limit or 0
    if "device_limit" in fields:
        changes["hwid_limit"] = body.device_limit
    # Use one policy transaction rather than committing group/traffic changes
    # before a second status change. The revision is committed with that state.
    if body.enabled is False:
        changes["status"] = SubscriptionStatus.disabled
    elif body.enabled is True and subscription.status == SubscriptionStatus.disabled:
        changes["status"] = SubscriptionStatus.active
    subscription.policy_revision = body.revision
    if changes:
        await SubscriptionManagementOperation(operator_type=OperatorType.API).modify_subscription_by_uuid(
            db,
            subscription_uuid=subscription.uuid,
            modified_user=SubscriptionModify(**changes),
            admin=admin,
            core_policy=True,
        )
    else:
        await db.commit()
    await db.refresh(subscription)
    await db.refresh(subscription, ["groups"])
    await sync_subscription_access(subscription)
    return _summary(subscription)


@router.get("", response_model=SubscriptionPage)
async def list_subscriptions(
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    admin: AdminDetails = Depends(require_permission("subscriptions", "read")),
):
    query = _scoped_statement(admin)
    rows = (
        (
            await db.execute(
                query.order_by(Subscription.created_at.desc(), Subscription.uuid).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    total = (await db.execute(select(func.count()).select_from(query.order_by(None).subquery()))).scalar_one()
    return SubscriptionPage(
        items=[_summary(subscription) for subscription in rows], total=total, limit=limit, offset=offset
    )


async def require_core_usage_key(
    admin: AdminDetails = Depends(require_core_policy_key),
) -> AdminDetails:
    try:
        enforce_permission(admin, "subscriptions", "reset_usage")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail="Core usage grant is required") from exc
    return admin


@router.post("/traffic/reset-all", status_code=status.HTTP_204_NO_CONTENT)
async def reset_all_subscription_usage(
    db: AsyncSession = Depends(get_db),
    admin: AdminDetails = Depends(require_core_usage_key),
):
    """Core-authorized global technical reset; re-evaluate UUID admission.

    Resetting commercial counters must not restart unrelated VPN Cores.
    Current Xray subscription entitlement is rebuilt over the authenticated
    WSS desired-state channel. User counter telemetry is never reset here.
    """
    if not is_scope_all(admin, "subscriptions", "reset_usage"):
        raise HTTPException(status_code=403, detail="Core global reset requires all scope")
    await SubscriptionManagementOperation(operator_type=OperatorType.API).reset_subscriptions_data_usage(db, admin)
    await reconcile_subscriptions()


class SubscriptionBatchAction(StrEnum):
    disable = "disable"
    enable = "enable"
    reset_usage = "reset_usage"
    delete = "delete"
    reissue = "reissue"


_BATCH_ACTION_PERMISSIONS = {
    SubscriptionBatchAction.disable: "update",
    SubscriptionBatchAction.enable: "update",
    SubscriptionBatchAction.reset_usage: "reset_usage",
    SubscriptionBatchAction.delete: "delete",
    SubscriptionBatchAction.reissue: "revoke_sub",
}


class SubscriptionBatchRequest(BaseModel):
    uuids: set[UUID4] = Field(min_length=1, max_length=100)
    model_config = ConfigDict(extra="forbid")


class SubscriptionBatchResult(BaseModel):
    uuids: list[UUID]
    count: int


@router.post("/bulk/{action}", response_model=SubscriptionBatchResult)
async def perform_subscription_batch_action(
    action: SubscriptionBatchAction,
    body: SubscriptionBatchRequest,
    db: AsyncSession = Depends(get_db),
    admin: AdminDetails = Depends(get_current),
):
    """Translate public UUID identities into private node IDs under action RBAC."""
    permission = _BATCH_ACTION_PERMISSIONS[action]
    try:
        enforce_permission(admin, "subscriptions", permission)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    query = _scoped_statement(admin, action=permission).where(Subscription.uuid.in_(body.uuids))
    subscriptions = (await db.execute(query)).scalars().all()
    if len(subscriptions) != len(body.uuids):
        # An absent or out-of-scope UUID rejects the entire batch; do not
        # disclose which UUID belongs to an unrelated administrator.
        raise HTTPException(status_code=404, detail="Subscription selection not found")

    # Core-managed technical status and usage are part of the revision-fenced
    # projection. Only the existing scoped Core service key can change them.
    core_owned = True
    core_write = core_owned and action in (
        SubscriptionBatchAction.disable,
        SubscriptionBatchAction.enable,
        SubscriptionBatchAction.reset_usage,
    )
    if core_write:
        await require_core_entitlement_key(admin)

    op = SubscriptionManagementOperation(operator_type=OperatorType.API)
    selected = BulkSubscriptionsSelection(uuids={subscription.uuid for subscription in subscriptions})
    if action is SubscriptionBatchAction.disable:
        result = await op.bulk_disable_subscriptions(db, selected, admin, core_policy=core_write)
    elif action is SubscriptionBatchAction.enable:
        result = await op.bulk_enable_subscriptions(db, selected, admin, core_policy=core_write)
    elif action is SubscriptionBatchAction.reset_usage:
        result = await op.bulk_reset_subscription_data_usage(db, selected, admin, core_policy=core_write)
    elif action is SubscriptionBatchAction.reissue:
        result = await op.bulk_revoke_subscription_sub(db, selected, admin)
    else:
        result = await op.bulk_remove_subscriptions(db, selected, admin)

    affected_uuids = set(result.uuids)
    affected = sorted((item.uuid for item in subscriptions if item.uuid in affected_uuids), key=str)
    return SubscriptionBatchResult(uuids=affected, count=len(affected))


@router.get("/analytics/usage", response_model=SubscriptionUsageStatsList)
async def get_subscription_usage_metrics(
    query: Annotated[SubscriptionsUsageBreakdownQuery, Depends(get_subscriptions_usage_breakdown_query)],
    db: AsyncSession = Depends(get_db),
    admin: AdminDetails = Depends(require_permission("subscriptions", "read")),
):
    return await SubscriptionManagementOperation(operator_type=OperatorType.API).get_subscriptions_usage(
        db, admin=admin, query=query
    )


@router.get("/analytics/counts/{metric}", response_model=SubscriptionCountMetricStatsList)
async def get_subscription_count_metrics(
    metric: SubscriptionCountMetric,
    query: Annotated[SubscriptionsUsageQuery, Depends(get_subscriptions_usage_query)],
    db: AsyncSession = Depends(get_db),
    admin: AdminDetails = Depends(require_permission("subscriptions", "read")),
):
    return await SubscriptionManagementOperation(operator_type=OperatorType.API).get_subscriptions_count_metric(
        db, admin=admin, metric=metric, query=query
    )


@router.get("/analytics/client-updates", response_model=SubscriptionUpdateChart)
async def get_subscription_client_update_metrics(
    subscription_uuid: UUID | None = None,
    admin_id: int | None = None,
    period: Period = Period.hour,
    start: datetime | None = None,
    end: datetime | None = None,
    db: AsyncSession = Depends(get_db),
    admin: AdminDetails = Depends(require_permission("subscriptions", "read")),
):
    subscription = await _find_subscription(db, subscription_uuid, admin) if subscription_uuid is not None else None
    return await SubscriptionManagementOperation(operator_type=OperatorType.API).get_subscriptions_sub_update_chart(
        db,
        admin=admin,
        subscription_uuid=subscription.uuid if subscription is not None else None,
        admin_id=admin_id,
        period=period,
        start=start,
        end=end,
    )


@router.get("/{subscription_uuid}", response_model=SubscriptionSummary)
async def get_subscription(
    subscription_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminDetails = Depends(require_permission("subscriptions", "read")),
):
    subscription = await _find_subscription(db, subscription_uuid, admin)
    return _summary(subscription)


@router.put("/{subscription_uuid}/entitlement", response_model=SubscriptionSummary)
async def update_entitlement(
    subscription_uuid: UUID,
    body: SubscriptionEntitlementChange,
    db: AsyncSession = Depends(get_db),
    admin: AdminDetails = Depends(require_core_entitlement_key),
):
    subscription = await _find_subscription(db, subscription_uuid, admin, action="set_entitlement", lock=True)
    if body.revision < subscription.entitlement_revision or (
        body.revision == subscription.entitlement_revision and body.entitlement.value != subscription.entitlement
    ):
        raise HTTPException(status_code=409, detail="Stale or conflicting subscription revision")
    if body.revision > subscription.entitlement_revision:
        subscription.entitlement_revision = body.revision
        subscription.entitlement = body.entitlement.value
        await db.commit()
    # Reassert even for a same-revision retry: a previous response may have
    # failed after the DB commit but before node delivery was queued.
    await sync_subscription_access(subscription)
    return _summary(subscription)


@router.get("/{subscription_uuid}/devices", response_model=SubscriptionDeviceList)
async def get_subscription_devices(
    subscription_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminDetails = Depends(require_permission("hwids", "read")),
):
    subscription = await _find_subscription(db, subscription_uuid, admin, resource="hwids", action="read")
    result = await HWIDOperation(operator_type=OperatorType.API).get_subscription_hwids(
        db, subscription_uuid=subscription.uuid, admin=admin
    )
    return SubscriptionDeviceList(
        devices=[SubscriptionDevice.model_validate(device.model_dump(exclude={"id"})) for device in result.hwids],
        count=result.count,
    )


@router.delete("/{subscription_uuid}/devices/{hwid}")
async def delete_subscription_device(
    subscription_uuid: UUID,
    hwid: str,
    db: AsyncSession = Depends(get_db),
    admin: AdminDetails = Depends(require_permission("hwids", "delete")),
):
    subscription = await _find_subscription(db, subscription_uuid, admin, resource="hwids", action="delete")
    return await HWIDOperation(operator_type=OperatorType.API).delete_subscription_hwid(
        db, subscription_uuid=subscription.uuid, hwid=hwid, admin=admin
    )


@router.post("/{subscription_uuid}/devices/reset")
async def reset_subscription_devices(
    subscription_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminDetails = Depends(require_permission("hwids", "delete")),
):
    subscription = await _find_subscription(db, subscription_uuid, admin, resource="hwids", action="delete")
    return await HWIDOperation(operator_type=OperatorType.API).reset_subscription_hwids(
        db, subscription_uuid=subscription.uuid, admin=admin
    )


@router.post("/{subscription_uuid}/traffic/reset", response_model=SubscriptionSummary)
async def reset_subscription_traffic(
    subscription_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminDetails = Depends(require_core_usage_key),
):
    subscription = await _find_subscription(db, subscription_uuid, admin, action="reset_usage")
    operation = SubscriptionManagementOperation(operator_type=OperatorType.API)
    await operation.reset_subscription_data_usage_by_uuid(db, subscription_uuid=subscription.uuid, admin=admin, core_policy=True)
    await db.refresh(subscription)
    return _summary(subscription)


@router.delete("/{subscription_uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_subscription(
    subscription_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminDetails = Depends(require_permission("subscriptions", "delete")),
):
    subscription = await _find_subscription(db, subscription_uuid, admin, action="delete")
    operation = SubscriptionManagementOperation(operator_type=OperatorType.API)
    await operation.remove_subscription_by_uuid(db, subscription_uuid=subscription.uuid, admin=admin)


@router.post("/{subscription_uuid}/reissue", response_model=SubscriptionLink)
async def reissue_subscription(
    subscription_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminDetails = Depends(require_permission("subscriptions", "revoke_sub")),
):
    # Reissue both VPN credentials and the access URL in one provider operation.
    subscription = await _find_subscription(db, subscription_uuid, admin, action="revoke_sub")
    operation = SubscriptionManagementOperation(operator_type=OperatorType.API)
    await operation.revoke_subscription_sub_by_uuid(db, subscription_uuid=subscription.uuid, admin=admin)
    await db.refresh(subscription)
    return await _subscription_link(db, subscription)


@router.post("/{subscription_uuid}/rotate-access", response_model=SubscriptionLink)
async def rotate_subscription_access(
    subscription_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminDetails = Depends(require_permission("subscriptions", "update")),
):
    subscription = await _find_subscription(db, subscription_uuid, admin, action="update", lock=True)
    subscription.access_generation += 1
    await db.commit()
    return await _subscription_link(db, subscription)


@router.get("/{subscription_uuid}/link", response_model=SubscriptionLink)
async def get_subscription_link(
    subscription_uuid: UUID,
    db: AsyncSession = Depends(get_db),
    admin: AdminDetails = Depends(require_permission("subscriptions", "read")),
):
    subscription = await _find_subscription(db, subscription_uuid, admin)
    return await _subscription_link(db, subscription)


async def _subscription_link(db: AsyncSession, subscription: Subscription) -> SubscriptionLink:
    settings = await subscription_settings()
    origin = settings.url_prefix
    if subscription.admin_id is not None:
        admin = await db.get(Admin, subscription.admin_id)
        if admin is not None and admin.sub_domain:
            origin = admin.sub_domain
    # Wildcard domains remain stable for the lifetime of this UUID rather than
    # changing with every API read.
    origin = origin.replace("*", subscription.uuid.hex[:16]).rstrip("/")
    token = await subscription_token(subscription)
    return SubscriptionLink(subscription_uuid=subscription.uuid, url=f"{origin}/sub/{token}")
