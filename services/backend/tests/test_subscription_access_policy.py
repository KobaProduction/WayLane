"""A signed subscription link must never override the current provider access state."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.db.models import UserStatus
from app.operation import BaseOperation, OperatorType


@pytest.mark.parametrize(
    ("status", "is_expired", "is_limited", "allowed"),
    [
        (UserStatus.active, False, False, True),
        (UserStatus.on_hold, False, False, True),
        (UserStatus.disabled, False, False, False),
        (UserStatus.expired, False, False, False),
        (UserStatus.limited, False, False, False),
        (UserStatus.active, True, False, False),
        (UserStatus.active, False, True, False),
    ],
)
def test_subscription_access_uses_current_provider_state(monkeypatch, status, is_expired, is_limited, allowed):
    async def verified_token(_token):
        return {"user_id": 7, "created_at": datetime.now(UTC)}

    async def lookup_user(_session, _id, **_kwargs):
        return SimpleNamespace(
            status=status,
            is_expired=is_expired,
            is_limited=is_limited,
            created_at=datetime.now(UTC) - timedelta(days=2),
            sub_revoked_at=None,
        )

    monkeypatch.setattr("app.operation.get_subscription_payload", verified_token)
    monkeypatch.setattr("app.operation.get_user_by_id", lookup_user)
    operation = BaseOperation(OperatorType.API)

    async def exercise():
        return await operation.get_validated_sub(None, "otherwise-valid-signed-token")

    if allowed:
        assert asyncio.run(exercise()).status is status
    else:
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(exercise())
        assert exc_info.value.status_code == 403
