import hmac
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import jwt
from aiocache import cached

from app.db import GetDB
from app.db.crud.general import get_jwt_secret_key
from config import jwt_settings


@cached()
async def get_secret_key():
    async with GetDB() as db:
        key = await get_jwt_secret_key(db=db)
        return key


def _admin_token_binding(hashed_password: str, secret_key: str) -> str:
    return hmac.new(secret_key.encode(), b"admin-token-binding:" + hashed_password.encode(), sha256).hexdigest()


def get_admin_token_binding(hashed_password: str, secret_key: str) -> str:
    """Derive an admin binding with the key already used to verify the token."""
    return _admin_token_binding(hashed_password, secret_key)


async def create_admin_token(admin_id: int | None, username: str, hashed_password: str | None = None) -> str:
    data = {"sub": username, "access": "admin", "iat": datetime.now(UTC)}
    secret_key = await get_secret_key()
    if admin_id is not None:
        if not hashed_password:
            raise ValueError("Database admin tokens require a password hash")
        data["aid"] = int(admin_id)
        data["binding"] = _admin_token_binding(hashed_password, secret_key)
    if jwt_settings.access_token_expire_minutes > 0:
        expire = datetime.now(UTC) + timedelta(minutes=jwt_settings.access_token_expire_minutes)
        data["exp"] = expire
    encoded_jwt = jwt.encode(data, secret_key, algorithm="HS256")
    return encoded_jwt


async def get_admin_payload(token: str, *, secret_key: str | None = None) -> dict | None:
    """Decode an admin token, optionally reusing a previously loaded signing key."""
    try:
        if secret_key is None:
            secret_key = await get_secret_key()
        payload = jwt.decode(token, secret_key, algorithms=["HS256"], leeway=5)
        username: str = payload.get("sub")
        access: str = payload.get("access")
        admin_id = payload.get("aid")
        binding = payload.get("binding")
        if binding is not None and not isinstance(binding, str):
            return
        if admin_id is not None:
            try:
                admin_id = int(admin_id)
            except (TypeError, ValueError):
                return
        if not username or access not in ("admin", "sudo"):
            return
        try:
            created_at = datetime.fromtimestamp(payload["iat"], tz=UTC)
        except KeyError:
            created_at = None

        return {
            "admin_id": admin_id,
            "username": username,
            "created_at": created_at,
            "binding": binding,
        }
    except jwt.exceptions.PyJWTError:
        return
