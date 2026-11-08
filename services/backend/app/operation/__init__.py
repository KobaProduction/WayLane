import re
from datetime import UTC, datetime as dt, timedelta as td
from enum import IntEnum
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from app.core.manager import core_manager
from app.db import AsyncSession
from app.db.crud import (
    get_admin,
    get_client_template_by_id,
    get_core_config_by_id,
    get_group_by_id,
    get_host_by_id,
    get_node_by_id,
    get_subscription_template,
)
from app.db.crud.admin import get_admin_by_id
from app.db.crud.group import get_groups_by_ids
from app.db.crud.subscription import get_subscription_by_uuid, get_wireguard_subscription_node_identity
from app.db.models import (
    Admin as DBAdmin,
    ClientTemplate,
    CoreConfig,
    Group,
    Node,
    ProxyHost,
    Subscription,
    SubscriptionStatus,
    SubscriptionTemplate,
)
from app.models.admin import AdminDetails
from app.models.subscription_models import SubscriptionCreate, SubscriptionModify
from app.models.subscription_template import SubscriptionTemplateCreate, SubscriptionTemplateModify
from app.operation.permissions import apply_group_access, get_scope_admin_id
from app.subscription.identity import subscription_from_token
from app.utils.helpers import ensure_datetime_timezone


class OperatorType(IntEnum):
    SYSTEM = 0
    API = 1
    WEB = 2
    CLI = 3


