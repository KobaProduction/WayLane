"""Standalone WayLane DEV can exercise subscriptions without VPN Core billing."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.db.models import SubscriptionStatus
from app.operation import BaseOperation, OperatorType
from app.routers.node_registry import ManagedInboundInput, enable_managed_xray_inbound
from app.subscription.entitlements import dev_unpaid_access_enabled, entitlement_allows_access
from app.subscription_delivery import node_payload_signature
from config import subscription_env_settings
from tests.test_node_managed_subscription_wss import setup_db


def _set_standalone(monkeypatch, *, enabled: bool, environment: str):
    monkeypatch.setattr(subscription_env_settings, "dev_allow_unpaid", enabled)
    monkeypatch.setenv("DEPLOYMENT_ENVIRONMENT", environment)


@pytest.mark.parametrize("enabled,environment,expected", [
    (False, "development", False),
    (True, "production", False),
    (True, "staging", False),
    (True, "", False),
    (True, "development", True),
])
def test_payment_gate_is_explicitly_development_only(monkeypatch, enabled, environment, expected):
    _set_standalone(monkeypatch, enabled=enabled, environment=environment)
    assert dev_unpaid_access_enabled() is expected
    assert entitlement_allows_access("active")
    assert entitlement_allows_access("payment_required") is expected
    for disabled in ("suspended", "cancelled", "expired", "unexpected"):
        assert not entitlement_allows_access(disabled)


@pytest.mark.parametrize("status,expired,limited,allowed", [
    (SubscriptionStatus.active, False, False, True),
    (SubscriptionStatus.on_hold, False, False, True),
    (SubscriptionStatus.disabled, False, False, False),
    (SubscriptionStatus.active, True, False, False),
    (SubscriptionStatus.active, False, True, False),
])
def test_dev_link_still_enforces_technical_policy(monkeypatch, status, expired, limited, allowed):
    _set_standalone(monkeypatch, enabled=True, environment="development")
    uid = uuid4()

    async def lookup_token(_db, _token):
        return SimpleNamespace(uuid=uid, entitlement="payment_required")

    async def lookup_subscription(_db, _uid, **_kwargs):
        return SimpleNamespace(uuid=uid, status=status, is_expired=expired, is_limited=limited)

    monkeypatch.setattr("app.operation.subscription_from_token", lookup_token)
    monkeypatch.setattr("app.operation.get_subscription_by_uuid", lookup_subscription)

    async def exercise():
        return await BaseOperation(OperatorType.API).get_validated_sub(None, "synthetic-valid-token")

    if allowed:
        assert asyncio.run(exercise()).status == status
    else:
        with pytest.raises(HTTPException) as error:
            asyncio.run(exercise())
        assert error.value.status_code == 403


def test_unpaid_client_is_injected_only_in_standalone_dev(monkeypatch, tmp_path):
    async def run():
        engine, factory, agent, uid, paid, unpaid = await setup_db(tmp_path)
        try:
            async with factory() as db:
                await enable_managed_xray_inbound(uid, "allow-vless", ManagedInboundInput(expected_generation=1), db=db, _=object())

                async def client_uuids():
                    from app.routers.node_registry import agent_xray_desired
                    result = await agent_xray_desired(uid, x_node_secret=agent["node_token"], db=db)
                    return {client["email"] for client in result["config"]["inbounds"][0]["settings"]["clients"]}

                _set_standalone(monkeypatch, enabled=False, environment="development")
                assert await client_uuids() == {str(paid)}
                _set_standalone(monkeypatch, enabled=True, environment="production")
                assert await client_uuids() == {str(paid)}
                _set_standalone(monkeypatch, enabled=True, environment="development")
                assert await client_uuids() == {str(paid), str(unpaid)}

                from app.db.models import Subscription
                subscription = await db.get(Subscription, unpaid)
                assert subscription.entitlement == "payment_required"  # Never rewrite billing state
                signature = await node_payload_signature(subscription)
                assert signature[0] == str(unpaid)
                assert signature[1] != b"{}"
                _set_standalone(monkeypatch, enabled=False, environment="development")
                assert (await node_payload_signature(subscription))[1] == b"{}"
        finally:
            await engine.dispose()

    asyncio.run(run())
