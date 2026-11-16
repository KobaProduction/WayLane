"""Subscription commercial access state."""

from enum import StrEnum


class SubscriptionEntitlement(StrEnum):
    ACTIVE = "active"
    PAYMENT_REQUIRED = "payment_required"
    SUSPENDED = "suspended"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


def dev_unpaid_access_enabled() -> bool:
    """Explicit DEV-only standalone mode; never overrides a production deployment."""
    import os

    from config import subscription_env_settings

    return (
        subscription_env_settings.dev_allow_unpaid
        and os.environ.get("DEPLOYMENT_ENVIRONMENT", "").strip().lower() == "development"
    )


def entitlement_allows_access(entitlement: str) -> bool:
    """Only the absent *payment* decision is optional in standalone DEV.

    Suspended, cancelled and expired entitlements still fail closed, as do the
    separate technical status, expiry, quota, group and admin checks.
    """
    return entitlement == "active" or (entitlement == "payment_required" and dev_unpaid_access_enabled())
