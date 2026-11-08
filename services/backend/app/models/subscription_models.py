from datetime import datetime as dt
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.db.models import DataLimitResetStrategy, SubscriptionStatus
from app.models.admin import AdminBase, AdminContactInfo
from app.models.proxy import ProxyTable, ShadowsocksMethods
from app.models.stats import Period, PeriodStartStat, StatList
from app.utils.helpers import fix_datetime_timezone

from .validators import (
    MAX_ON_HOLD_EXPIRE_DURATION_SECONDS,
    AwareDatetime,
    ListValidator,
    NumericValidatorMixin,
    OptionalAwareDatetime,
    SubscriptionValidator,
)


class SubscriptionStatusModify(str, Enum):
    active = "active"
    disabled = "disabled"
    on_hold = "on_hold"


class NextPlanModel(BaseModel):
    subscription_template_id: int | None = Field(default=None)
    data_limit: int | None = Field(default=None)
    expire: int | None = Field(default=None)
    add_remaining_traffic: bool = False
    model_config = ConfigDict(from_attributes=True)


class Subscription(BaseModel):
    proxy_settings: ProxyTable = Field(default_factory=ProxyTable)
    expire: dt | int | None = Field(default=None)
    data_limit: int | None = Field(ge=0, default=None, description="data_limit can be 0 or greater")
    data_limit_reset_strategy: DataLimitResetStrategy | None = Field(default=None)
    note: str | None = Field(max_length=500, default=None)
    on_hold_expire_duration: int | None = Field(
        ge=0,
        le=MAX_ON_HOLD_EXPIRE_DURATION_SECONDS,
        default=None,
        description="on_hold_expire_duration can be 0 or greater in seconds",
    )
    on_hold_timeout: dt | int | None = Field(default=None)
    group_ids: list[int] | None = Field(default_factory=list)
    auto_delete_in_days: int | None = Field(default=None)
    hwid_limit: int | None = Field(default=None)
    next_plan: NextPlanModel | None = Field(default=None)


class SubscriptionWithValidator(Subscription):
    @field_validator("on_hold_expire_duration")
    @classmethod
    def validate_timeout(cls, v):
        # Check if expire is 0 or None and timeout is not 0 or None
        if v in (0, None):
            return None
        return v

    @field_validator("on_hold_timeout", check_fields=False)
    @classmethod
    def validator_on_hold_timeout(cls, value):
        return SubscriptionValidator.validator_on_hold_timeout(value)

    @field_validator("expire", check_fields=False)
    @classmethod
    def validator_expire(cls, value):
        if not value:
            return value
        return fix_datetime_timezone(value)

    @field_validator("proxy_settings")
    @classmethod
    def validate_wireguard_keys(cls, value: ProxyTable | None):
        # Strict on input only: responses load stored rows through ProxyTable, which may hold keys saved before this.
        if value is not None:
            value.wireguard.check_input_keys()
        return value

    @field_validator("next_plan")
    @classmethod
    def validate_next_plan(cls, value: NextPlanModel | None):
        # Bound on input only: responses reuse NextPlanModel and must still load rows stored before this check.
        expire = value.expire if value is not None else None
        if expire is not None and not 0 <= expire <= MAX_ON_HOLD_EXPIRE_DURATION_SECONDS:
            raise ValueError(f"next_plan expire must be between 0 and {MAX_ON_HOLD_EXPIRE_DURATION_SECONDS} seconds")
        return value


class SubscriptionCreate(SubscriptionWithValidator):
    uuid: UUID
    status: SubscriptionStatus | None = Field(default=None)

    @field_validator("status", mode="before", check_fields=False)
    def validate_status(cls, status, values):
        return SubscriptionValidator.validate_status(
            status, {SubscriptionStatus.active, SubscriptionStatus.on_hold}, values
        )

    @field_validator("group_ids", mode="after")
    @classmethod
    def group_ids_validator(cls, v):
        return ListValidator.not_null_list(v, "group")


