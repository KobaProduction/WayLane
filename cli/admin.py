"""Local administration bootstrap commands for WayLane."""

import asyncio
import json
import os
import secrets
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url

from app.db.base import GetDB
from app.db.crud.admin import create_admin
from app.db.crud.temp_key import create_temp_key
from app.db.models import Admin, AdminRole
from app.models.admin import AdminCreate
from cli import console
from config import database_settings


class OwnerInitializationError(Exception):
    """The local owner bootstrap cannot safely proceed."""


def _require_local_development_database(database_url: str) -> None:
    """Never create privileged credentials against another application's database."""
    url = make_url(database_url)
    if (
        url.get_backend_name() != "postgresql"
        or url.host not in {"127.0.0.1", "localhost"}
        or url.port != 15432
        or url.database != "waylane_dev"
    ):
        raise OwnerInitializationError(
            "init-owner is restricted to the local waylane_dev PostgreSQL "
            "database on port 15432"
        )


def _generate_owner_password() -> str:
    """Generate a high-entropy secret satisfying the current password policy."""
    return "WyRk42!_" + secrets.token_urlsafe(24)


def _write_private_credentials(path: Path, username: str, password: str) -> None:
    """Create credentials with 0600 permissions; never overwrite or follow a symlink."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        json.dump({"username": username, "password": password}, output)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())


async def _initialize_local_owner(username: str, credentials_file: Path) -> None:
    """Create the only first admin, using the same CRUD and password hashing as the API."""
    _require_local_development_database(database_settings.url)

    # Credentials must stay in the ignored local development directory.
    runtime_directory = (Path.cwd() / ".runtime").resolve()
    destination = credentials_file.resolve()
    if not destination.is_relative_to(runtime_directory):
        raise OwnerInitializationError("Credentials must be stored inside .runtime/")

    # Validate before writing secrets and before acquiring a DB transaction.
    password = _generate_owner_password()
    owner = AdminCreate(username=username, password=password, role_id=1)

    async with GetDB() as db:
        # Serialize competing local bootstrap attempts until create_admin commits.
        await db.execute(text("SELECT pg_advisory_xact_lock(731409291)"))
        existing = (await db.execute(select(func.count(Admin.id)))).scalar_one()
        if existing:
            raise OwnerInitializationError("Admin accounts already exist; initialization refused")

        owner_role = (await db.execute(select(AdminRole).where(AdminRole.id == 1))).scalar_one_or_none()
        if owner_role is None or not owner_role.is_owner:
            raise OwnerInitializationError("Owner role is missing from the database baseline")

        try:
            _write_private_credentials(credentials_file, owner.username, password)
        except FileExistsError as exc:
            raise OwnerInitializationError(
                "Credentials file already exists; refusing to overwrite it"
            ) from exc
        # The file is durable before the DB commit, so credentials are not lost
        # even if a post-commit ORM refresh fails.
        await create_admin(db, owner)


def init_owner(username: str, credentials_file: Path) -> None:
    """Bootstrap the first local admin; never print generated credentials."""
    try:
        asyncio.run(_initialize_local_owner(username, credentials_file))
    except OwnerInitializationError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc
    console.print(f"[green]First local owner created: {username}[/green]")
    console.print(f"Credentials are saved locally in {credentials_file} (mode 0600).")
    console.print("Use the normal /api/admin/token endpoint to sign in.")


async def _generate_temp_key() -> None:
    async with GetDB() as db:
        key = await create_temp_key(db)
        console.print(f"[bold green]Temp key:[/bold green] {key.key}")
        console.print(f"[yellow]Expires at:[/yellow] {key.expires_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        console.print("[dim]This key is valid for 5 minutes and can only be used once.[/dim]")


def generate_temp_key() -> None:
    """Generate a one-time temp key for owner setup operations."""
    asyncio.run(_generate_temp_key())
