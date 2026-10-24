"""TLS certificates must remain scoped, validated, and encrypted in storage."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.operation.tls_presets import (
    TlsPresetError,
    decrypt_private_key,
    encrypt_private_key,
    generate_service_token,
    hydrate_tls_config,
    service_token_matches,
    tls_inbounds,
    token_digest,
    validate_certificate_pair,
)


def make_pair(common_name: str = "test.example", expired: bool = False):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .public_key(private.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=10))
        .not_valid_after(now - timedelta(days=1) if expired else now + timedelta(days=30))
        .sign(private, hashes.SHA256())
    )
    return (
        cert.public_bytes(serialization.Encoding.PEM).decode(),
        private.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode(),
    )


def test_certificate_validation_and_pair_mismatch():
    cert, key = make_pair()
    valid = validate_certificate_pair(cert, key)
    assert valid["not_after"] > datetime.now(UTC)
    assert len(valid["fingerprint_sha256"]) == 64
    _, different_key = make_pair("another.example")
    with pytest.raises(TlsPresetError, match="does not match"):
        validate_certificate_pair(cert, different_key)
    old_cert, old_key = make_pair(expired=True)
    with pytest.raises(TlsPresetError, match="expired"):
        validate_certificate_pair(old_cert, old_key)


def test_fernet_encrypts_and_refuses_invalid_key(monkeypatch):
    _, key = make_pair()
    monkeypatch.setenv("WAYLANE_TLS_SECRET_KEY", Fernet.generate_key().decode())
    ciphertext = encrypt_private_key(key)
    assert key not in ciphertext
    assert decrypt_private_key(ciphertext) == key
    monkeypatch.setenv("WAYLANE_TLS_SECRET_KEY", Fernet.generate_key().decode())
    with pytest.raises(TlsPresetError, match="decryption failed"):
        decrypt_private_key(ciphertext)


def test_webhook_scope_and_token_hash():
    token = generate_service_token()
    assert service_token_matches(token, token_digest(token))
    assert not service_token_matches(token + "x", token_digest(token))
    assert not service_token_matches(token, None)
    assert len(token) > 40


def test_node_specific_materialization_does_not_modify_persistent_config(monkeypatch):
    cert, private_key = make_pair()
    monkeypatch.setenv("WAYLANE_TLS_SECRET_KEY", Fernet.generate_key().decode())
    ciphertext = encrypt_private_key(private_key)
    base = {
        "inbounds": [
            {
                "tag": "managed",
                "protocol": "trojan",
                "streamSettings": {"security": "tls", "tlsSettings": {"serverName": "test.example"}},
            },
            {"tag": "unmanaged", "protocol": "vless", "streamSettings": {"security": "none"}},
        ]
    }
    original = json.dumps(base)

    class Results:
        def all(self):
            return [("managed", cert, ciphertext)]

    class Session:
        async def execute(self, _query):
            return Results()

    effective = asyncio.run(hydrate_tls_config(Session(), original, 12, 5))
    assert original == json.dumps(base)
    assert private_key not in original
    resolved = json.loads(effective)
    cert_cfg = resolved["inbounds"][0]["streamSettings"]["tlsSettings"]["certificates"][0]
    assert cert_cfg["certificate"] == cert.splitlines()
    assert cert_cfg["key"] == private_key.splitlines()
    assert resolved["inbounds"][1] == base["inbounds"][1]
    assert tls_inbounds(original) == ["managed"]


def test_binding_to_non_tls_inbound_fails_closed(monkeypatch):
    cert, key = make_pair()
    monkeypatch.setenv("WAYLANE_TLS_SECRET_KEY", Fernet.generate_key().decode())

    class Rows:
        def all(self):
            return [("missing", cert, encrypt_private_key(key))]

    class Session:
        async def execute(self, _):
            return Rows()

    raw = json.dumps({"inbounds": [{"tag": "existing", "streamSettings": {"security": "none"}}]})
    with pytest.raises(TlsPresetError, match="missing"):
        asyncio.run(hydrate_tls_config(Session(), raw, 1, 1))