class SubscriptionModify(SubscriptionWithValidator):
    status: SubscriptionStatus | None = Field(default=None)
    proxy_settings: ProxyTable | None = Field(default=None)
    group_ids: list[int] | None = Field(default=None)

    @field_validator("status", mode="before", check_fields=False)
    def validate_status(cls, status, values):
        return SubscriptionValidator.validate_status(
            status, {SubscriptionStatus.active, SubscriptionStatus.on_hold, SubscriptionStatus.disabled}, values
        )

    @field_validator("group_ids", mode="after")
    @classmethod
    def group_ids_validator(cls, v):
        return ListValidator.nullable_list(v, "group")


class SubscriptionStatusToggle(BaseModel):
    disabled: bool


class SubscriptionNotificationResponse(Subscription):
    uuid: UUID
    status: SubscriptionStatus
    used_traffic: int
    lifetime_used_traffic: int = Field(default=0)
    created_at: AwareDatetime
    edit_at: OptionalAwareDatetime = Field(default=None)
    online_at: OptionalAwareDatetime = Field(default=None)
    subscription_url: str = Field(default="")
    admin: AdminContactInfo | None = Field(default=None)
    group_names: list[str] | None = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)

    @field_validator("used_traffic", "lifetime_used_traffic", "data_limit", mode="before")
    @classmethod
    def cast_to_int(cls, v):
        return NumericValidatorMixin.cast_to_int(v)


class SubscriptionResponse(SubscriptionNotificationResponse):
    admin: AdminBase | None = Field(default=None)
    group_names: list[str] | None = Field(default=None, exclude=True)


class SubscriptionAccessResponse(SubscriptionResponse):
    # Public links expose a canonical UUID, never account credentials or an administrator.
    uuid: UUID = Field(exclude=True)
    subscription_uuid: UUID | None = Field(default=None)
    # A shareable subscription token must not expose connection credentials in metadata.
    proxy_settings: ProxyTable = Field(default_factory=ProxyTable, exclude=True)
    admin: AdminContactInfo | None = Field(default=None, exclude=True)
    note: str | None = Field(None, exclude=True)
    auto_delete_in_days: int | None = Field(None, exclude=True)
    subscription_url: str | None = Field(None, exclude=True)
    ip: str | None = Field(default=None)
    model_config = ConfigDict(from_attributes=True)


class SubscriptionWithInbounds(SubscriptionAccessResponse):
    inbounds: list[str] | None = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class SubscriptionListItem(SubscriptionResponse):
    # None when the device count was not loaded, for example without hwids.read
    hwid_count: int | None = Field(default=None, ge=0)


class SubscriptionsResponse(BaseModel):
    subscriptions: list[SubscriptionListItem]
    total: int


class SubscriptionSimple(BaseModel):
    """Lightweight subscription identified only by UUID."""

    uuid: UUID
    model_config = ConfigDict(from_attributes=True)


class SubscriptionsSimpleResponse(BaseModel):
    """Lightweight subscription list without numeric database identifiers."""

    subscriptions: list[SubscriptionSimple]
    total: int


class SubscriptionSortField(str, Enum):
    hwid_count = "hwid_count"
    uuid = "uuid"
    used_traffic = "used_traffic"
    data_limit = "data_limit"
    expire = "expire"
    created_at = "created_at"
    edit_at = "edit_at"
    online_at = "online_at"


class SubscriptionSimpleSortField(str, Enum):
    uuid = "uuid"


class SortDirection(str, Enum):
    asc = "asc"
    desc = "desc"


class SubscriptionSortOption(str, Enum):
    hwid_count = "hwid_count"
    desc_hwid_count = "-hwid_count"
    uuid = "uuid"
    used_traffic = "used_traffic"
    data_limit = "data_limit"
    expire = "expire"
    created_at = "created_at"
    edit_at = "edit_at"
    online_at = "online_at"
    desc_uuid = "-uuid"
    desc_used_traffic = "-used_traffic"
    desc_data_limit = "-data_limit"
    desc_expire = "-expire"
    desc_created_at = "-created_at"
    desc_edit_at = "-edit_at"
    desc_online_at = "-online_at"

    @property
    def field(self) -> SubscriptionSortField:
        return SubscriptionSortField(self.value.lstrip("-"))

    @property
    def direction(self) -> SortDirection:
        return SortDirection.desc if self.value.startswith("-") else SortDirection.asc


