"""A signed subscription link must never override the current provider access state."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.db.models import SubscriptionStatus
from app.operation import BaseOperation, OperatorType


@pytest.mark.parametrize(
    ("status", "is_expired", "is_limited", "allowed"),
    [
        (SubscriptionStatus.active, False, False, True),
        (SubscriptionStatus.on_hold, False, False, True),
        (SubscriptionStatus.disabled, False, False, False),
        (SubscriptionStatus.expired, False, False, False),
        (SubscriptionStatus.limited, False, False, False),
        (SubscriptionStatus.active, True, False, False),
        (SubscriptionStatus.active, False, True, False),
    ],
)
def test_subscription_access_uses_current_provider_state(monkeypatch, status, is_expired, is_limited, allowed):
    subscription_uuid = uuid4()

    async def verified_token(_db, _token):
        return SimpleNamespace(uuid=subscription_uuid, entitlement="active")

    async def lookup_user(_session, _id, **_kwargs):
        return SimpleNamespace(
            uuid=subscription_uuid,
            status=status,
            is_expired=is_expired,
            is_limited=is_limited,
            created_at=datetime.now(UTC) - timedelta(days=2),
            sub_revoked_at=None,
        )

    monkeypatch.setattr("app.operation.subscription_from_token", verified_token)
    monkeypatch.setattr("app.operation.get_subscription_by_uuid", lookup_user)
    operation = BaseOperation(OperatorType.API)

    async def exercise():
        return await operation.get_validated_sub(None, "otherwise-valid-signed-token")

    if allowed:
        assert asyncio.run(exercise()).status is status
    else:
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(exercise())
        assert exc_info.value.status_code == 403


@pytest.mark.parametrize("entitlement", ["payment_required", "suspended", "expired", "cancelled"])
def test_inactive_subscription_never_reaches_node_inbounds(entitlement):
    """Commercial refusal also revokes previously downloaded VPN credentials."""
    from app.node.subscription import serialize_subscription

    async def assigned_inbounds():
        return ["test-inbound"]

    subscription = SimpleNamespace(
        uuid=uuid4(),
        proxy_settings={},
        status=SubscriptionStatus.active,
        entitlement=entitlement,
        inbounds=assigned_inbounds,
    )
    forbidden_node = asyncio.run(serialize_subscription(subscription))
    assert list(forbidden_node.inbounds) == []

    subscription.entitlement = "active"
    allowed_node = asyncio.run(serialize_subscription(subscription))
    assert list(allowed_node.inbounds) == ["test-inbound"]
