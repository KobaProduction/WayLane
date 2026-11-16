"""Fail-closed commercial admission for native Core-managed subscriptions.

WayLane verifies the public token before this call. Only its canonical UUID
reaches VPN Core; independent subscriptions have neither Core-owned revision.
"""
import hmac
import ipaddress
import json
import os
import time
from hashlib import sha256
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

_AUTH_PATH = "/v1/integrations/waylane/subscription-authorizations"
_PURPOSE = b"intens:waylane:subscription-access:v1\n"


def _trusted_auth_url(raw: str) -> str | None:
    if not raw or raw != raw.strip():
        return None
    try:
        parsed = urlsplit(raw)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if (
        not host
        or (parsed.scheme == "http" and port is None)
        or parsed.path != _AUTH_PATH
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or parsed.scheme not in ("http", "https")
    ):
        return None
    host = host.lower()
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        addr = None
    allowed_internal = {
        value.strip().lower()
        for value in os.getenv("WAYLANE_WEBHOOK_ALLOWED_INTERNAL_HOSTS", "").split(",")
        if value.strip()
    }
    allow_loopback = (
        os.getenv("WAYLANE_WEBHOOK_ALLOW_LOCAL_TEST") == "1"
        and host in {"127.0.0.1", "::1", "localhost"}
    )
    if parsed.scheme == "http" and host not in allowed_internal and not allow_loopback:
        return None
    if addr is not None and not addr.is_global and host not in allowed_internal and not allow_loopback:
        return None
    return raw


async def authorize_core_subscription(subscription) -> None:
    """Check each Core-managed UUID with the signed receiver; never cache allow."""
    # The sole production caller returns a Subscription ORM instance with both
    # persisted revision fields. Test doubles predating these fields represent
    # legacy independent subscriptions, just like records with both zeros.
    if not (
        getattr(subscription, "entitlement_revision", 0) > 0
        or getattr(subscription, "policy_revision", 0) > 0
    ):
        return
    url = _trusted_auth_url(os.getenv("WAYLANE_CORE_AUTHORIZATION_URL", ""))
    secret = os.getenv("WAYLANE_DEVICE_WEBHOOK_SECRET", "")
    if url is None or len(secret) < 32:
        raise HTTPException(
            status_code=503, detail="Commercial subscription authorization unavailable",
            headers={"Cache-Control": "no-store"},
        )

    body = json.dumps(
        {"subscription_uuid": str(subscription.uuid)}, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    timestamp = str(int(time.time()))
    signed = _PURPOSE + timestamp.encode("ascii") + b"\n" + body
    signature = "sha256=" + hmac.new(secret.encode("utf-8"), signed, sha256).hexdigest()
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(4.0, connect=2.0), follow_redirects=False, trust_env=False
        ) as client:
            response = await client.post(
                url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-WayLane-Timestamp": timestamp,
                    "X-WayLane-Access-Signature": signature,
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503, detail="Commercial subscription authorization unavailable",
            headers={"Cache-Control": "no-store"},
        ) from exc

    if response.status_code == 204:
        return
    if response.status_code == 403:
        raise HTTPException(
            status_code=403, detail="Subscription access denied", headers={"Cache-Control": "no-store"}
        )
    raise HTTPException(
        status_code=503, detail="Commercial subscription authorization unavailable",
        headers={"Cache-Control": "no-store"},
    )