class SubscriptionSimpleSortOption(str, Enum):
    uuid = "uuid"
    desc_uuid = "-uuid"

    @property
    def field(self) -> SubscriptionSimpleSortField:
        return SubscriptionSimpleSortField(self.value.lstrip("-"))

    @property
    def direction(self) -> SortDirection:
        return SortDirection.desc if self.value.startswith("-") else SortDirection.asc


class SubscriptionListQuery(BaseModel):
    offset: int | None = Field(default=None)
    limit: int | None = Field(default=None)
    uuids: list[UUID] | None = Field(default=None)
    owner: list[str] | None = Field(default=None, alias="admin")
    admin_ids: list[int] | None = Field(default=None, validation_alias=AliasChoices("admin_ids", "admin_id"))
    group_ids: list[int] | None = Field(default=None, alias="group")
    no_group: bool = Field(default=False)
    search: str | None = Field(default=None)
    status: SubscriptionStatus | list[SubscriptionStatus] | None = Field(default=None)
    sort: list[SubscriptionSortOption] = Field(default_factory=list)
    proxy_id: str | None = Field(default=None)
    data_limit_reset_strategy: DataLimitResetStrategy | list[DataLimitResetStrategy] | None = Field(
        default=None, validation_alias=AliasChoices("data_limit_reset_strategy", "reset_strategy")
    )
    data_limit_min: int | None = Field(default=None, ge=0)
    data_limit_max: int | None = Field(default=None, ge=0)
    expire_after: OptionalAwareDatetime = Field(default=None, examples=["2026-01-01T00:00:00+03:30"])
    expire_before: OptionalAwareDatetime = Field(default=None, examples=["2026-01-31T23:59:59+03:30"])
    online_after: OptionalAwareDatetime = Field(default=None, examples=["2026-01-01T00:00:00+03:30"])
    online_before: OptionalAwareDatetime = Field(default=None, examples=["2026-01-31T23:59:59+03:30"])
    online: bool = Field(default=False)
    no_data_limit: bool = Field(default=False)
    no_expire: bool = Field(default=False)
    load_sub: bool = Field(default=False)
    model_config = ConfigDict(populate_by_name=True)

    @field_validator("sort", mode="before")
    @classmethod
    def validate_sort(cls, value):
        return ListValidator.normalize_enum_list_input(value, SubscriptionSortOption)


class SubscriptionSimpleListQuery(BaseModel):
    uuids: list[UUID] | None = Field(default=None)
    offset: int | None = Field(default=None)
    limit: int | None = Field(default=None)
    search: str | None = Field(default=None)
    sort: list[SubscriptionSimpleSortOption] = Field(default_factory=list)
    all: bool = Field(default=False)

    @field_validator("sort", mode="before")
    @classmethod
    def validate_sort(cls, value):
        return ListValidator.normalize_enum_list_input(value, SubscriptionSimpleSortOption)


class SubscriptionUsageQuery(BaseModel):
    period: Period = Field(default=Period.hour)
    node_id: int | None = Field(default=None)
    group_by_node: bool = Field(default=False)
    start: OptionalAwareDatetime = Field(default=None, examples=["2024-01-01T00:00:00+03:30"])
    end: OptionalAwareDatetime = Field(default=None, examples=["2024-01-31T23:59:59+03:30"])


class SubscriptionsUsageQuery(SubscriptionUsageQuery):
    owner: list[str] | None = Field(default=None, alias="admin")
    model_config = ConfigDict(populate_by_name=True)


class SubscriptionsUsageBreakdownQuery(SubscriptionsUsageQuery):
    core_id: int | None = Field(default=None)
    group_by_admin: bool = Field(default=False)


