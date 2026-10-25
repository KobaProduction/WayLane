"""Cloudflare DNS-01 ACME issuance adapted from OpenFlare's lego workflow.

No OpenFlare source is copied. Lego v5 is invoked with arguments (no shell);
DNS tokens, ACME account state and certificate private keys are encrypted.
The CA is restricted to Let's Encrypt staging and production.
"""

import asyncio
import io
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from cryptography import x509
from sqlalchemy import and_, insert, or_, select, update

from app.db import GetDB
from app.db.acme import acme_dns_accounts, acme_orders
from app.db.tls import tls_presets
from app.operation.tls_presets import (
    _vault,
    decrypt_private_key,
    encrypt_private_key,
    list_bindings,
    validate_certificate_pair,
)

ACME_RENEW_BEFORE = timedelta(days=7)  # OpenFlare's established renewal lead time.
ACME_TIMEOUT_SECONDS = 420
DOMAIN_PATTERN = re.compile(r"^(?:\*\.)?(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class AcmeError(ValueError):
    """An ACME request or local runner failed safely."""


def normalize_domains(domains: list[str]) -> list[str]:
    if not 1 <= len(domains) <= 20:
        raise AcmeError("Specify between 1 and 20 DNS domain names")
    result = []
    for domain in domains:
        domain = domain.strip().lower().rstrip(".")
        if not DOMAIN_PATTERN.fullmatch(domain):
            raise AcmeError(f"Invalid public DNS domain: {domain[:100]}")
        if domain not in result:
            result.append(domain)
    return result


def validate_email(email: str) -> str:
    email = email.strip()
    if len(email) > 254 or not EMAIL_PATTERN.fullmatch(email):
        raise AcmeError("A valid ACME contact email is required")
    return email


def is_renewal_due(not_after: datetime | None, now: datetime | None = None) -> bool:
    if not not_after:
        return True
    if not_after.tzinfo is None:
        not_after = not_after.replace(tzinfo=UTC)
    return not_after <= (now or datetime.now(UTC)) + ACME_RENEW_BEFORE


def _secure_dir() -> Path:
    root = Path(os.environ.get("WAYLANE_ACME_WORK_DIR", ".runtime/acme")).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.stat().st_mode & 0o077:
        root.chmod(0o700)
    return root


def _archive(directory: Path) -> str:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as tar:
        for child in directory.rglob("*"):
            if child.is_file() and not child.is_symlink():
                tar.add(child, arcname=child.relative_to(directory), recursive=False)
    return _vault().encrypt(stream.getvalue()).decode()


def _restore(archive: str, directory: Path) -> None:
    from cryptography.fernet import InvalidToken

    try:
        data = _vault().decrypt(archive.encode())
    except InvalidToken as exc:
        raise AcmeError("Cannot decrypt persisted ACME account state") from exc
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile() or member.name.startswith("/") or ".." in Path(member.name).parts:
                raise AcmeError("Invalid ACME state archive")
        tar.extractall(directory, filter="data")


def _certificate_matches_domains(pem: str, domains: list[str]) -> None:
    try:
        cert = x509.load_pem_x509_certificate(pem.encode())
        names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(
            x509.DNSName
        )
    except (ValueError, x509.ExtensionNotFound) as exc:
        raise AcmeError("ACME issuer returned a certificate without DNS subject alternative names") from exc
    if not set(domains).issubset({name.lower() for name in names}):
        raise AcmeError("ACME certificate does not include all requested DNS names")


def _run_lego(
    *,
    order_id: str,
    email: str,
    domains: list[str],
    environment: str,
    dns_token: str,
    zone_token: str | None,
    encrypted_state: str | None,
) -> tuple[str, str, str]:
    """Return fullchain, private key and encrypted lego state. No secret output."""
    if environment not in {"staging", "production"}:
        raise AcmeError("Unknown ACME environment")
    if not dns_token:
        raise AcmeError("Cloudflare DNS API token is missing")
    executable = os.environ.get("WAYLANE_LEGO_BIN") or shutil.which("lego")
    if not executable or not Path(executable).is_file():
        raise AcmeError("lego v5 CLI is not installed/configured (WAYLANE_LEGO_BIN)")
    with tempfile.TemporaryDirectory(prefix="issue-", dir=_secure_dir()) as temp:
        home = Path(temp)
        if encrypted_state:
            _restore(encrypted_state, home)
        args = [
            executable,
            "run",
            "--path", str(home),
            "--cert.name", order_id,
            "--dns", "cloudflare",
            "--server", "letsencrypt-staging" if environment == "staging" else "letsencrypt",
            "--email", email,
            "--accept-tos",
            "--key-type", "EC256",
        ]
        for domain in domains:
            args.extend(("--domains", domain))
        # Do not pass account credentials on the command line or in logs.
        process_env = {key: value for key, value in os.environ.items() if not key.startswith("CF_")}
        process_env["CF_DNS_API_TOKEN"] = dns_token
        if zone_token:
            process_env["CF_ZONE_API_TOKEN"] = zone_token
        try:
            result = subprocess.run(
                args,
                env=process_env,
                cwd=home,
                capture_output=True,
                timeout=ACME_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AcmeError("ACME DNS-01 attempt timed out") from exc
        except OSError as exc:
            raise AcmeError("ACME client could not be executed") from exc
        if result.returncode != 0:
            # Raw lego output might disclose a provider token, so never return it.
            raise AcmeError(f"ACME client failed (exit status {result.returncode}); check DNS zone permissions")
        certificate = home / "certificates" / f"{order_id}.crt"
        private_key = home / "certificates" / f"{order_id}.key"
        if not certificate.is_file() or not private_key.is_file():
            raise AcmeError("ACME client finished without expected certificate files")
        cert_text = certificate.read_text()
        key_text = private_key.read_text()
        validate_certificate_pair(cert_text, key_text)
        _certificate_matches_domains(cert_text, domains)
        return cert_text, key_text, _archive(home)


async def issue_order(order_id: str) -> None:
    """One issued order at a time; status is persistent for the frontend."""
    async with GetDB() as db:
        now = datetime.now(UTC)
        lease = now + timedelta(seconds=ACME_TIMEOUT_SECONDS + 30)
        lease_token = str(uuid4())
        claimed = await db.execute(
            update(acme_orders)
            .where(
                acme_orders.c.id == order_id,
                or_(
                    acme_orders.c.status != "running",
                    acme_orders.c.lease_until.is_(None),
                    acme_orders.c.lease_until < now,
                ),
            )
            .values(status="running", last_error=None, last_attempt_at=now, lease_until=lease, lease_token=lease_token)
            .returning(acme_orders.c.id)
        )
        if claimed.first() is None:
            return
        await db.commit()

    try:
        async with GetDB() as db:
            order = (await db.execute(select(acme_orders).where(acme_orders.c.id == order_id))).mappings().one()
            dns = (await db.execute(
                select(acme_dns_accounts).where(acme_dns_accounts.c.id == order["dns_account_id"])
            )).mappings().one()
        credentials = decrypt_private_key(dns["encrypted_dns_token"])
        zone_token = decrypt_private_key(dns["encrypted_zone_token"]) if dns["encrypted_zone_token"] else None
        cert, key, new_state = await asyncio.to_thread(
            _run_lego,
            order_id=order_id,
            email=order["email"],
            domains=normalize_domains(order["domains"]),
            environment=order["environment"],
            dns_token=credentials,
            zone_token=zone_token,
            encrypted_state=order["encrypted_lego_state"],
        )
        info = validate_certificate_pair(cert, key)
        async with GetDB() as db:
            # Fencing: take the order row lock and reread ownership *before*
            # modifying either the preset or the order. A superseded worker
            # must not publish its stale certificate or clear the newer lease.
            current = (await db.execute(
                select(acme_orders).where(acme_orders.c.id == order_id).with_for_update()
            )).mappings().first()
            if not current or current["status"] != "running" or current["lease_token"] != lease_token:
                return
            preset_id = current["preset_id"] or str(uuid4())
            previous = None
            if current["preset_id"]:
                previous = (await db.execute(
                    select(tls_presets).where(tls_presets.c.id == preset_id)
                )).mappings().one()
            if previous is None:
                await db.execute(insert(tls_presets).values(
                    id=preset_id, name=order["name"], certificate_pem=cert,
                    encrypted_private_key=encrypt_private_key(key), **info,
                ))
            elif previous["fingerprint_sha256"] != info["fingerprint_sha256"]:
                await db.execute(update(tls_presets).where(tls_presets.c.id == preset_id).values(
                    certificate_pem=cert, encrypted_private_key=encrypt_private_key(key),
                    revision=previous["revision"] + 1, updated_at=datetime.now(UTC), **info,
                ))
            await db.execute(
                update(acme_orders).where(acme_orders.c.id == order_id).values(
                    status="ready",
                    preset_id=preset_id,
                    encrypted_lego_state=new_state,
                    last_error=None,
                    lease_until=None,
                    lease_token=None,
                    next_renewal_at=info["not_after"] - ACME_RENEW_BEFORE,
                )
            )
            await db.commit()
            from app.routers.tls_presets import _apply
            bindings = await list_bindings(db, preset_id)
            if bindings:
                await _apply(db, [binding["node_id"] for binding in bindings])
    except Exception as exc:
        # Never persist DNS/ACME credentials or subprocess output as errors.
        if isinstance(exc, AcmeError):
            msg = str(exc)[:256]
        else:
            msg = "ACME issue or deployment failed; check server diagnostics"
        async with GetDB() as db:
            await db.execute(update(acme_orders).where(
                acme_orders.c.id == order_id,
                acme_orders.c.status == "running",
                acme_orders.c.lease_token == lease_token,
            ).values(
                status="error", last_error=msg,
                lease_until=None, lease_token=None,
                next_renewal_at=datetime.now(UTC) + timedelta(hours=1),
            ))
            await db.commit()


async def due_orders() -> list[str]:
    """Select due renewals, abandoned first issues and expired leases.

    This is a one-shot recovery scan, not an internal scheduler. Pending
    initial orders are recovered even when subsequent auto-renew is disabled.
    """
    now = datetime.now(UTC)
    pending_grace = now - timedelta(minutes=1)
    async with GetDB() as db:
        stmt = (
            select(acme_orders.c.id)
            .where(or_(
                and_(acme_orders.c.status == "pending", acme_orders.c.created_at <= pending_grace),
                and_(acme_orders.c.status == "running", acme_orders.c.lease_until < now),
                and_(
                    acme_orders.c.auto_renew.is_(True),
                    acme_orders.c.status.in_(("ready", "error")),
                    acme_orders.c.next_renewal_at <= now,
                ),
            ))
            .order_by(acme_orders.c.created_at, acme_orders.c.id)
            .limit(100)
        )
        return list((await db.scalars(stmt)).all())


async def run_due_once() -> int:
    jobs = await due_orders()
    for order_id in jobs:
        await issue_order(order_id)
    return len(jobs)
