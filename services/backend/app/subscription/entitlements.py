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


def entitlement_allows_access(
    entitlement: str, *, entitlement_revision: int = 0, policy_revision: int = 0
) -> bool:
    """Standalone DEV may bypass payment only before Core owns commercial state.

    Once Core asserts a policy or entitlement revision, its commercial denial
    cannot be bypassed through native documents or a Node/Xray desired state.
    """
    return entitlement == "active" or (
        entitlement == "payment_required"
        and entitlement_revision == 0
        and policy_revision == 0
        and dev_unpaid_access_enabled()
    )
