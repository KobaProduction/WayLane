"""UUID subscription links with independently revocable 256-bit access codes.

The link contains no technical user ID, username, contact or VPN credentials.
The 16-byte per-subscription nonce prevents old links from becoming valid
when a technical node identity is recreated under the same subscription UUID.
"""

import base64
import hmac
import re
from hashlib import sha256
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Subscription
from app.utils.jwt import get_secret_key

_TOKEN_PATTERN = re.compile(r"^([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\.([A-Za-z0-9_-]{43})$")


def _access_code(subscription: Subscription, signing_key: str) -> str:
    payload = (
        b"waylane:subscription-access:v1:\n"
        + subscription.uuid.bytes
        + subscription.access_generation.to_bytes(8, "big")
        + subscription.access_nonce
    )
    digest = hmac.new(signing_key.encode("utf-8"), payload, sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


async def subscription_token(subscription: Subscription) -> str:
    return f"{subscription.uuid}.{_access_code(subscription, await get_secret_key())}"


async def subscription_from_token(db: AsyncSession, token: str) -> Subscription | None:
    match = _TOKEN_PATTERN.fullmatch(token)
    if match is None:
        return None
    sid = UUID(match.group(1))
    if sid.version != 4 or str(sid) != match.group(1):
        return None
    subscription = (await db.execute(select(Subscription).where(Subscription.uuid == sid))).scalar_one_or_none()
    if subscription is None:
        return None
    expected = _access_code(subscription, await get_secret_key())
    return subscription if hmac.compare_digest(match.group(2), expected) else None
