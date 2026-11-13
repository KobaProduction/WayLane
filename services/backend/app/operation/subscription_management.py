import asyncio
import re
from collections import Counter
from datetime import UTC, datetime, datetime as dt, timedelta as td
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app import notification
from app.db import AsyncSession
from app.db.crud.admin import get_admin
from app.db.crud.bulk import (
    BulkSubscriptionUpdateConflict,
    count_bulk_datalimit_targets,
    count_bulk_expire_targets,
    count_bulk_proxy_targets,
    get_bulk_user_limit_targets,
    legacy_bulk_scope_contains_core_managed,
    reset_all_subscriptions_data_usage,
    update_users_datalimit,
    update_users_expire,
    update_users_proxy_settings,
)
from app.db.crud.hwid import get_subscription_hwid_count
from app.db.crud.subscription import (
    build_revoked_proxy_settings,
    bulk_reset_subscription_data_usage,
    bulk_revoke_subscription_sub,
    bulk_set_owner,
    create_subscription,
    get_all_subscriptions_usages,
    get_expired_subscriptions,
    get_subscription_count_metric_stats,
    get_subscription_usages,
    get_subscriptions,
    get_subscriptions_agent_stats,
    get_subscriptions_by_uuid_batch,
    get_subscriptions_count_by_admin,
    get_subscriptions_simple,
    get_subscriptions_sub_update_list,
    load_subscription_attrs,
    lock_admin_quota_row,
    modify_subscription as crud_modify_user,
    remove_expired_subscriptions,
    remove_subscription,
    remove_subscriptions,
    reset_subscription_by_next,
    reset_subscription_data_usage,
    revoke_subscription_sub,
    set_owner,
)
from app.db.crud.wireguard import ensure_wireguard_subnet_pools
from app.db.models import Subscription, SubscriptionStatus, SubscriptionTemplate
from app.models.admin import AdminDetails
from app.models.proxy import ProxyTable
from app.models.settings import HWIDSettings
from app.models.stats import (
    Period,
    SubscriptionCountMetric,
    SubscriptionCountMetricStatsList,
    SubscriptionUsageStatsList,
    validate_user_count_metric_scope,
)
from app.models.subscription_models import (
    BulkOperationDryRunResponse,
    BulkSubscription,
    BulkSubscriptionsActionResponse,
    BulkSubscriptionsApplyTemplate,
    BulkSubscriptionsProxy,
    BulkSubscriptionsSelection,
    BulkSubscriptionsSetOwner,
    ExpiredSubscriptionsQuery,
    ModifySubscriptionByTemplate,
    RemoveSubscriptionsResponse,
    SubscriptionCreate,
    SubscriptionListQuery,
    SubscriptionModify,
    SubscriptionNotificationResponse,
    SubscriptionResponse,
    SubscriptionSimple,
    SubscriptionSimpleListQuery,
    SubscriptionSortField,
    SubscriptionsResponse,
    SubscriptionsSimpleResponse,
    SubscriptionStatusToggle,
    SubscriptionsUsageBreakdownQuery,
    SubscriptionsUsageQuery,
    SubscriptionUpdateChart,
    SubscriptionUpdateChartSegment,
    SubscriptionUpdateChartStat,
    SubscriptionUpdateList,
    SubscriptionUsageQuery,
)
from app.operation import BaseOperation, OperatorType
from app.operation.permissions import (
    PermissionDenied,
    apply_template_access,
    enforce_permission,
    get_effective_limits,
    get_scope_admin_id,
    is_scope_all,
)
from app.settings import hwid_settings, subscription_settings
from app.subscription.identity import subscription_token
from app.subscription_delivery import (
    node_payload_signature,
    remove_subscription as sync_remove_subscription,
    remove_subscriptions as sync_remove_subscriptions,
    sync_subscription,
    sync_subscriptions,
)
from app.utils.helpers import fix_datetime_timezone
from app.utils.hwid import resolve_effective_hwid_settings
from app.utils.logger import get_logger
from app.utils.system import readable_duration, readable_size
from app.utils.wireguard import prepare_wireguard_keys
from config import subscription_env_settings, usage_settings


def _has_permission(admin: AdminDetails, resource: str, action: str) -> bool:
    """Return True if admin has the given resource+action permission (no scope check)."""
    try:
        enforce_permission(admin, resource, action)
        return True
    except PermissionDenied:
        return False


async def _resolve_subscriptions_usage_admins_filter(
    operation: BaseOperation,
    db: AsyncSession,
    admin: AdminDetails,
    requested_admins: list[str] | None,
) -> list[str] | None:
    """Resolve admin username filters for aggregate user usage endpoints."""
    can_read_all_users = is_scope_all(admin, "subscriptions", "read")
    if not requested_admins:
        return None if can_read_all_users else [admin.username]

    admins_filter = list(dict.fromkeys(requested_admins))
    if can_read_all_users or all(username == admin.username for username in admins_filter):
        return admins_filter

    if not _has_permission(admin, "admins", "read"):
        await operation.raise_error(message="You're not allowed", code=403)

    for username in admins_filter:
        if username == admin.username:
            continue
        db_admin = await get_admin(db, username, load_users=False, load_usage_logs=False)
        if not db_admin:
            await operation.raise_error(message="Admin not found", code=404)

    return admins_filter


logger = get_logger("subscription-operation")

_USER_AGENT_SPLIT_RE = re.compile(r"[;/\s\(\)]+")
_VERSION_TOKEN_RE = re.compile(r"v?\d+(?:\.\d+)*", re.IGNORECASE)


def _resolve_enabled_subscription_status(user: Subscription) -> SubscriptionStatus:
    now = dt.now(UTC)
    expire = user.expire
    if expire is not None and expire.replace(tzinfo=UTC) <= now:
        return SubscriptionStatus.expired
    if user.data_limit is not None and user.data_limit > 0 and user.used_traffic >= user.data_limit:
        return SubscriptionStatus.limited
    if user.on_hold_expire_duration is not None:
        return SubscriptionStatus.on_hold
    return SubscriptionStatus.active


