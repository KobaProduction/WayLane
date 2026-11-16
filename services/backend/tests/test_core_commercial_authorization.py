"""Native Core-managed subscriptions require per-request signed commercial grants."""
import asyncio
import hmac
import json
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.subscription import core_authorization as auth

_URL = "http://127.0.0.1:8765/v1/integrations/waylane/subscription-authorizations"
_SECRET = "test-only-synthetic-core-authentication-signing-key"


def test_independent_subscriptions_never_require_intens_core(monkeypatch):
    monkeypatch.delenv("WAYLANE_CORE_AUTHORIZATION_URL", raising=False)
    standalone = SimpleNamespace(uuid=uuid4(), entitlement_revision=0, policy_revision=0)
    asyncio.run(auth.authorize_core_subscription(standalone))


def test_managed_subscriptions_fail_closed_on_missing_untrusted_origin(monkeypatch):
    sub = SimpleNamespace(uuid=uuid4(), entitlement_revision=1, policy_revision=0)
    monkeypatch.setenv("WAYLANE_DEVICE_WEBHOOK_SECRET", _SECRET)
    for value in ("", "http://public.example/v1/integrations/waylane/subscription-authorizations",
                  "http://127.0.0.1:8765/v1/integrations/waylane/subscription-authorizations",
                  "http://vpn-core-api:8000/v1/integrations/waylane/subscription-authorizations?leak=1"):
        monkeypatch.setenv("WAYLANE_CORE_AUTHORIZATION_URL", value)
        monkeypatch.delenv("WAYLANE_WEBHOOK_ALLOW_LOCAL_TEST", raising=False)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(auth.authorize_core_subscription(sub))
        assert exc.value.status_code == 503


@pytest.mark.parametrize("core_status,expected", [(204,None),(403,403),(401,503),(200,503),(302,503),(500,503)])
def test_core_signed_replies_and_denial(monkeypatch, core_status, expected):
    sub = SimpleNamespace(uuid=uuid4(), entitlement_revision=1, policy_revision=3)
    monkeypatch.setenv("WAYLANE_DEVICE_WEBHOOK_SECRET", _SECRET)
    monkeypatch.setenv("WAYLANE_CORE_AUTHORIZATION_URL", _URL)
    monkeypatch.setenv("WAYLANE_WEBHOOK_ALLOW_LOCAL_TEST", "1")
    observed = []
    original = httpx.AsyncClient
    def receiver(request):
        observed.append(request)
        body = bytes(request.content)
        timestamp = request.headers["X-WayLane-Timestamp"]
        signed = auth._PURPOSE + timestamp.encode("ascii") + b"\n" + body
        expected_signature = "sha256=" + hmac.new(_SECRET.encode(), signed, sha256).hexdigest()
        assert request.headers["X-WayLane-Access-Signature"] == expected_signature
        assert json.loads(body) == {"subscription_uuid":str(sub.uuid)}
        assert request.url.path == auth._AUTH_PATH
        return httpx.Response(core_status, headers={"location": "http://example.invalid/"} if core_status==302 else {})
    monkeypatch.setattr(auth.httpx, "AsyncClient",
                        lambda **kw: original(transport=httpx.MockTransport(receiver), **kw))
    if expected is None:
        asyncio.run(auth.authorize_core_subscription(sub))
    else:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(auth.authorize_core_subscription(sub))
        assert exc.value.status_code == expected
    assert len(observed)==1


def test_core_unavailable_denies_instead_of_returning_cached_allow(monkeypatch):
    sub = SimpleNamespace(uuid=uuid4(), entitlement_revision=0, policy_revision=2)
    monkeypatch.setenv("WAYLANE_DEVICE_WEBHOOK_SECRET", _SECRET)
    monkeypatch.setenv("WAYLANE_CORE_AUTHORIZATION_URL", _URL)
    monkeypatch.setenv("WAYLANE_WEBHOOK_ALLOW_LOCAL_TEST", "1")
    original = httpx.AsyncClient
    def outage(request):
        raise httpx.ConnectError("test-only refusal", request=request)
    monkeypatch.setattr(auth.httpx, "AsyncClient",
                        lambda **kw: original(transport=httpx.MockTransport(outage), **kw))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth.authorize_core_subscription(sub))
    assert exc.value.status_code == 503
