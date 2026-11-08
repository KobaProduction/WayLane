"""Subscription commercial access state."""

from enum import StrEnum


class SubscriptionEntitlement(StrEnum):
    ACTIVE = "active"
    PAYMENT_REQUIRED = "payment_required"
    SUSPENDED = "suspended"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