class BaseOperation:
    _HTTP_HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")

    def __init__(self, operator_type: OperatorType):
        self.operator_type = operator_type

    @classmethod
    def sanitize_response_headers(cls, headers: dict[str, Any] | None) -> dict[str, str]:
        """
        Validate and normalize HTTP response headers to avoid runtime encoding failures.
        """
        if not headers:
            return {}

        cleaned_headers: dict[str, str] = {}
        for raw_name, raw_value in headers.items():
            if raw_value is None:
                continue

            header_name = str(raw_name)
            header_value = str(raw_value)

            if not header_name or header_name != header_name.strip():
                raise ValueError("Invalid response header name: empty or whitespace wrapped")
            if not cls._HTTP_HEADER_NAME_PATTERN.fullmatch(header_name):
                raise ValueError(f'Invalid response header name "{header_name}"')

            if "\r" in header_value or "\n" in header_value or "\x00" in header_value:
                raise ValueError(
                    f'Invalid response header "{header_name}": value contains forbidden control characters'
                )

            try:
                header_name.encode("latin-1")
                header_value.encode("latin-1")
            except UnicodeEncodeError as exc:
                raise ValueError(
                    f'Invalid response header "{header_name}": value contains non latin-1 characters'
                ) from exc

            cleaned_headers[header_name] = header_value

        return cleaned_headers

    async def raise_error(self, message: str, code: int, db: AsyncSession | None = None):
        """Raise an error based on the operator type."""
        if db:
            await db.rollback()
        if code <= 0:
            code = 408
        if self.operator_type in [OperatorType.API, OperatorType.WEB]:
            raise HTTPException(status_code=code, detail=str(message))
        else:
            raise ValueError(message)

    async def handle_rpc_error(self, exc: RuntimeError):
        """Convert NATS RPC errors to appropriate HTTP responses."""
        code = getattr(exc, "code", 500)
        await self.raise_error(message=str(exc), code=code)

    async def validate_dates(self, start: dt | None, end: dt | None, set_default_values: bool) -> tuple[dt, dt]:
        """
        Validate if start and end dates are correct and if end is after start.
        Preserves timezone information instead of converting to UTC.
        """

        start_date = None
        end_date = None
        try:
            if start:
                start_date = ensure_datetime_timezone(start)

            if end:
                end_date = ensure_datetime_timezone(end)

            if set_default_values:
                if not start_date:
                    start_date = dt.now(UTC) - td(days=30)
                if not end_date:
                    end_date = dt.now(UTC)

            # Validate that start and end have the same timezone
            if start_date and end_date:
                if start_date.tzinfo != end_date.tzinfo:
                    await self.raise_error(message="Start and end dates must have the same timezone", code=400)

                # Compare dates (SQLAlchemy handles timezone conversion)
                if end_date < start_date:
                    await self.raise_error(message="Start date must be before end date", code=400)

            return start_date, end_date
        except ValueError as e:
            await self.raise_error(message=f"Invalid date range or format: {e!s}", code=400)

    async def get_validated_host(self, db: AsyncSession, host_id: int) -> ProxyHost:
        db_host = await get_host_by_id(db, host_id)
        if db_host is None:
            await self.raise_error(message="Host not found", code=404)
        return db_host

    async def get_validated_sub(
        self,
        db: AsyncSession,
        token: str,
        *,
        load_admin: bool = True,
        load_admin_role: bool = False,
        load_next_plan: bool = True,
        load_usage_logs: bool = True,
        load_groups: bool = True,
        load_lifetime_used_traffic: bool = False,
        wireguard_fast: bool = False,
    ) -> Subscription:
        subscription = await subscription_from_token(db, token)
        if subscription is None:
            await self.raise_error(message="Subscription not found", code=404)
        if subscription.entitlement != "active":
            await self.raise_error(message="Subscription access denied", code=403)

        db_user = None
        if wireguard_fast:
            db_user = await get_wireguard_subscription_node_identity(
                db, subscription.uuid, load_admin_role=load_admin_role
            )
        else:
            db_user = await get_subscription_by_uuid(
                db,
                subscription.uuid,
                load_admin=load_admin,
                load_admin_role=load_admin_role,
                load_next_plan=load_next_plan,
                load_usage_logs=load_usage_logs,
                load_groups=load_groups,
                load_lifetime_used_traffic=load_lifetime_used_traffic,
            )

        if not db_user:
            await self.raise_error(message="Not Found", code=404)

        # Subscription links must reflect provider-owned access immediately,
        # not only after the background status-review loop has run. This guard
        # applies to direct /sub requests as well as management-independent
        # subscription formats and cannot be bypassed by a valid signed token.
        if (
            db_user.status not in (SubscriptionStatus.active, SubscriptionStatus.on_hold)
            or db_user.is_expired
            or db_user.is_limited
        ):
            await self.raise_error(message="Subscription access denied", code=403)

        return db_user

    async def get_validated_subscription_by_uuid(
        self,
        db: AsyncSession,
        subscription_uuid: UUID,
        admin: AdminDetails,
        *,
        load_admin: bool = True,
        load_next_plan: bool = True,
        load_usage_logs: bool = True,
        load_groups: bool = True,
        join_groups: bool = False,
        load_lifetime_used_traffic: bool = False,
        scope_resource: str = "subscriptions",
        scope_action: str = "read",
    ) -> Subscription:
        db_user = await get_subscription_by_uuid(
            db,
            subscription_uuid,
            load_admin=load_admin,
            load_next_plan=load_next_plan,
            load_usage_logs=load_usage_logs,
            load_groups=load_groups,
            join_groups=join_groups,
            load_lifetime_used_traffic=load_lifetime_used_traffic,
            admin_id=get_scope_admin_id(admin, scope_resource, scope_action),
        )
        if not db_user:
            await self.raise_error(message="Subscription not found", code=404)
        return db_user

    async def get_validated_admin(self, db: AsyncSession, username: str) -> DBAdmin:
        db_admin = await get_admin(db, username)
        if not db_admin:
            await self.raise_error(message="Admin not found", code=404)
        return db_admin

    async def get_validated_admin_by_id(self, db: AsyncSession, id: int) -> DBAdmin:
        db_admin = await get_admin_by_id(db, id)
        if not db_admin:
            await self.raise_error(message="Admin not found", code=404)
        return db_admin

    async def get_validated_group(self, db: AsyncSession, group_id: int) -> Group:
        db_group = await get_group_by_id(db, group_id)
        if not db_group:
            await self.raise_error("Group not found", 404)
        return db_group

    async def validate_all_groups(
        self,
        db,
        model: SubscriptionCreate
        | SubscriptionModify
        | SubscriptionTemplate
        | SubscriptionTemplateCreate
        | SubscriptionTemplateModify,
        admin: AdminDetails | None = None,
        *,
        existing_group_ids: set[int] | list[int] | None = None,
    ) -> list[Group]:
        requested_group_ids: list[int] = []
        if model.group_ids:
            requested_group_ids.extend(model.group_ids)

        if not requested_group_ids:
            return []

        unique_ids = list(dict.fromkeys(requested_group_ids))

        if admin is not None:
            allowed_ids = apply_group_access(admin, unique_ids)
            if allowed_ids is not None:
                allowed_set = set(allowed_ids)
                grandfathered = set(existing_group_ids or ())
                for group_id in unique_ids:
                    if group_id not in allowed_set and group_id not in grandfathered:
                        await self.raise_error("Group not found", 404)

        groups = await get_groups_by_ids(db, unique_ids, load_users=False, load_inbounds=True)
        groups_by_id = {group.id: group for group in groups}

        missing_ids = [group_id for group_id in unique_ids if group_id not in groups_by_id]
        if missing_ids:
            await self.raise_error("Group not found", 404)

        # Preserve the requested order and duplicate semantics.
        return [groups_by_id[group_id] for group_id in requested_group_ids]

    async def get_validated_subscription_template(self, db: AsyncSession, template_id: int) -> SubscriptionTemplate:
        dbuser_template = await get_subscription_template(db, template_id)
        if not dbuser_template:
            await self.raise_error("Subscription Template not found", 404)
        return dbuser_template

    async def get_validated_node(self, db: AsyncSession, node_id, *, load_usage_logs: bool = True) -> Node:
        """Dependency: Fetch node or return not found error."""
        db_node = await get_node_by_id(db, node_id, load_usage_logs=load_usage_logs)
        if not db_node:
            await self.raise_error(message="Node not found", code=404)
        return db_node

    async def check_inbound_tags(self, tags: list[str]) -> None:
        for tag in tags:
            if tag not in await core_manager.get_inbounds():
                await self.raise_error(f"{tag} not found", 400)

    async def check_host_inbound_tags(self, tags: list[str]) -> None:
        await self.check_inbound_tags(tags)

    async def get_validated_core_config(self, db: AsyncSession, core_id) -> CoreConfig:
        """Dependency: Fetch core config or return not found error."""
        db_core_config = await get_core_config_by_id(db, core_id)
        if not db_core_config:
            await self.raise_error(message="Core config not found", code=404)
        return db_core_config

    async def get_validated_client_template(self, db: AsyncSession, template_id: int) -> ClientTemplate:
        db_client_template = await get_client_template_by_id(db, template_id)
        if not db_client_template:
            await self.raise_error(message="Client template not found", code=404)
        return db_client_template