class ExpiredSubscriptionsQuery(BaseModel):
    admin_username: str | None = Field(default=None)
    target: Literal["expired", "limited", "on_hold", "disabled", "inactive"] = Field(default="expired")
    expired_after: OptionalAwareDatetime = Field(default=None, examples=["2024-01-01T00:00:00+03:30"])
    expired_before: OptionalAwareDatetime = Field(default=None, examples=["2024-01-31T23:59:59+03:30"])
    dry_run: bool = Field(
        default=False,
        description="If true, returns users that would be deleted without actually deleting them.",
    )

    @model_validator(mode="after")
    def validate_inactive_cutoff(self):
        if self.target == "inactive" and self.expired_before is None:
            raise ValueError("expired_before is required when target is inactive")
        return self


class SubscriptionUpdateSchema(BaseModel):
    created_at: AwareDatetime
    user_agent: str
    ip: str | None = Field(default=None)
    hwid: str | None = Field(default=None)

    model_config = ConfigDict(from_attributes=True)


class SubscriptionUpdateList(BaseModel):
    updates: list[SubscriptionUpdateSchema] = Field(default_factory=list)
    count: int


class SubscriptionUpdateChartSegment(BaseModel):
    name: str
    count: int
    percentage: float


class SubscriptionUpdateChartStat(PeriodStartStat):
    agent: str
    count: int

    @field_validator("count", mode="before")
    @classmethod
    def cast_count_to_int(cls, v):
        return NumericValidatorMixin.cast_to_int(v)


class SubscriptionUpdateChart(StatList):
    total: int
    segments: list[SubscriptionUpdateChartSegment] = Field(default_factory=list)
    stats: list[SubscriptionUpdateChartStat] = Field(default_factory=list)


class SubscriptionHWIDResponse(BaseModel):
    id: int
    hwid: str
    device_os: str | None = None
    os_version: str | None = None
    device_model: str | None = None
    created_at: dt
    last_used_at: dt
    model_config = ConfigDict(from_attributes=True)


class SubscriptionHWIDListResponse(BaseModel):
    hwids: list[SubscriptionHWIDResponse]
    count: int


class RemoveSubscriptionsResponse(BaseModel):
    uuids: list[UUID]
    count: int


class BulkSubscriptionsActionResponse(BaseModel):
    uuids: list[UUID]
    count: int


class BulkSubscriptionsSelection(BaseModel):
    uuids: set[UUID] = Field(default_factory=set)

    @field_validator("uuids", mode="after")
    @classmethod
    def uuids_validator(cls, v):
        return ListValidator.not_null_list(v, "subscription")


class BulkSubscriptionsSetOwner(BulkSubscriptionsSelection):
    admin_username: str

    @field_validator("admin_username", check_fields=False)
    @classmethod
    def validate_admin_username(cls, v):
        return SubscriptionValidator.validate_username(v)


class ModifySubscriptionByTemplate(BaseModel):
    subscription_template_id: int
    note: str | None = Field(max_length=500, default=None)


class BulkSubscriptionsApplyTemplate(BulkSubscriptionsSelection, ModifySubscriptionByTemplate):
    """Apply a subscription template to the selected subscription UUIDs."""


class BulkSubscriptionFilter(BaseModel):
    dry_run: bool = False
    group_ids: set[int] = Field(default_factory=set)
    admins: set[int] = Field(default_factory=set)
    subscriptions: set[UUID] = Field(default_factory=set)
    status: set[SubscriptionStatus] = Field(default_factory=set)
    expire_after: OptionalAwareDatetime = Field(
        default=None, validation_alias=AliasChoices("expire_after", "expired_after")
    )
    expire_before: OptionalAwareDatetime = Field(
        default=None, validation_alias=AliasChoices("expire_before", "expired_before")
    )


class BulkSubscription(BulkSubscriptionFilter):
    amount: int


class BulkSubscriptionsProxy(BulkSubscriptionFilter):
    method: ShadowsocksMethods | None = Field(default=None)


class BulkOperationDryRunResponse(BaseModel):
    """Preview for scoped subscription operations (no DB writes)."""

    dry_run: bool = True
    affected_subscriptions: int
