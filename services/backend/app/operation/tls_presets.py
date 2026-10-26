"""TLS X.509 validation, protected storage, and per-node config materialization."""

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime

from cryptography import x509
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes, serialization
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.tls import tls_bindings, tls_presets


class TlsPresetError(ValueError):
    """A TLS preset or target is invalid."""


def _vault() -> Fernet:
    key = os.environ.get("WAYLANE_TLS_SECRET_KEY")
    if not key:
        raise TlsPresetError("WAYLANE_TLS_SECRET_KEY is not configured")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise TlsPresetError("TLS encryption key must be a valid Fernet key") from exc


def validate_certificate_pair(certificate: str, private_key: str) -> dict:
    if len(certificate.encode()) > 65536 or len(private_key.encode()) > 16384:
        raise TlsPresetError("Certificate or private key exceeds the supported size")
    try:
        cert = x509.load_pem_x509_certificate(certificate.encode())
        key = serialization.load_pem_private_key(private_key.encode(), password=None)
        pub = cert.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        key_pub = key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    except (ValueError, TypeError) as exc:
        raise TlsPresetError("Certificate or private key is not valid unencrypted PEM") from exc
    if not hmac.compare_digest(pub, key_pub):
        raise TlsPresetError("Certificate does not match the private key")
    expiry = cert.not_valid_after_utc
    if expiry <= datetime.now(UTC):
        raise TlsPresetError("Certificate has expired")
    return {
        "fingerprint_sha256": cert.fingerprint(hashes.SHA256()).hex(),
        "subject": cert.subject.rfc4514_string()[:512],
        "not_after": expiry,
    }


def encrypt_private_key(private_key: str) -> str:
    return _vault().encrypt(private_key.encode()).decode("ascii")


def decrypt_private_key(ciphertext: str) -> str:
    try:
        return _vault().decrypt(ciphertext.encode("ascii")).decode()
    except InvalidToken as exc:
        raise TlsPresetError("TLS key decryption failed; verify the deployment key") from exc


def generate_service_token() -> str:
    import secrets
    return "wltls_" + secrets.token_urlsafe(36)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def service_token_matches(token: str, expected_hash: str | None) -> bool:
    return bool(expected_hash and token.startswith("wltls_") and hmac.compare_digest(token_digest(token), expected_hash))


def tls_inbounds(config: dict | str) -> list[str]:
    if isinstance(config, str):
        config = json.loads(config)
    return [
        inbound["tag"]
        for inbound in config.get("inbounds", [])
        if isinstance(inbound, dict)
        and isinstance(inbound.get("tag"), str)
        and (inbound.get("streamSettings") or {}).get("security") == "tls"
    ]


async def hydrate_tls_config(db: AsyncSession, raw_config: str, node_id: int, core_id: int) -> str:
    """Expand only this node's bound TLS certificates immediately before its Start RPC.

    The persistent core JSON and public API never contain decrypted private keys.
    No bind means the original core is returned without any changes.
    """
    query = (
        select(tls_bindings.c.inbound_tag, tls_presets.c.certificate_pem, tls_presets.c.encrypted_private_key)
        .select_from(tls_bindings.join(tls_presets, tls_bindings.c.preset_id == tls_presets.c.id))
        .where(tls_bindings.c.node_id == node_id, tls_bindings.c.core_id == core_id)
    )
    rows = (await db.execute(query)).all()
    if not rows:
        return raw_config
    config = json.loads(raw_config)
    indexed = {inbound.get("tag"): inbound for inbound in config.get("inbounds", []) if isinstance(inbound, dict)}
    for tag, cert, encrypted_key in rows:
        inbound = indexed.get(tag)
        if not inbound:
            raise TlsPresetError(f"Bound TLS inbound {tag!r} is missing")
        stream = inbound.get("streamSettings") or {}
        if stream.get("security") != "tls":
            raise TlsPresetError(f"Bound TLS inbound {tag!r} is not configured for TLS")
        tls_settings = stream.setdefault("tlsSettings", {})
        # Explicit managed certificate replaces ad-hoc inline/file certificates
        # only in this ephemeral, node-facing payload.
        tls_settings["certificates"] = [
            {
                "certificate": cert.splitlines(),
                "key": decrypt_private_key(encrypted_key).splitlines(),
                "usage": "encipherment",
            }
        ]
    return json.dumps(config, separators=(",", ":"))


async def list_bindings(db: AsyncSession, preset_id: str) -> list[dict]:
    rows = (await db.execute(select(tls_bindings).where(tls_bindings.c.preset_id == preset_id))).mappings().all()
    return [dict(row) for row in rows]