class SubscriptionManagementOperation(BaseOperation):
    @staticmethod
    def _require_core_policy_context(db_user: Subscription, *, core_policy: bool = False) -> None:
        """Core-created VPN policy is fenced; legacy admin calls must not override it.

        The only caller passing `core_policy=True` is the UUID policy route after
        scoped service-key authentication. Old numeric/username APIs cannot
        grant themselves Core policy authority via a request body.
        """
        if not core_policy:
            raise HTTPException(
                status_code=403,
                detail="Core-managed subscription policy must be updated through VPN Core",
            )

    @staticmethod
    def _is_non_blocking_sync_operator(operator_type: OperatorType) -> bool:
        return operator_type in (OperatorType.API, OperatorType.WEB)

    @staticmethod
    def _resolve_effective_hwid_limit(
        hwid_limit: int | None,
        effective_hwid_conf: HWIDSettings | None,
    ) -> int | None:
        if hwid_limit is not None:
            return hwid_limit
        if effective_hwid_conf is None or not effective_hwid_conf.enabled:
            return None
        return effective_hwid_conf.fallback_limit

    @staticmethod
    def _apply_explicit_null_hwid_limit(db_user: Subscription, modified_user: SubscriptionModify) -> None:
        if "hwid_limit" in modified_user.model_fields_set and modified_user.hwid_limit is None:
            db_user.hwid_limit = None

    @staticmethod
    def _format_validation_errors(error: ValidationError) -> str:
        return "; ".join(
            [f"{'.'.join(str(loc_part) for loc_part in err['loc'])}: {err['msg']}" for err in error.errors()]
        )

    @staticmethod
    async def generate_subscription_url(user: SubscriptionNotificationResponse | Subscription, db_user: Subscription):
        """Mint a link from the loaded ORM row, never through a second DB session.

        In addition to avoiding an N+1, this protects asyncpg from cross-loop
        checkout in a multi-threaded TestClient and keeps URLs consistent with
        the request's committed access generation and nonce.
        """
        if user.uuid != db_user.uuid:
            raise ValueError("Subscription UUID differs from loaded access identity")
        token = await subscription_token(db_user)
        # Wildcard vanity hosts must be stable across refreshes and never depend
        # on technical usernames or a random value generated per HTTP response.
        stable_host_label = db_user.uuid.hex[:16]
        settings = await subscription_settings()
        url_prefix = (
            user.admin.sub_domain.replace("*", stable_host_label)
            if user.admin and user.admin.sub_domain
            else settings.url_prefix.replace("*", stable_host_label)
        )
        return f"{url_prefix.rstrip('/')}/{subscription_env_settings.path}/{token}"

    async def validate_subscription(
        self, db_user: Subscription, include_subscription_url: bool = True
    ) -> SubscriptionNotificationResponse:
        lifetime_used_traffic = db_user.lifetime_used_traffic
        group_ids = list(db_user.group_ids or [])
        group_names = list(db_user.group_names or [])
        user = SubscriptionNotificationResponse.model_validate(db_user)
        user.lifetime_used_traffic = lifetime_used_traffic
        user.group_ids = group_ids
        user.group_names = group_names
        if include_subscription_url:
            user.subscription_url = await self.generate_subscription_url(user, db_user)
        return user

    async def update_subscription(
        self, db_user: Subscription, include_subscription_url: bool = True
    ) -> SubscriptionNotificationResponse:
        await sync_subscription(db_user)

        user = await self.validate_subscription(
            db_user,
            include_subscription_url=include_subscription_url,
        )
        return user

    async def _prepare_subscription_proxy_settings(
        self,
        db: AsyncSession,
        groups: list,
        proxy_settings: ProxyTable,
        *,
        exclude_subscription_uuid: UUID | None = None,
    ) -> ProxyTable:
        try:
            return await prepare_wireguard_keys(
                db,
                proxy_settings,
                groups,
                exclude_subscription_uuid=exclude_subscription_uuid,
            )
        except ValueError as exc:
            await self.raise_error(message=str(exc), code=400, db=db)

    async def _prepare_revoked_proxy_settings(self, db: AsyncSession, db_user: Subscription) -> ProxyTable:
        groups = db_user.__dict__.get("groups")
        if groups is None:
            groups = await db_user.awaitable_attrs.groups

        return await self._prepare_subscription_proxy_settings(
            db,
            groups,
            ProxyTable.model_validate(build_revoked_proxy_settings(db_user)),
            exclude_subscription_uuid=db_user.uuid,
        )

    async def _get_validated_template_with_access(
        self, db: AsyncSession, template_id: int, admin: AdminDetails
    ) -> SubscriptionTemplate:
        """Fetch a user template and verify the admin is allowed to access it via allowed_template_ids."""
        allowed = apply_template_access(admin, [template_id])
        if allowed is not None and template_id not in allowed:
            await self.raise_error("Subscription Template not found", 404)
        return await self.get_validated_subscription_template(db, template_id)

    async def _enforce_subscription_limits(
        self,
        db: AsyncSession,
        admin: AdminDetails,
        *,
        data_limit: int | None = None,
        expire: dt | int | None = None,
        status: SubscriptionStatus | None = None,
        on_hold_expire_duration: int | None = None,
        on_hold_timeout: dt | int | None = None,
        hwid_limit: int | None = None,
        data_limit_reset_strategy=None,
        next_plan=None,
        check_max_users: bool = False,
        require_finite_data_limit: bool = True,
        require_finite_expire: bool = True,
        require_finite_on_hold_timeout: bool = True,
        require_finite_hwid_limit: bool = True,
        reference_time: dt | None = None,
    ) -> None:
        """Enforce role-level limits and feature flags. No-op for owner."""
        if admin.is_owner:
            return

        limits = get_effective_limits(admin)

        if check_max_users and limits.max_users is not None:
            await lock_admin_quota_row(db, admin.id)
            current_count = await get_subscriptions_count_by_admin(db, admin.id)
            if current_count >= limits.max_users:
                await self.raise_error(message=f"Subscription limit reached ({limits.max_users})", code=400, db=db)

        if limits.data_limit_max is not None and require_finite_data_limit and (data_limit is None or data_limit <= 0):
            await self.raise_error(
                message=f"Data limit cannot be unlimited; maximum is {readable_size(limits.data_limit_max)}",
                code=400,
                db=db,
            )

        if data_limit is not None and data_limit > 0:
            if limits.data_limit_min is not None and data_limit < limits.data_limit_min:
                await self.raise_error(
                    message=f"Data limit must be at least {readable_size(limits.data_limit_min)}", code=400, db=db
                )
            if limits.data_limit_max is not None and data_limit > limits.data_limit_max:
                await self.raise_error(
                    message=f"Data limit cannot exceed {readable_size(limits.data_limit_max)}", code=400, db=db
                )

        status_value = getattr(status, "value", status)
        uses_on_hold_duration = status_value == SubscriptionStatus.on_hold.value

        if limits.expire_max is not None and require_finite_expire:
            if uses_on_hold_duration:
                if on_hold_expire_duration is None or on_hold_expire_duration <= 0:
                    await self.raise_error(
                        message=(
                            f"On-hold duration cannot be unlimited; maximum is {readable_duration(limits.expire_max)}"
                        ),
                        code=400,
                        db=db,
                    )
            elif expire is None or expire == 0:
                await self.raise_error(
                    message=f"Expire cannot be unlimited; maximum is {readable_duration(limits.expire_max)} from now",
                    code=400,
                    db=db,
                )

        if expire is not None and expire != 0:
            expire_dt = fix_datetime_timezone(expire)
            seconds = (expire_dt - (reference_time or datetime.now(UTC))).total_seconds()
            if limits.expire_min is not None and seconds < limits.expire_min:
                await self.raise_error(
                    message=f"Expire must be at least {readable_duration(limits.expire_min)} from now",
                    code=400,
                    db=db,
                )
            if limits.expire_max is not None and seconds > limits.expire_max:
                await self.raise_error(
                    message=f"Expire cannot exceed {readable_duration(limits.expire_max)} from now",
                    code=400,
                    db=db,
                )

        if on_hold_expire_duration is not None and on_hold_expire_duration != 0:
            if limits.expire_min is not None and on_hold_expire_duration < limits.expire_min:
                await self.raise_error(
                    message=f"On-hold duration must be at least {readable_duration(limits.expire_min)}",
                    code=400,
                    db=db,
                )
            if limits.expire_max is not None and on_hold_expire_duration > limits.expire_max:
                await self.raise_error(
                    message=f"On-hold duration cannot exceed {readable_duration(limits.expire_max)}",
                    code=400,
                    db=db,
                )

        if (
            limits.on_hold_timeout_max is not None
            and require_finite_on_hold_timeout
            and (on_hold_timeout is None or on_hold_timeout == 0)
        ):
            await self.raise_error(
                message=f"On-hold timeout cannot be unlimited; maximum is {readable_duration(limits.on_hold_timeout_max)} from now",
                code=400,
                db=db,
            )

        if on_hold_timeout is not None and on_hold_timeout != 0:
            if isinstance(on_hold_timeout, dt):
                timeout_seconds = (fix_datetime_timezone(on_hold_timeout) - datetime.now(UTC)).total_seconds()
            else:
                timeout_seconds = on_hold_timeout

            if limits.on_hold_timeout_min is not None and timeout_seconds < limits.on_hold_timeout_min:
                await self.raise_error(
                    message=f"On-hold timeout must be at least {readable_duration(limits.on_hold_timeout_min)}",
                    code=400,
                    db=db,
                )
            if limits.on_hold_timeout_max is not None and timeout_seconds > limits.on_hold_timeout_max:
                await self.raise_error(
                    message=f"On-hold timeout cannot exceed {readable_duration(limits.on_hold_timeout_max)}",
                    code=400,
                    db=db,
                )

        if (
            limits.max_hwid_per_user is not None
            and require_finite_hwid_limit
            and (hwid_limit is None or hwid_limit <= 0)
        ):
            await self.raise_error(
                message=f"HWID limit cannot be unlimited; maximum is {limits.max_hwid_per_user}",
                code=400,
                db=db,
            )

        if hwid_limit is not None:
            if limits.min_hwid_per_user is not None and hwid_limit < limits.min_hwid_per_user:
                await self.raise_error(
                    message=f"HWID limit must be at least {limits.min_hwid_per_user}", code=400, db=db
                )
            if limits.max_hwid_per_user is not None and hwid_limit > limits.max_hwid_per_user:
                await self.raise_error(message=f"HWID limit cannot exceed {limits.max_hwid_per_user}", code=400, db=db)

        features = admin.role.features if admin.role else None
        if features is not None:
            if data_limit_reset_strategy is not None and not features.can_use_reset_strategy:
                strategy_val = getattr(data_limit_reset_strategy, "value", str(data_limit_reset_strategy))
                if strategy_val != "no_reset":
                    await self.raise_error(message="Reset strategy is not allowed for your role", code=403, db=db)
            if next_plan is not None and not features.can_use_next_plan:
                await self.raise_error(message="Next plan is not allowed for your role", code=403, db=db)

    async def _enforce_manual_subscription_write_access(self, admin: AdminDetails, db: AsyncSession) -> None:
        if admin.is_owner or admin.role is None:
            return
        if admin.role.access.require_template:
            await self.raise_error(message="Manual user create/modify is not allowed for your role", code=403, db=db)

    async def create_subscription(
        self, db: AsyncSession, new_user: SubscriptionCreate, admin: AdminDetails, *, skip_role_limits: bool = False
    ) -> SubscriptionResponse:
        if not skip_role_limits:
            await self._enforce_manual_subscription_write_access(admin, db)

        global_hwid_conf = await hwid_settings()
        effective_hwid_conf = resolve_effective_hwid_settings(
            global_hwid_conf,
            admin.role.hwid if admin.role is not None else None,
        )

        effective_hwid_limit = self._resolve_effective_hwid_limit(new_user.hwid_limit, effective_hwid_conf)

        if not skip_role_limits:
            await self._enforce_subscription_limits(
                db,
                admin,
                data_limit=new_user.data_limit,
                expire=new_user.expire,
                status=new_user.status,
                on_hold_expire_duration=new_user.on_hold_expire_duration,
                on_hold_timeout=new_user.on_hold_timeout,
                hwid_limit=effective_hwid_limit,
                data_limit_reset_strategy=new_user.data_limit_reset_strategy,
                next_plan=new_user.next_plan,
                check_max_users=True,
            )

        if effective_hwid_limit is not None and not admin.is_owner and effective_hwid_conf is not None:
            if effective_hwid_conf.min_limit is not None and effective_hwid_limit < effective_hwid_conf.min_limit:
                await self.raise_error(
                    message=f"HWID limit cannot be less than {effective_hwid_conf.min_limit}", code=400, db=db
                )
            if (
                effective_hwid_conf.max_limit is not None
                and effective_hwid_conf.max_limit > 0
                and (effective_hwid_limit > effective_hwid_conf.max_limit or effective_hwid_limit == 0)
            ):
                await self.raise_error(
                    message=f"HWID limit cannot exceed {effective_hwid_conf.max_limit}", code=400, db=db
                )

        if new_user.next_plan is not None and new_user.next_plan.subscription_template_id is not None:
            await self._get_validated_template_with_access(db, new_user.next_plan.subscription_template_id, admin)

        all_groups = await self.validate_all_groups(db, new_user, admin)
        db_admin = await get_admin(db, admin.username, load_users=False, load_usage_logs=False)
        # peer IPs are never taken from input; the subnet pool assigns them at creation
        new_user.proxy_settings.wireguard.peer_ips = []
        new_user.proxy_settings = await self._prepare_subscription_proxy_settings(
            db, all_groups, new_user.proxy_settings
        )

        try:
            db_user = await create_subscription(db, new_user, all_groups, db_admin)
        except IntegrityError:
            await self.raise_error(message="Subscription already exists", code=409, db=db)
        except ValueError as exc:  # WireGuard subnet exhausted
            await self.raise_error(message=str(exc), code=400, db=db)

        user = await self.update_subscription(db_user)

        logger.info(f'New subscription "{db_user.uuid}" added by admin "{admin.username}"')

        asyncio.create_task(notification.create_subscription(user, admin))

        return user

    async def _prepare_modified_subscription(
        self,
        db: AsyncSession,
        db_user: Subscription,
        modified_user: SubscriptionModify,
        admin: AdminDetails,
        *,
        skip_role_limits: bool = False,
    ):
        modified_fields = modified_user.model_fields_set
        hwid_limit_was_changed = "hwid_limit" in modified_fields
        effective_hwid_conf = None
        effective_hwid_limit = modified_user.hwid_limit
        if hwid_limit_was_changed:
            global_hwid_conf = await hwid_settings()
            effective_hwid_conf = resolve_effective_hwid_settings(
                global_hwid_conf,
                admin.role.hwid if admin.role is not None else None,
            )
            effective_hwid_limit = self._resolve_effective_hwid_limit(modified_user.hwid_limit, effective_hwid_conf)

        if hwid_limit_was_changed and effective_hwid_limit is not None and effective_hwid_limit > 0:
            current_count = await get_subscription_hwid_count(db, db_user.uuid)
            if current_count > effective_hwid_limit:
                await self.raise_error(
                    message=f"Cannot lower HWID limit below current device count ({current_count}). Remove devices first.",
                    code=400,
                    db=db,
                )

        if not skip_role_limits:
            data_limit_was_changed = "data_limit" in modified_fields and modified_user.data_limit is not None
            expire_was_changed = "expire" in modified_fields and modified_user.expire is not None
            status_was_changed = modified_user.status is not None
            modified_status_value = getattr(modified_user.status, "value", modified_user.status)
            status_requires_finite_expire = modified_status_value in {
                SubscriptionStatus.active.value,
                SubscriptionStatus.on_hold.value,
            }
            on_hold_duration_was_changed = (
                "on_hold_expire_duration" in modified_fields and modified_user.on_hold_expire_duration is not None
            )
            expire_requires_finite_limit = (
                expire_was_changed
                or (status_was_changed and status_requires_finite_expire)
                or on_hold_duration_was_changed
            )
            on_hold_timeout_was_changed = "on_hold_timeout" in modified_fields
            on_hold_timeout_requires_finite_limit = on_hold_timeout_was_changed or (
                status_was_changed and modified_status_value == SubscriptionStatus.on_hold.value
            )

            effective_status = modified_user.status if modified_user.status is not None else db_user.status
            effective_expire = modified_user.expire
            effective_on_hold_expire_duration = modified_user.on_hold_expire_duration
            if status_was_changed and status_requires_finite_expire:
                if modified_status_value == SubscriptionStatus.on_hold.value:
                    effective_expire = None
                    if effective_on_hold_expire_duration is None:
                        effective_on_hold_expire_duration = db_user.on_hold_expire_duration
                elif effective_expire is None:
                    effective_expire = db_user.expire

            await self._enforce_subscription_limits(
                db,
                admin,
                data_limit=modified_user.data_limit,
                expire=effective_expire,
                status=effective_status,
                on_hold_expire_duration=effective_on_hold_expire_duration,
                on_hold_timeout=modified_user.on_hold_timeout,
                hwid_limit=effective_hwid_limit,
                data_limit_reset_strategy=modified_user.data_limit_reset_strategy,
                next_plan=modified_user.next_plan,
                require_finite_data_limit=data_limit_was_changed,
                require_finite_expire=expire_requires_finite_limit,
                require_finite_on_hold_timeout=on_hold_timeout_requires_finite_limit,
                require_finite_hwid_limit=hwid_limit_was_changed,
            )

        if (
            hwid_limit_was_changed
            and effective_hwid_limit is not None
            and not admin.is_owner
            and effective_hwid_conf is not None
        ):
            if effective_hwid_conf.min_limit is not None and effective_hwid_limit < effective_hwid_conf.min_limit:
                await self.raise_error(
                    message=f"HWID limit cannot be less than {effective_hwid_conf.min_limit}", code=400, db=db
                )
            if (
                effective_hwid_conf.max_limit is not None
                and effective_hwid_conf.max_limit > 0
                and (effective_hwid_limit > effective_hwid_conf.max_limit or effective_hwid_limit == 0)
            ):
                await self.raise_error(
                    message=f"HWID limit cannot exceed {effective_hwid_conf.max_limit}", code=400, db=db
                )

        validated_groups = None
        if modified_user.group_ids is not None:
            validated_groups = await self.validate_all_groups(
                db,
                modified_user,
                admin,
                existing_group_ids={group.id for group in db_user.groups},
            )

        if modified_user.next_plan is not None and modified_user.next_plan.subscription_template_id is not None:
            await self._get_validated_template_with_access(db, modified_user.next_plan.subscription_template_id, admin)

        effective_groups = validated_groups if validated_groups is not None else db_user.groups
        current_proxy_settings = ProxyTable.model_validate(db_user.proxy_settings)
        current_proxy_settings_data = current_proxy_settings.dict()
        proxy_settings_to_prepare = (
            ProxyTable.model_validate(modified_user.proxy_settings.dict())
            if modified_user.proxy_settings is not None
            else ProxyTable.model_validate(current_proxy_settings_data)
        )

        # peer IPs are pool-managed: whatever the client sent, keep the stored allocation
        proxy_settings_to_prepare.wireguard.peer_ips = list(current_proxy_settings.wireguard.peer_ips or [])

        prepared_proxy_settings = await self._prepare_subscription_proxy_settings(
            db,
            effective_groups,
            proxy_settings_to_prepare,
            exclude_subscription_uuid=db_user.uuid,
        )
        if modified_user.proxy_settings is not None or prepared_proxy_settings.dict() != current_proxy_settings_data:
            modified_user.proxy_settings = prepared_proxy_settings

        return validated_groups

    async def _apply_modified_subscription(
        self,
        db: AsyncSession,
        db_user: Subscription,
        modified_user: SubscriptionModify,
        admin: AdminDetails,
        *,
        validated_groups=None,
        before: tuple | None = None,
        core_policy: bool = False,
    ) -> SubscriptionNotificationResponse:
        self._require_core_policy_context(db_user, core_policy=core_policy)
        old_status = db_user.status
        if before is None:
            before = await node_payload_signature(db_user)

        self._apply_explicit_null_hwid_limit(db_user, modified_user)
        try:
            db_user = await crud_modify_user(db, db_user, modified_user, groups=validated_groups)
        except ValueError as exc:  # WireGuard subnet exhausted
            await self.raise_error(message=str(exc), code=400, db=db)
        if await node_payload_signature(db_user) == before:
            # Nothing a node receives changed (credentials, inbounds from
            # enabled groups, status incl. implicit flips): no fan-out to nodes.
            user = await self.validate_subscription(db_user)
        else:
            user = await self.update_subscription(db_user)

        logger.info(f'Subscription "{user.uuid}" with id "{db_user.uuid}" modified by admin "{admin.username}"')

        asyncio.create_task(notification.modify_subscription(user, admin))

        if user.status != old_status:
            asyncio.create_task(notification.subscription_status_change(user, admin))

            old_status_value = getattr(old_status, "value", old_status)
            new_status_value = getattr(user.status, "value", user.status)
            logger.info(
                f'Subscription "{db_user.uuid}" status changed from "{old_status_value}" to "{new_status_value}"'
            )

        return user

    async def _set_subscription_disabled(
        self,
        db: AsyncSession,
        db_user: Subscription,
        toggle: SubscriptionStatusToggle,
        admin: AdminDetails,
    ) -> SubscriptionResponse:
        self._require_core_policy_context(db_user)
        old_status = db_user.status
        new_status = SubscriptionStatus.disabled if toggle.disabled else _resolve_enabled_subscription_status(db_user)

        if db_user.status != new_status:
            changed_at = dt.now(UTC)
            if new_status == SubscriptionStatus.on_hold:
                # Back on hold: like an edit, it makes earlier traffic not count as coming online.
                db_user.edit_at = changed_at
            db_user.status = new_status
            db_user.last_status_change = changed_at
            await db.commit()
            await load_subscription_attrs(db_user, load_admin_role=True)

        user = await self.update_subscription(db_user)

        if user.status != old_status:
            asyncio.create_task(notification.subscription_status_change(user, admin))
            old_status_value = getattr(old_status, "value", old_status)
            new_status_value = getattr(user.status, "value", user.status)
            logger.info(f'Subscription "{user.uuid}" status changed from "{old_status_value}" to "{new_status_value}"')

        return user

    async def _modify_subscription(
        self,
        db: AsyncSession,
        db_user: Subscription,
        modified_user: SubscriptionModify,
        admin: AdminDetails,
        *,
        skip_role_limits: bool = False,
        core_policy: bool = False,
    ) -> SubscriptionNotificationResponse:
        self._require_core_policy_context(db_user, core_policy=core_policy)
        if not skip_role_limits:
            await self._enforce_manual_subscription_write_access(admin, db)

        # Canonical node payload before anything is prepared or written.
        before = await node_payload_signature(db_user)
        validated_groups = await self._prepare_modified_subscription(
            db, db_user, modified_user, admin, skip_role_limits=skip_role_limits
        )
        return await self._apply_modified_subscription(
            db,
            db_user,
            modified_user,
            admin,
            validated_groups=validated_groups,
            before=before,
            core_policy=core_policy,
        )

    async def modify_subscription_by_uuid(
        self,
        db: AsyncSession,
        subscription_uuid: UUID,
        modified_user: SubscriptionModify,
        admin: AdminDetails,
        *,
        core_policy: bool = False,
    ) -> SubscriptionResponse:
        db_user = await self.get_validated_subscription_by_uuid(db, subscription_uuid, admin, scope_action="update")
        return await self._modify_subscription(db, db_user, modified_user, admin, core_policy=core_policy)

    async def set_subscription_disabled_by_uuid(
        self, db: AsyncSession, subscription_uuid: UUID, toggle: SubscriptionStatusToggle, admin: AdminDetails
    ) -> SubscriptionResponse:
        db_user = await self.get_validated_subscription_by_uuid(db, subscription_uuid, admin, scope_action="update")
        return await self._set_subscription_disabled(db, db_user, toggle, admin)

    async def _remove_subscription(self, db: AsyncSession, db_user: Subscription, admin: AdminDetails) -> dict:
        user = await self.validate_subscription(db_user, include_subscription_url=False)
        await remove_subscription(db, db_user)
        await sync_remove_subscription(user)

        asyncio.create_task(notification.remove_subscription(user, admin))
        logger.info(f'Subscription "{db_user.uuid}" with id "{db_user.uuid}" deleted by admin "{admin.username}"')
        return {}

    async def remove_subscription_by_uuid(self, db: AsyncSession, subscription_uuid: UUID, admin: AdminDetails):
        db_user = await self.get_validated_subscription_by_uuid(db, subscription_uuid, admin, scope_action="delete")
        return await self._remove_subscription(db, db_user, admin)

    async def _get_validated_subscriptions_by_uuids(
        self,
        db: AsyncSession,
        subscription_uuids: list[UUID] | set[UUID],
        admin: AdminDetails,
        *,
        load_admin: bool = True,
        load_next_plan: bool = True,
        load_usage_logs: bool = True,
        load_groups: bool = True,
        load_admin_role: bool = False,
        scope_action: str = "read",
    ) -> list[Subscription]:
        """Validate the entire selection against scope, using aggregates when history is omitted."""
        if not subscription_uuids:
            return []

        ids_list = list(subscription_uuids)

        # Replicate the scope filter that get_validated_subscription_by_uuid applies per-user.
        scope_admin_id = get_scope_admin_id(admin, "subscriptions", scope_action)
        query = SubscriptionListQuery(
            uuids=ids_list,
            admin_ids=[scope_admin_id] if scope_admin_id is not None else None,
            limit=len(ids_list),
        )
        users = await get_subscriptions(
            db,
            query=query,
            load_admin_role=load_admin_role,
            load_usage_logs=load_usage_logs,
            load_lifetime_used_traffic=not load_usage_logs,
        )

        # Verify every requested ID was found (mirrors the 404 in get_validated_subscription_by_uuid)
        found_ids = {user.uuid for user in users}
        missing = set(ids_list) - found_ids
        if missing:
            await self.raise_error(message="Subscription not found", code=404)

        return users

    @staticmethod
    def _build_bulk_action_response(
        users: list[Subscription | SubscriptionNotificationResponse],
    ) -> BulkSubscriptionsActionResponse:
        """Summarize the users returned by a completed bulk action."""
        uuids = [user.uuid for user in users]
        return BulkSubscriptionsActionResponse(uuids=uuids, count=len(uuids))

    async def _load_subscriptions_by_uuids(
        self, db: AsyncSession, subscription_uuids: list[UUID]
    ) -> list[Subscription]:
        """Refresh bulk results and node-sync relationships without materializing reset history."""
        return await get_subscriptions_by_uuid_batch(
            db,
            subscription_uuids,
            load_admin_role=True,
            load_group_inbounds=True,
            load_usage_logs=False,
            load_lifetime_used_traffic=True,
            populate_existing=True,
        )

    async def bulk_remove_subscriptions(
        self, db: AsyncSession, bulk_users: BulkSubscriptionsSelection, admin: AdminDetails
    ) -> RemoveSubscriptionsResponse:
        db_users = await self._get_validated_subscriptions_by_uuids(db, bulk_users.uuids, admin, scope_action="delete")
        users = [await self.validate_subscription(db_user, include_subscription_url=False) for db_user in db_users]

        await remove_subscriptions(db, db_users)

        for user in users:
            await sync_remove_subscription(user)
            asyncio.create_task(notification.remove_subscription(user, admin))
            logger.info(f'Subscription "{user.uuid}" with id "{user.uuid}" deleted by admin "{admin.username}"')

        return RemoveSubscriptionsResponse(uuids=[user.uuid for user in users], count=len(users))

    async def _reset_subscription_data_usage(
        self,
        db: AsyncSession,
        db_user: Subscription,
        admin: AdminDetails,
        *,
        clean_chart_data: bool | None = None,
        emit_status_change_notification: bool = True,
        core_policy: bool = False,
    ):
        self._require_core_policy_context(db_user, core_policy=core_policy)
        old_status = db_user.status

        if clean_chart_data is None:
            clean_chart_data = usage_settings.reset_user_usage_clean_chart_data

        db_user = await reset_subscription_data_usage(db=db, db_user=db_user, clean_chart_data=clean_chart_data)
        user = await self.update_subscription(db_user)

        if emit_status_change_notification and user.status != old_status:
            asyncio.create_task(notification.subscription_status_change(user, admin))

        asyncio.create_task(notification.reset_subscription_data_usage(user, admin))

        logger.info(f'Subscription "{db_user.uuid}" usage was reset by admin "{admin.username}"')

        return user

    async def reset_subscription_data_usage_by_uuid(
        self, db: AsyncSession, subscription_uuid: UUID, admin: AdminDetails, *, core_policy: bool = False
    ):
        db_user = await self.get_validated_subscription_by_uuid(
            db, subscription_uuid, admin, scope_action="reset_usage"
        )
        return await self._reset_subscription_data_usage(db, db_user, admin, core_policy=core_policy)

    async def bulk_reset_subscription_data_usage(
        self,
        db: AsyncSession,
        bulk_users: BulkSubscriptionsSelection,
        admin: AdminDetails,
        *,
        core_policy: bool = False,
    ) -> BulkSubscriptionsActionResponse:
        db_users = await self._get_validated_subscriptions_by_uuids(
            db, bulk_users.uuids, admin, load_usage_logs=False, scope_action="reset_usage"
        )
        for db_user in db_users:
            self._require_core_policy_context(db_user, core_policy=core_policy)
        old_statuses = {user.uuid: user.status for user in db_users}

        db_users = await bulk_reset_subscription_data_usage(
            db,
            db_users,
            clean_chart_data=usage_settings.reset_user_usage_clean_chart_data,
        )
        await sync_subscriptions(db_users)

        users = [await self.validate_subscription(db_user) for db_user in db_users]
        for user in users:
            if user.status != old_statuses[user.uuid]:
                asyncio.create_task(notification.subscription_status_change(user, admin))
            asyncio.create_task(notification.reset_subscription_data_usage(user, admin))
            logger.info(f'Subscription "{user.uuid}" usage was reset by admin "{admin.username}"')

        return self._build_bulk_action_response(users)

    async def _revoke_subscription_sub(
        self, db: AsyncSession, db_user: Subscription, admin: AdminDetails
    ) -> SubscriptionResponse:
        proxy_settings = await self._prepare_revoked_proxy_settings(db, db_user)
        db_user = await revoke_subscription_sub(db=db, db_user=db_user, proxy_settings=proxy_settings.dict())
        user = await self.update_subscription(db_user)

        asyncio.create_task(notification.subscription_access_revoked(user, admin))
        logger.info(f'Subscription "{db_user.uuid}" subscription was revoked by admin "{admin.username}"')

        return user

    async def revoke_subscription_sub_by_uuid(
        self, db: AsyncSession, subscription_uuid: UUID, admin: AdminDetails
    ) -> SubscriptionResponse:
        db_user = await self.get_validated_subscription_by_uuid(
            db, subscription_uuid, admin, load_usage_logs=False, scope_action="revoke_sub"
        )
        return await self._revoke_subscription_sub(db, db_user, admin)

    async def bulk_revoke_subscription_sub(
        self, db: AsyncSession, bulk_users: BulkSubscriptionsSelection, admin: AdminDetails
    ) -> BulkSubscriptionsActionResponse:
        db_users = await self._get_validated_subscriptions_by_uuids(
            db, bulk_users.uuids, admin, load_usage_logs=False, scope_action="revoke_sub"
        )

        proxy_settings_by_subscription_uuid = {
            db_user.uuid: (await self._prepare_revoked_proxy_settings(db, db_user)).dict() for db_user in db_users
        }
        db_users = await bulk_revoke_subscription_sub(
            db, db_users, proxy_settings_by_subscription_uuid=proxy_settings_by_subscription_uuid
        )
        await sync_subscriptions(db_users)

        users = [await self.validate_subscription(db_user) for db_user in db_users]
        for user in users:
            asyncio.create_task(notification.subscription_access_revoked(user, admin))
            logger.info(f'Subscription "{user.uuid}" subscription was revoked by admin "{admin.username}"')

        return self._build_bulk_action_response(users)

    async def _bulk_set_subscription_status(
        self, db: AsyncSession, db_users: list[Subscription], status: SubscriptionStatus, admin: AdminDetails
    ) -> list[SubscriptionNotificationResponse]:
        if not db_users:
            return []

        prepared_updates = []
        for db_user in db_users:
            original_status = db_user.status
            modified_user = SubscriptionModify(status=status)
            validated_groups = await self._prepare_modified_subscription(db, db_user, modified_user, admin)
            prepared_updates.append((db_user, modified_user, validated_groups, original_status))

        modified_subscription_uuids: list[UUID] = []
        try:
            for db_user, modified_user, validated_groups, _ in prepared_updates:
                await crud_modify_user(
                    db,
                    db_user,
                    modified_user,
                    groups=validated_groups,
                    commit=False,
                )
                if db_user.uuid is not None:
                    modified_subscription_uuids.append(db_user.uuid)

            await db.commit()
        except Exception:
            await db.rollback()
            raise

        modified_db_users = await self._load_subscriptions_by_uuids(db, modified_subscription_uuids)
        await sync_subscriptions(modified_db_users)

        users = [await self.validate_subscription(db_user) for db_user in modified_db_users]
        users_by_id = {user.uuid: user for user in users}
        for db_user, _, _, original_status in prepared_updates:
            user = users_by_id.get(db_user.uuid)
            if user is None:
                continue
            asyncio.create_task(notification.modify_subscription(user, admin))
            logger.info(f'Subscription "{user.uuid}" with id "{user.uuid}" modified by admin "{admin.username}"')
            if user.status != original_status:
                asyncio.create_task(notification.subscription_status_change(user, admin))
                old_status_value = getattr(original_status, "value", original_status)
                new_status_value = getattr(user.status, "value", user.status)
                logger.info(
                    f'Subscription "{user.uuid}" status changed from "{old_status_value}" to "{new_status_value}"'
                )

        return users

    async def _bulk_set_subscriptions_disabled(
        self,
        db: AsyncSession,
        db_users: list[Subscription],
        disabled: bool,
        admin: AdminDetails,
        *,
        core_policy: bool = False,
    ) -> list[SubscriptionNotificationResponse]:
        if not db_users:
            return []
        for db_user in db_users:
            self._require_core_policy_context(db_user, core_policy=core_policy)

        changed_subscription_uuids: list[UUID] = []
        original_statuses: dict[UUID, SubscriptionStatus] = {}
        changed_at = dt.now(UTC)

        try:
            for db_user in db_users:
                new_status = SubscriptionStatus.disabled if disabled else _resolve_enabled_subscription_status(db_user)
                if db_user.status == new_status:
                    continue
                if db_user.uuid is None:
                    continue
                original_statuses[db_user.uuid] = db_user.status
                if new_status == SubscriptionStatus.on_hold:
                    # Back on hold: like an edit, it makes earlier traffic not count as coming online.
                    db_user.edit_at = changed_at
                db_user.status = new_status
                db_user.last_status_change = changed_at
                changed_subscription_uuids.append(db_user.uuid)

            await db.commit()
        except Exception:
            await db.rollback()
            raise

        if changed_subscription_uuids:
            changed_db_users = await self._load_subscriptions_by_uuids(db, changed_subscription_uuids)
            await sync_subscriptions(changed_db_users)
            users = [await self.validate_subscription(db_user) for db_user in changed_db_users]
        else:
            users = []

        for user in users:
            original_status = original_statuses.get(user.uuid)
            if original_status is None or user.status == original_status:
                continue
            asyncio.create_task(notification.subscription_status_change(user, admin))
            old_status_value = getattr(original_status, "value", original_status)
            new_status_value = getattr(user.status, "value", user.status)
            logger.info(f'Subscription "{user.uuid}" status changed from "{old_status_value}" to "{new_status_value}"')

        return users

    async def bulk_disable_subscriptions(
        self,
        db: AsyncSession,
        bulk_users: BulkSubscriptionsSelection,
        admin: AdminDetails,
        *,
        core_policy: bool = False,
    ) -> BulkSubscriptionsActionResponse:
        db_users = await self._get_validated_subscriptions_by_uuids(
            db, bulk_users.uuids, admin, load_usage_logs=False, scope_action="update"
        )
        for db_user in db_users:
            self._require_core_policy_context(db_user, core_policy=core_policy)
        users_to_disable = [db_user for db_user in db_users if db_user.status != SubscriptionStatus.disabled]

        users = await self._bulk_set_subscriptions_disabled(db, users_to_disable, True, admin, core_policy=core_policy)

        return self._build_bulk_action_response(users)

    async def bulk_enable_subscriptions(
        self,
        db: AsyncSession,
        bulk_users: BulkSubscriptionsSelection,
        admin: AdminDetails,
        *,
        core_policy: bool = False,
    ) -> BulkSubscriptionsActionResponse:
        db_users = await self._get_validated_subscriptions_by_uuids(
            db, bulk_users.uuids, admin, load_usage_logs=False, scope_action="update"
        )
        for db_user in db_users:
            self._require_core_policy_context(db_user, core_policy=core_policy)
        users_to_enable = [db_user for db_user in db_users if db_user.status == SubscriptionStatus.disabled]

        users = await self._bulk_set_subscriptions_disabled(db, users_to_enable, False, admin, core_policy=core_policy)

        return self._build_bulk_action_response(users)

    async def reset_subscriptions_data_usage(self, db: AsyncSession, admin: AdminDetails):
        """Reset all users data usage — requires scope=all, resets every user."""
        await reset_all_subscriptions_data_usage(
            db=db,
            admin=None,  # None = all admins, not filtered by owner
            clean_chart_data=usage_settings.reset_user_usage_clean_chart_data,
        )

    async def _active_next_plan(
        self, db: AsyncSession, db_user: Subscription, admin: AdminDetails
    ) -> SubscriptionResponse:
        self._require_core_policy_context(db_user)
        if db_user is None or db_user.next_plan is None:
            await self.raise_error(message="Subscription doesn't have next plan", code=404)

        old_status = db_user.status
        db_user = await reset_subscription_by_next(
            db=db,
            db_user=db_user,
            clean_chart_data=usage_settings.reset_user_usage_clean_chart_data,
        )
        user = await self.update_subscription(db_user)

        if user.status != old_status:
            asyncio.create_task(notification.subscription_status_change(user, admin))

        asyncio.create_task(notification.subscription_data_reset_by_next(user, admin))
        logger.info(f'Subscription "{db_user.uuid}"\'s usage was reset by next plan by admin "{admin.username}"')
        return user

    async def active_next_plan_by_uuid(
        self, db: AsyncSession, subscription_uuid: UUID, admin: AdminDetails
    ) -> SubscriptionResponse:
        db_user = await self.get_validated_subscription_by_uuid(
            db, subscription_uuid, admin, scope_action="activate_next_plan"
        )
        return await self._active_next_plan(db, db_user, admin)

    async def _set_owner(
        self, db: AsyncSession, db_user: Subscription, new_admin, admin: AdminDetails
    ) -> SubscriptionResponse:
        self._require_core_policy_context(db_user)
        db_user = await set_owner(db, db_user, new_admin)
        user = await self.validate_subscription(db_user)
        logger.info(
            f'Subscription "{user.uuid}" owner successfully set to "{new_admin.username}" by admin "{admin.username}"'
        )
        return user

    async def set_owner_by_uuid(
        self, db: AsyncSession, subscription_uuid: UUID, admin_username: str, admin: AdminDetails
    ) -> SubscriptionResponse:
        new_admin = await self.get_validated_admin(db, username=admin_username)
        db_user = await self.get_validated_subscription_by_uuid(db, subscription_uuid, admin, scope_action="set_owner")
        return await self._set_owner(db, db_user, new_admin, admin)

    async def bulk_set_owner(
        self, db: AsyncSession, bulk_users: BulkSubscriptionsSetOwner, admin: AdminDetails
    ) -> BulkSubscriptionsActionResponse:
        new_admin = await self.get_validated_admin(db, username=bulk_users.admin_username)
        db_users = await self._get_validated_subscriptions_by_uuids(
            db, bulk_users.uuids, admin, load_usage_logs=False, scope_action="set_owner"
        )
        for db_user in db_users:
            self._require_core_policy_context(db_user)

        db_users = await bulk_set_owner(db, db_users, new_admin)
        users = [await self.validate_subscription(db_user) for db_user in db_users]
        for user in users:
            logger.info(
                f'Subscription "{user.uuid}" owner successfully set to "{new_admin.username}" by admin "{admin.username}"'
            )

        return self._build_bulk_action_response(users)

    async def _get_subscription_usage(
        self,
        db: AsyncSession,
        db_user: Subscription,
        admin: AdminDetails,
        start: dt | None = None,
        end: dt | None = None,
        period: Period = Period.hour,
        node_id: int | None = None,
        group_by_node: bool = False,
    ) -> SubscriptionUsageStatsList:
        start, end = await self.validate_dates(start, end, True)

        if not _has_permission(admin, "nodes", "stats"):
            node_id = None
            group_by_node = False

        return await get_subscription_usages(
            db, db_user.uuid, start, end, period, node_id=node_id, group_by_node=group_by_node
        )

    async def get_subscription_usage_by_uuid(
        self,
        db: AsyncSession,
        subscription_uuid: UUID,
        admin: AdminDetails,
        query: SubscriptionUsageQuery,
    ) -> SubscriptionUsageStatsList:
        db_user = await self.get_validated_subscription_by_uuid(db, subscription_uuid, admin)
        return await self._get_subscription_usage(
            db,
            db_user,
            admin,
            query.start,
            query.end,
            query.period,
            query.node_id,
            query.group_by_node,
        )

    async def get_subscription_by_uuid(
        self, db: AsyncSession, subscription_uuid: UUID, admin: AdminDetails
    ) -> SubscriptionNotificationResponse:
        db_user = await self.get_validated_subscription_by_uuid(
            db,
            subscription_uuid,
            admin,
            load_usage_logs=False,
            join_groups=True,
            load_lifetime_used_traffic=True,
        )
        return await self.validate_subscription(db_user)

    async def get_subscriptions(
        self,
        db: AsyncSession,
        admin: AdminDetails,
        query: SubscriptionListQuery,
    ) -> SubscriptionsResponse:
        """Get subscriptions within the administrator's authorized scope."""
        # Enforce the permission here as well as at the HTTP boundary to protect internal callers.
        try:
            enforce_permission(admin, "subscriptions", "read")
        except PermissionDenied as exc:
            await self.raise_error(message=str(exc), code=403)
        scope_admin_id = get_scope_admin_id(admin, "subscriptions", "read")
        if scope_admin_id is not None:
            query = query.model_copy(update={"owner": [admin.username], "admin_ids": None})

        # Device counts are HWID data: without hwids.read they are not loaded and the sort is
        # ignored, the same way node filters are ignored without nodes.stats.
        can_read_hwids = _has_permission(admin, "hwids", "read")
        if not can_read_hwids and any(option.field == SubscriptionSortField.hwid_count for option in query.sort):
            query = query.model_copy(
                update={"sort": [option for option in query.sort if option.field != SubscriptionSortField.hwid_count]}
            )

        users, count = await get_subscriptions(
            db=db,
            query=query,
            return_with_count=True,
            load_usage_logs=False,
            load_lifetime_used_traffic=True,
            load_hwid_count=can_read_hwids,
        )

        if query.load_sub:
            tasks = [self.generate_subscription_url(user, user) for user in users]
            urls = await asyncio.gather(*tasks)

            for user, url in zip(users, urls):
                user.subscription_url = url

        response = SubscriptionsResponse(users=users, total=count)

        return response

    async def get_subscriptions_simple(
        self,
        db: AsyncSession,
        admin: AdminDetails,
        query: SubscriptionSimpleListQuery,
    ) -> SubscriptionsSimpleResponse:
        """Get UUID-only lightweight subscription list."""
        scope_admin_id = get_scope_admin_id(admin, "subscriptions", "read_simple")
        admin_filter = (
            None
            if scope_admin_id is None
            else await get_admin(db, admin.username, load_users=False, load_usage_logs=False)
        )

        # Call CRUD function
        rows, total = await get_subscriptions_simple(
            db=db,
            query=query,
            admin=admin_filter,
        )

        # Convert tuples to Pydantic models
        subscriptions = [SubscriptionSimple(uuid=row[0]) for row in rows]

        return SubscriptionsSimpleResponse(subscriptions=subscriptions, total=total)

    async def get_subscriptions_usage(
        self,
        db: AsyncSession,
        admin: AdminDetails,
        query: SubscriptionsUsageBreakdownQuery,
    ) -> SubscriptionUsageStatsList:
        """Get all users usage"""
        start, end = await self.validate_dates(query.start, query.end, True)
        node_id = query.node_id
        core_id = query.core_id
        group_by_node = query.group_by_node

        if group_by_node and query.group_by_admin:
            await self.raise_error(message="group_by_node and group_by_admin can't be used together", code=400)

        can_use_node_scope = _has_permission(admin, "nodes", "stats")
        if not can_use_node_scope:
            node_id = None
            core_id = None
            group_by_node = False

        admins_filter = await _resolve_subscriptions_usage_admins_filter(self, db, admin, query.owner)

        return await get_all_subscriptions_usages(
            db=db,
            start=start,
            end=end,
            period=query.period,
            node_id=node_id,
            core_id=core_id,
            admins=admins_filter,
            group_by_node=group_by_node,
            group_by_admin=query.group_by_admin,
        )

    async def get_subscriptions_count_metric(
        self,
        db: AsyncSession,
        admin: AdminDetails,
        metric: SubscriptionCountMetric,
        query: SubscriptionsUsageQuery,
    ) -> SubscriptionCountMetricStatsList:
        """Get one users activity/status count metric from usage rows."""
        start, end = await self.validate_dates(query.start, query.end, True)
        node_id = query.node_id
        group_by_node = query.group_by_node

        can_use_node_scope = _has_permission(admin, "nodes", "stats")
        if not can_use_node_scope:
            node_id = None
            group_by_node = False

        try:
            validate_user_count_metric_scope(metric, node_id=node_id, group_by_node=group_by_node)
        except ValueError as exc:
            await self.raise_error(message=str(exc), code=400)

        admins_filter = await _resolve_subscriptions_usage_admins_filter(self, db, admin, query.owner)

        return await get_subscription_count_metric_stats(
            db=db,
            admins=admins_filter,
            start=start,
            end=end,
            period=query.period,
            metric=metric,
            node_id=node_id,
            group_by_node=group_by_node,
        )

    @staticmethod
    async def remove_subscriptions_logger(users: list[str], by: str):
        for user in users:
            logger.info(f'Subscription "{user}" deleted by admin "{by}"')

    async def get_expired_subscriptions(
        self,
        db: AsyncSession,
        query: ExpiredSubscriptionsQuery,
    ) -> list[str]:
        """
        Get users who match the cleanup target within the specified date range.

        - **target**: `expired` | `limited` | `on_hold` | `disabled` | `inactive`
        - **expired_after** / **expired_before** UTC datetime (optional)
        - For `expired`: filters by expiration date.
        - For `limited` / `on_hold` / `disabled`: filters by last_status_change.
        - For `inactive`: filters by last activity (online_at, or created_at if never online).
        - If both dates are omitted, returns all users matching target.
        """

        expired_after, expired_before = await self.validate_dates(query.expired_after, query.expired_before, False)
        if query.admin_username:
            admin_id = (await self.get_validated_admin(db, query.admin_username)).id
        else:
            admin_id = None
        users = await get_expired_subscriptions(
            db,
            query=query.model_copy(update={"expired_after": expired_after, "expired_before": expired_before}),
            admin_id=admin_id,
        )
        return [str(row.uuid) for row in users]

    async def delete_expired_subscriptions(
        self,
        db: AsyncSession,
        admin: AdminDetails,
        query: ExpiredSubscriptionsQuery,
    ) -> RemoveSubscriptionsResponse:
        """
        Delete users who match the cleanup target within the specified date range.

        - **target**: `expired` | `limited` | `on_hold` | `disabled` | `inactive`
        - **expired_after** / **expired_before** UTC datetime (optional)
        - For `expired`: filters by expiration date.
        - For `limited` / `on_hold` / `disabled`: filters by last_status_change.
        - For `inactive`: filters by last activity (online_at, or created_at if never online).
        - **dry_run**: if true, returns the list of users that would be deleted without deleting them.
        """

        expired_after, expired_before = await self.validate_dates(query.expired_after, query.expired_before, False)

        if query.admin_username:
            admin_id = (await self.get_validated_admin(db, query.admin_username)).id
        else:
            admin_id = None
        uuid_list, node_users = await remove_expired_subscriptions(
            db,
            expired_after,
            expired_before,
            admin_id,
            target=query.target,
            dry_run=query.dry_run,
        )
        if not query.dry_run:
            # Active and on_hold users are still on the nodes (the inactive target includes them).
            await sync_remove_subscriptions(node_users)
            await self.remove_subscriptions_logger(users=[str(u) for u in uuid_list], by=admin.username)

        return RemoveSubscriptionsResponse(uuids=uuid_list, count=len(uuid_list))

    @staticmethod
    def load_base_subscription_args(template: SubscriptionTemplate) -> dict:
        user_args = {
            "data_limit": template.data_limit,
            "group_ids": template.group_ids,
            "data_limit_reset_strategy": template.data_limit_reset_strategy,
            "status": template.status,
            "hwid_limit": template.hwid_limit,
            "next_plan": None,
        }

        if template.status == SubscriptionStatus.active:
            if template.expire_duration:
                user_args["expire"] = dt.now(UTC) + td(seconds=template.expire_duration)
            else:
                user_args["expire"] = 0
        else:
            user_args["expire"] = 0
            user_args["on_hold_expire_duration"] = template.expire_duration
            if template.on_hold_timeout:
                user_args["on_hold_timeout"] = dt.now(UTC) + td(seconds=template.on_hold_timeout)
            else:
                user_args["on_hold_timeout"] = 0

        return user_args

    @staticmethod
    def apply_settings(
        user_args: SubscriptionCreate | SubscriptionModify, template: SubscriptionTemplate
    ) -> SubscriptionCreate | SubscriptionModify:
        if template.extra_settings:
            method = template.extra_settings.get("method", None)

            if method is not None:
                user_args.proxy_settings.shadowsocks.method = method

        return user_args

    async def _modify_subscription_with_template(
        self,
        db: AsyncSession,
        db_user: Subscription,
        modified_template: ModifySubscriptionByTemplate,
        admin: AdminDetails,
    ) -> SubscriptionResponse:
        self._require_core_policy_context(db_user)
        original_status = db_user.status
        subscription_template = await self._get_validated_template_with_access(
            db, modified_template.subscription_template_id, admin
        )

        if subscription_template.is_disabled:
            await self.raise_error("this template is disabled", 403)

        user_args = self.load_base_subscription_args(subscription_template)
        user_args["proxy_settings"] = db_user.proxy_settings

        try:
            modify_subscription = SubscriptionModify(**user_args, note=modified_template.note)
        except ValidationError as e:
            error_messages = "; ".join([f"{err['loc'][0]}: {err['msg']}" for err in e.errors()])
            await self.raise_error(message=error_messages, code=400)

        modify_subscription = self.apply_settings(modify_subscription, subscription_template)
        validated_groups = await self._prepare_modified_subscription(
            db, db_user, modify_subscription, admin, skip_role_limits=True
        )

        if subscription_template.reset_usages:
            suppress_reset_status_change = (
                subscription_template.status == SubscriptionStatus.on_hold
                and original_status != SubscriptionStatus.active
            )
            await self._reset_subscription_data_usage(
                db,
                db_user,
                admin,
                emit_status_change_notification=not suppress_reset_status_change,
            )

        return await self._apply_modified_subscription(
            db, db_user, modify_subscription, admin, validated_groups=validated_groups
        )

    async def modify_subscription_with_template_by_uuid(
        self,
        db: AsyncSession,
        subscription_uuid: UUID,
        modified_template: ModifySubscriptionByTemplate,
        admin: AdminDetails,
    ) -> SubscriptionResponse:
        db_user = await self.get_validated_subscription_by_uuid(db, subscription_uuid, admin, scope_action="update")
        return await self._modify_subscription_with_template(db, db_user, modified_template, admin)

    async def bulk_apply_template_to_subscriptions(
        self,
        db: AsyncSession,
        body: BulkSubscriptionsApplyTemplate,
        admin: AdminDetails,
    ) -> BulkSubscriptionsActionResponse:
        db_users = await self._get_validated_subscriptions_by_uuids(
            db, body.uuids, admin, load_usage_logs=False, scope_action="update"
        )
        for db_user in db_users:
            self._require_core_policy_context(db_user)
        subscription_template = await self._get_validated_template_with_access(db, body.subscription_template_id, admin)

        if subscription_template.is_disabled:
            await self.raise_error("this template is disabled", 403)

        prepared_updates = []
        for db_user in db_users:
            original_status = db_user.status
            user_args = self.load_base_subscription_args(subscription_template)
            user_args["proxy_settings"] = db_user.proxy_settings

            try:
                modify_subscription = SubscriptionModify(**user_args, note=body.note)
            except ValidationError as e:
                error_messages = "; ".join([f"{err['loc'][0]}: {err['msg']}" for err in e.errors()])
                await self.raise_error(message=error_messages, code=400)

            modify_subscription = self.apply_settings(modify_subscription, subscription_template)
            validated_groups = await self._prepare_modified_subscription(
                db, db_user, modify_subscription, admin, skip_role_limits=True
            )
            emit_reset_status_change = not (
                subscription_template.status == SubscriptionStatus.on_hold
                and original_status != SubscriptionStatus.active
            )
            prepared_updates.append(
                (db_user, modify_subscription, validated_groups, original_status, emit_reset_status_change)
            )

        modified_subscription_uuids: list[UUID] = []
        try:
            if subscription_template.reset_usages:
                # The modify below allocates WireGuard IPs, which locks the pool rows before user rows everywhere
                # else: take them before the reset locks the users, or a concurrent modify can deadlock with us
                await ensure_wireguard_subnet_pools(db)
                # One set-based reset for all users; it reloads them, so each one below is edited from fresh state
                await bulk_reset_subscription_data_usage(
                    db,
                    [db_user for db_user, *_ in prepared_updates],
                    clean_chart_data=usage_settings.reset_user_usage_clean_chart_data,
                    commit=False,
                )

            for db_user, modified_user_model, validated_groups, _, _ in prepared_updates:
                self._apply_explicit_null_hwid_limit(db_user, modified_user_model)
                await crud_modify_user(
                    db,
                    db_user,
                    modified_user_model,
                    groups=validated_groups,
                    commit=False,
                )
                if db_user.uuid is not None:
                    modified_subscription_uuids.append(db_user.uuid)

            await db.commit()
        except Exception:
            await db.rollback()
            raise

        modified_db_users = await self._load_subscriptions_by_uuids(db, modified_subscription_uuids)
        await sync_subscriptions(modified_db_users)

        users = [await self.validate_subscription(db_user) for db_user in modified_db_users]
        users_by_id = {user.uuid: user for user in users}
        for db_user, _, _, original_status, emit_reset_status_change in prepared_updates:
            user = users_by_id.get(db_user.uuid)
            if user is None:
                continue
            status_notification_sent = False
            if subscription_template.reset_usages:
                if emit_reset_status_change and user.status != original_status:
                    asyncio.create_task(notification.subscription_status_change(user, admin))
                    status_notification_sent = True
                asyncio.create_task(notification.reset_subscription_data_usage(user, admin))
                logger.info(f'Subscription "{user.uuid}" usage was reset by admin "{admin.username}"')
            asyncio.create_task(notification.modify_subscription(user, admin))
            logger.info(f'Subscription "{user.uuid}" with id "{user.uuid}" modified by admin "{admin.username}"')
            if user.status != original_status:
                if not status_notification_sent:
                    asyncio.create_task(notification.subscription_status_change(user, admin))
                old_status_value = getattr(original_status, "value", original_status)
                new_status_value = getattr(user.status, "value", user.status)
                logger.info(
                    f'Subscription "{user.uuid}" status changed from "{old_status_value}" to "{new_status_value}"'
                )

        return self._build_bulk_action_response(users)

    async def _prepare_bulk_limit_update(
        self,
        db: AsyncSession,
        bulk_model: BulkSubscription,
        admin: AdminDetails,
        field: Literal["expire", "data_limit"],
    ) -> dict:
        """Check write access and validate every final value before a guarded batch update."""
        if not _has_permission(admin, "subscriptions", "update") or not is_scope_all(admin, "subscriptions", "update"):
            await self.raise_error(message="All-user update permission is required", code=403, db=db)
        await self._enforce_manual_subscription_write_access(admin, db)
        if admin.is_owner:
            return {}

        limits = get_effective_limits(admin)
        minimum = getattr(limits, f"{field}_min")
        maximum = getattr(limits, f"{field}_max")
        if minimum is None and maximum is None:
            return {}

        targets = await get_bulk_user_limit_targets(db, bulk_model, field, lock=not bulk_model.dry_run)
        now = datetime.now(UTC)
        for _, value in targets:
            final_value = (
                fix_datetime_timezone(value) + td(seconds=bulk_model.amount)
                if field == "expire"
                else value + bulk_model.amount
            )
            await self._enforce_subscription_limits(
                db,
                admin,
                **{field: final_value},
                require_finite_data_limit=field == "data_limit",
                require_finite_expire=field == "expire",
                require_finite_on_hold_timeout=False,
                require_finite_hwid_limit=False,
                reference_time=now,
            )
        if field == "expire":
            minimum = now + td(seconds=minimum) if minimum is not None else None
            maximum = now + td(seconds=maximum) if maximum is not None else None
        return {
            "target_ids": [subscription_uuid for subscription_uuid, _ in targets],
            "minimum": minimum,
            "maximum": maximum,
        }

    async def bulk_modify_expire(self, db: AsyncSession, bulk_model: BulkSubscription, admin: AdminDetails):
        if await legacy_bulk_scope_contains_core_managed(db, bulk_model):
            await self.raise_error("Core-managed subscription policy must be updated through VPN Core", 409)
        """Adjust finite expirations atomically within the actor's role and override limits."""
        constraints = await self._prepare_bulk_limit_update(db, bulk_model, admin, "expire")
        if bulk_model.dry_run:
            n = len(constraints["target_ids"]) if constraints else await count_bulk_expire_targets(db, bulk_model)
            return BulkOperationDryRunResponse(affected_subscriptions=n)
        try:
            users, users_count = await update_users_expire(db, bulk_model, **constraints)
        except BulkSubscriptionUpdateConflict as exc:
            await self.raise_error(message=str(exc), code=409, db=db)
        await sync_subscriptions(users)

        if self.operator_type in (OperatorType.API, OperatorType.WEB):
            return {"detail": f"operation has been successfuly done on {users_count} users"}
        return users_count

    async def bulk_modify_datalimit(self, db: AsyncSession, bulk_model: BulkSubscription, admin: AdminDetails):
        if await legacy_bulk_scope_contains_core_managed(db, bulk_model):
            await self.raise_error("Core-managed subscription policy must be updated through VPN Core", 409)
        """Adjust finite quotas atomically within the actor's role and override limits."""
        constraints = await self._prepare_bulk_limit_update(db, bulk_model, admin, "data_limit")
        if bulk_model.dry_run:
            n = len(constraints["target_ids"]) if constraints else await count_bulk_datalimit_targets(db, bulk_model)
            return BulkOperationDryRunResponse(affected_subscriptions=n)
        try:
            users, users_count = await update_users_datalimit(db, bulk_model, **constraints)
        except BulkSubscriptionUpdateConflict as exc:
            await self.raise_error(message=str(exc), code=409, db=db)
        await sync_subscriptions(users)

        if self.operator_type in (OperatorType.API, OperatorType.WEB):
            return {"detail": f"operation has been successfuly done on {users_count} users"}
        return users_count

    async def bulk_modify_proxy_settings(self, db: AsyncSession, bulk_model: BulkSubscriptionsProxy):
        if await legacy_bulk_scope_contains_core_managed(db, bulk_model):
            await self.raise_error("Core-managed subscription policy must be updated through VPN Core", 409)
        if bulk_model.method is None:
            await self.raise_error(message="No supported proxy settings were provided", code=400, db=db)
        if bulk_model.dry_run:
            n = await count_bulk_proxy_targets(db, bulk_model)
            return BulkOperationDryRunResponse(affected_subscriptions=n)
        users, users_count = await update_users_proxy_settings(db, bulk_model)
        await sync_subscriptions(users)

        if self.operator_type in (OperatorType.API, OperatorType.WEB):
            return {"detail": f"operation has been successfuly done on {users_count} users"}
        return users_count

    async def _get_subscriptions_sub_update_list(
        self, db: AsyncSession, db_user: Subscription, offset: int = 0, limit: int = 10
    ) -> SubscriptionUpdateList:
        user_sub_data, count = await get_subscriptions_sub_update_list(
            db, subscription_uuid=db_user.uuid, offset=offset, limit=limit
        )
        return SubscriptionUpdateList(updates=user_sub_data, count=count)

    async def get_subscriptions_sub_update_list_by_id(
        self, db: AsyncSession, subscription_uuid: UUID, admin: AdminDetails, offset: int = 0, limit: int = 10
    ) -> SubscriptionUpdateList:
        db_user = await self.get_validated_subscription_by_uuid(db, subscription_uuid, admin)
        return await self._get_subscriptions_sub_update_list(db, db_user, offset, limit)

    async def get_subscriptions_sub_update_chart(
        self,
        db: AsyncSession,
        admin: AdminDetails,
        subscription_uuid: UUID | None = None,
        admin_id: int | None = None,
        period: Period = Period.hour,
        start: dt | None = None,
        end: dt | None = None,
    ) -> SubscriptionUpdateChart:
        start, end = await self.validate_dates(start, end, True)
        resolved_subscription_uuid: UUID | None = None
        resolved_admin_id: int | None = None

        if subscription_uuid is not None:
            db_user = await self.get_validated_subscription_by_uuid(db, subscription_uuid, admin)
            resolved_subscription_uuid = db_user.uuid
        else:
            if admin_id:
                can_read_admins = False
                try:
                    enforce_permission(admin, "admins", "read")
                    can_read_admins = True
                except PermissionDenied:
                    pass
                if not can_read_admins and admin_id != admin.id:
                    await self.raise_error(message="You're not allowed", code=403)
                elif can_read_admins and admin_id != admin.id:
                    await self.get_validated_admin_by_id(db, admin_id)
                resolved_admin_id = admin_id
            else:
                resolved_admin_id = get_scope_admin_id(admin, "subscriptions", "read")

        agent_stats = await get_subscriptions_agent_stats(
            db,
            start=start,
            end=end,
            period=period,
            subscription_uuid=resolved_subscription_uuid,
            admin_id=resolved_admin_id,
        )
        # The period rows already contain every update in the requested range,
        # so derive pie totals from them instead of scanning the same history a
        # second time solely for totals.
        agent_totals: Counter[str] = Counter()
        for row in agent_stats:
            agent_totals[row.get("agent") or ""] += int(row.get("count") or 0)

        return self._build_user_agent_chart(
            list(agent_totals.items()),
            start=start,
            end=end,
            agent_stats=agent_stats,
            period=period,
        )

    @classmethod
    def _build_user_agent_chart(
        cls,
        agent_counts: list[tuple[str, int]],
        *,
        start: dt,
        end: dt,
        agent_stats: list[dict] | None = None,
        period: Period | None = None,
    ) -> SubscriptionUpdateChart:
        counts = Counter()
        display_names: dict[str, str] = {}

        for agent, count in agent_counts:
            normalized = cls._normalize_user_agent(agent)
            key = normalized.lower()
            counts[key] += count
            display_names.setdefault(key, normalized)

        total = sum(counts.values())
        segments = [
            SubscriptionUpdateChartSegment(
                name=display_names[key],
                count=count,
                percentage=round((count / total) * 100, 2) if total else 0.0,
            )
            for key, count in counts.most_common()
        ]

        stats: list[SubscriptionUpdateChartStat] = []
        if agent_stats:
            period_counts: Counter[tuple[dt, str]] = Counter()
            for row in agent_stats:
                normalized = cls._normalize_user_agent(row.get("agent") or "")
                key = normalized.lower()
                display_names.setdefault(key, normalized)
                period_start = row.get("period_start")
                if period_start is None:
                    continue
                period_counts[(period_start, key)] += int(row.get("count") or 0)

            stats = [
                SubscriptionUpdateChartStat(
                    agent=display_names[key],
                    count=count,
                    period_start=period_start,
                )
                for (period_start, key), count in sorted(period_counts.items(), key=lambda item: item[0][0])
            ]

        return SubscriptionUpdateChart(
            total=total,
            segments=segments,
            period=period,
            start=start,
            end=end,
            stats=stats,
        )

    @staticmethod
    def _normalize_user_agent(user_agent: str) -> str:
        if not user_agent:
            return "Unknown"

        cleaned = user_agent.strip()
        if not cleaned:
            return "Unknown"

        tokens = [token for token in _USER_AGENT_SPLIT_RE.split(cleaned) if token]

        for token in tokens:
            if _VERSION_TOKEN_RE.fullmatch(token):
                continue

            sanitized = token.strip("-_")
            if sanitized:
                return sanitized

        return "Unknown"
