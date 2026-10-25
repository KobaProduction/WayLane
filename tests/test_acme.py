"""The OpenFlare-inspired ACME adapter never calls a real CA in unit tests."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.operation import acme
from app.operation.tls_presets import decrypt_private_key


def certificate_pair(domains: list[str]) -> tuple[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domains[0])])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=75))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(domain) for domain in domains]), False)
        .sign(key, hashes.SHA256())
    )
    return (
        cert.public_bytes(serialization.Encoding.PEM).decode(),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    )


def test_acme_dns_and_email_input_validation():
    assert acme.normalize_domains(["EXAMPLE.COM.", "*.example.com", "example.com"]) == [
        "example.com", "*.example.com"
    ]
    for domains in (["127.0.0.1"], ["example.com;rm -rf /"], ["localhost"], [], ["*.com"]):
        with pytest.raises(acme.AcmeError):
            acme.normalize_domains(domains)
    assert acme.validate_email("certs@example.org") == "certs@example.org"
    with pytest.raises(acme.AcmeError):
        acme.validate_email("invalid-email")


def test_openflare_seven_day_renewal_window():
    now = datetime.now(UTC)
    assert acme.is_renewal_due(now + timedelta(days=5), now)
    assert not acme.is_renewal_due(now + timedelta(days=10), now)
    assert acme.is_renewal_due(None, now)


def test_acme_account_state_encrypted_at_rest(monkeypatch, tmp_path):
    monkeypatch.setenv("WAYLANE_TLS_SECRET_KEY", Fernet.generate_key().decode())
    directory = tmp_path / "plain"
    (directory / "accounts").mkdir(parents=True)
    (directory / "accounts" / "private.pem").write_text("highly-private-test-key")
    encrypted = acme._archive(directory)
    assert "highly-private-test-key" not in encrypted
    dest = tmp_path / "restored"
    dest.mkdir()
    acme._restore(encrypted, dest)
    assert (dest / "accounts" / "private.pem").read_text() == "highly-private-test-key"


def test_lego_v5_cloudflare_dns01_and_staging(monkeypatch, tmp_path):
    domains = ["example.org", "*.example.org"]
    cert, key = certificate_pair(domains)
    dummy = tmp_path / "lego"
    dummy.write_text("#!/bin/sh\nexit 0\n")
    dummy.chmod(0o700)
    monkeypatch.setenv("WAYLANE_LEGO_BIN", str(dummy))
    monkeypatch.setenv("WAYLANE_ACME_WORK_DIR", str(tmp_path / "acme"))
    monkeypatch.setenv("WAYLANE_TLS_SECRET_KEY", Fernet.generate_key().decode())
    recorded = {}

    def fake_run(args, *, env, cwd, capture_output, timeout, check):
        assert args[0] == str(dummy)
        assert args[1] == "run"
        assert "--dns" in args and args[args.index("--dns") + 1] == "cloudflare"
        assert "--server" in args and args[args.index("--server") + 1] == "letsencrypt-staging"
        assert args.count("--domains") == 2
        assert env["CF_DNS_API_TOKEN"] == "fake-private-token"
        assert env["CF_ZONE_API_TOKEN"] == "fake-zone-read-token"
        assert not any("fake-private-token" in str(value) for value in args)
        path = Path(args[args.index("--path") + 1]) / "certificates"
        path.mkdir()
        (path / "order-example.crt").write_text(cert)
        (path / "order-example.key").write_text(key)
        account = Path(cwd) / "accounts"
        account.mkdir()
        (account / "account.key").write_text("private-account-key")
        recorded["staging"] = True
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(acme.subprocess, "run", fake_run)
    certificate, private, state = acme._run_lego(
        order_id="order-example",
        email="certs@example.org",
        domains=domains,
        environment="staging",
        dns_token="fake-private-token",
        zone_token="fake-zone-read-token",
        encrypted_state=None,
    )
    assert recorded["staging"]
    assert certificate == cert and private == key
    restored = tmp_path / "restored"
    restored.mkdir()
    acme._restore(state, restored)
    assert (restored / "accounts" / "account.key").read_text() == "private-account-key"
    assert not list((tmp_path / "acme").iterdir()), "Unencrypted temp files must be deleted"


def test_lego_failure_does_not_leak_provider_secret(monkeypatch, tmp_path):
    dummy = tmp_path / "lego"
    dummy.write_text("fail")
    monkeypatch.setenv("WAYLANE_LEGO_BIN", str(dummy))
    monkeypatch.setenv("WAYLANE_ACME_WORK_DIR", str(tmp_path / "acme"))
    monkeypatch.setattr(acme.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=23, stderr=b"fake secret"))
    with pytest.raises(acme.AcmeError) as exc:
        acme._run_lego(
            order_id="id",
            email="certs@example.org",
            domains=["example.org"],
            environment="staging",
            dns_token="fake-secret-token",
            zone_token=None,
            encrypted_state=None,
        )
    assert "fake-secret-token" not in str(exc.value)
    assert "fake secret" not in str(exc.value)


def test_issued_certificate_covers_requested_san_domains():
    cert, _ = certificate_pair(["example.org"])
    with pytest.raises(acme.AcmeError):
        acme._certificate_matches_domains(cert, ["example.org", "*.example.org"])
    acme._certificate_matches_domains(cert, ["example.org"])


def test_persistent_order_issuance_and_renewal_updates_tls_preset(monkeypatch, tmp_path):
    """Real SQLAlchemy lifecycle (SQLite), fake DNS-01: no CA/DNS network use."""
    import asyncio
    import uuid
    from contextlib import asynccontextmanager

    from sqlalchemy import insert, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import Base
    from app.db.acme import acme_dns_accounts, acme_orders
    from app.db.tls import tls_presets

    first_cert, first_key = certificate_pair(["example.org"])
    renewed_cert, renewed_key = certificate_pair(["example.org"])
    dbfile = tmp_path / "acme.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{dbfile}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def test_db():
        async with session_factory() as session:
            yield session

    call_count = 0

    def fake_issuer(**kwargs):
        nonlocal call_count
        call_count += 1
        assert kwargs["environment"] == "staging"
        assert kwargs["dns_token"] == "private-token-for-fixture"
        assert kwargs["domains"] == ["example.org"]
        return (*((first_cert, first_key) if call_count == 1 else (renewed_cert, renewed_key)), "encrypted-lego-account-state")

    monkeypatch.setenv("WAYLANE_TLS_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(acme, "GetDB", test_db)
    monkeypatch.setattr(acme, "_run_lego", fake_issuer)

    async def exercise():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        order_id = str(uuid.uuid4())
        dns_id = str(uuid.uuid4())
        async with session_factory() as db:
            await db.execute(insert(acme_dns_accounts).values(
                id=dns_id, name="test-cloudflare", provider="cloudflare",
                encrypted_dns_token=acme.encrypt_private_key("private-token-for-fixture"),
            ))
            await db.execute(insert(acme_orders).values(
                id=order_id, name="example.org TLS", dns_account_id=dns_id,
                email="certs@example.org", domains=["example.org"], environment="staging",
                auto_renew=True, status="pending",
            ))
            await db.commit()
        await acme.issue_order(order_id)
        async with session_factory() as db:
            order = (await db.execute(select(acme_orders))).mappings().one()
            assert order["status"] == "ready", order["last_error"]
            assert order["next_renewal_at"] is not None
            assert order["encrypted_lego_state"] == "encrypted-lego-account-state"
            preset = (await db.execute(select(tls_presets))).mappings().one()
            assert order["preset_id"] == preset["id"]
            assert preset["revision"] == 1
            assert preset["certificate_pem"] == first_cert
            assert "BEGIN PRIVATE KEY" not in preset["encrypted_private_key"]
            assert decrypt_private_key(preset["encrypted_private_key"]) == first_key

        await acme.issue_order(order_id)
        async with session_factory() as db:
            order = (await db.execute(select(acme_orders))).mappings().one()
            assert order["status"] == "ready", order["last_error"]
            assert order["preset_id"] == preset["id"]
            renewed = (await db.execute(select(tls_presets))).mappings().one()
            assert renewed["revision"] == 2
            assert renewed["certificate_pem"] == renewed_cert
            assert decrypt_private_key(renewed["encrypted_private_key"]) == renewed_key
            assert await db.scalar(select(acme_orders.c.id)) == order_id
        await engine.dispose()

    asyncio.run(exercise())
    assert call_count == 2


def test_one_shot_recovery_selects_only_abandoned_and_due_orders(monkeypatch, tmp_path):
    """The external one-shot worker recovers abandoned first issues and expired leases."""
    import asyncio
    from contextlib import asynccontextmanager
    from uuid import uuid4

    from sqlalchemy import insert
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import Base
    from app.db.acme import acme_dns_accounts, acme_orders

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def test_db():
        async with sessions() as db:
            yield db

    monkeypatch.setattr(acme, "GetDB", test_db)
    now = datetime.now(UTC)

    async def exercise():
        async with engine.begin() as db:
            await db.run_sync(Base.metadata.create_all)
        dns_id = str(uuid4())
        rows = {
            "abandoned_pending": dict(status="pending", auto_renew=False, created_at=now - timedelta(minutes=5)),
            "fresh_pending": dict(status="pending", auto_renew=False, created_at=now),
            "expired_running": dict(status="running", auto_renew=False, lease_until=now - timedelta(minutes=1)),
            "active_running": dict(status="running", auto_renew=True, lease_until=now + timedelta(minutes=5)),
            "ready_due": dict(status="ready", auto_renew=True, next_renewal_at=now - timedelta(minutes=1)),
            "ready_later": dict(status="ready", auto_renew=True, next_renewal_at=now + timedelta(days=10)),
            "ready_disabled": dict(status="ready", auto_renew=False, next_renewal_at=now - timedelta(days=1)),
            "retry_due": dict(status="error", auto_renew=True, next_renewal_at=now - timedelta(minutes=1)),
            "retry_later": dict(status="error", auto_renew=True, next_renewal_at=now + timedelta(minutes=50)),
        }
        async with sessions() as db:
            await db.execute(insert(acme_dns_accounts).values(
                id=dns_id, name="recovery", provider="cloudflare", encrypted_dns_token="fixture"))
            for name, attrs in rows.items():
                await db.execute(insert(acme_orders).values(
                    id=name, name=name, dns_account_id=dns_id,
                    email="test@example.org", domains=["example.org"], environment="staging",
                    **attrs,
                ))
            await db.commit()
        selected = set(await acme.due_orders())
        assert selected == {"abandoned_pending", "expired_running", "ready_due", "retry_due"}
        await engine.dispose()

    asyncio.run(exercise())


@pytest.mark.parametrize("stale_fails", [False, True])
def test_stale_acme_worker_cannot_overwrite_newer_issuance(monkeypatch, tmp_path, stale_fails):
    """A timed-out worker completing late cannot overwrite the new lease owner."""
    import asyncio
    import threading
    from contextlib import asynccontextmanager
    from uuid import uuid4

    from sqlalchemy import insert, select, update
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import Base
    from app.db.acme import acme_dns_accounts, acme_orders
    from app.db.tls import tls_presets

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fencing.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    cert_old, key_old = certificate_pair(["example.org"])
    cert_new, key_new = certificate_pair(["example.org"])
    first_started = threading.Event()
    finish_first = threading.Event()
    calls = 0

    @asynccontextmanager
    async def test_db():
        async with sessions() as db:
            yield db

    def fake_issuer(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            if not finish_first.wait(timeout=15):
                raise TimeoutError("test fixture timeout")
            if stale_fails:
                raise acme.AcmeError("Superseded attempt failed")
            return cert_old, key_old, "old-state"
        return cert_new, key_new, "new-state"

    monkeypatch.setenv("WAYLANE_TLS_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(acme, "GetDB", test_db)
    monkeypatch.setattr(acme, "_run_lego", fake_issuer)

    async def exercise():
        async with engine.begin() as db:
            await db.run_sync(Base.metadata.create_all)
        order_id, dns_id = str(uuid4()), str(uuid4())
        async with sessions() as db:
            await db.execute(insert(acme_dns_accounts).values(
                id=dns_id, name="fencing", provider="cloudflare",
                encrypted_dns_token=acme.encrypt_private_key("fixture-private-token")))
            await db.execute(insert(acme_orders).values(
                id=order_id, name="fenced-order", dns_account_id=dns_id,
                email="test@example.org", domains=["example.org"], environment="staging",
                auto_renew=True, status="pending"))
            await db.commit()
        first_task = asyncio.create_task(acme.issue_order(order_id))
        try:
            assert await asyncio.wait_for(asyncio.to_thread(first_started.wait, 10), timeout=11)
            async with sessions() as db:
                await db.execute(update(acme_orders).where(acme_orders.c.id == order_id).values(
                    lease_until=datetime.now(UTC) - timedelta(minutes=1)))
                await db.commit()
            await acme.issue_order(order_id)
        finally:
            finish_first.set()
            await first_task
        async with sessions() as db:
            order = (await db.execute(select(acme_orders))).mappings().one()
            preset = (await db.execute(select(tls_presets))).mappings().one()
            assert order["status"] == "ready"
            assert order["lease_token"] is None
            assert order["encrypted_lego_state"] == "new-state"
            assert order["preset_id"] == preset["id"]
            assert preset["revision"] == 1
            assert preset["certificate_pem"] == cert_new
            assert decrypt_private_key(preset["encrypted_private_key"]) == key_new
        await engine.dispose()

    asyncio.run(exercise())
    assert calls == 2
