#!/usr/bin/env python3
"""Build the backend DSN and optionally initialize its own database.

Deployment adapters may supply POSTGRES_* credentials and opt into owner-created
database initialization. Standalone deployments continue to use their own
SQLALCHEMY_DATABASE_URL without enabling this optional feature.
"""

import asyncio
import os
import re

import asyncpg
from asyncpg.exceptions import DuplicateDatabaseError
from sqlalchemy.engine import URL


def resolved_database_name() -> str:
    """Resolve the schema-owning database independently of its connection path."""
    return (
        os.getenv("POSTGRES_DATABASE")
        or os.getenv("WAYLANE_POSTGRES_DATABASE")
        or os.getenv("POSTGRES_DB")
        or "waylane"
    )


def _postgres_port() -> int:
    """Use the same explicit PostgreSQL port for runtime and migrations."""
    value = os.getenv("POSTGRES_PORT", "5432")
    if not value.isdecimal() or not 1 <= int(value) <= 65535:
        raise SystemExit("POSTGRES_PORT must be an integer between 1 and 65535")
    return int(value)


def _postgres_host(value: str, setting: str) -> str:
    """Validate a bare database host, never an operator-provided URL."""
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise SystemExit(f"{setting} must be a hostname or IPv4 address")
    return value


def configure_database_url_from_components() -> None:
    """Build internal SQLAlchemy DSNs using the VPN Core-style host/port inputs."""
    username = os.getenv("POSTGRES_USERNAME") or os.getenv("POSTGRES_USER", "")
    password = os.getenv("POSTGRES_PASSWORD", "")
    database = resolved_database_name()

    if not username or not password:
        # Only the already-supported standalone backend mode accepts a
        # preassembled SQLAlchemy DSN; grouped Compose never exposes one.
        legacy_url = os.getenv("SQLALCHEMY_DATABASE_URL")
        if legacy_url and not username and not password:
            os.environ["SQLALCHEMY_MIGRATION_DATABASE_URL"] = (
                os.getenv("SQLALCHEMY_MIGRATION_DATABASE_URL") or legacy_url
            )
            return
        raise SystemExit("POSTGRES_USERNAME and POSTGRES_PASSWORD are required")

    host = _postgres_host(os.getenv("POSTGRES_HOST") or "database", "POSTGRES_HOST")
    migration_host = _postgres_host(
        os.getenv("POSTGRES_MIGRATION_HOST") or host,
        "POSTGRES_MIGRATION_HOST",
    )
    port = _postgres_port()

    def build_dsn(hostname: str) -> str:
        return URL.create(
            "postgresql+asyncpg",
            username=username,
            password=password,
            host=hostname,
            port=port,
            database=database,
        ).render_as_string(hide_password=False)

    os.environ["SQLALCHEMY_DATABASE_URL"] = build_dsn(host)
    os.environ["SQLALCHEMY_MIGRATION_DATABASE_URL"] = build_dsn(migration_host)


async def ensure_database_exists() -> None:
    """Create a missing database as the WayLane owner, before Alembic runs."""
    username = os.environ["POSTGRES_USER"]
    password = os.environ["POSTGRES_PASSWORD"]
    database = resolved_database_name()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", database):
        raise SystemExit("Invalid PostgreSQL database identifier")

    # Use PostgreSQL directly for DDL; the normal application DSN uses PgBouncer.
    connection = await asyncpg.connect(
        host=_postgres_host(os.getenv("POSTGRES_MIGRATION_HOST") or "postgres", "POSTGRES_MIGRATION_HOST"),
        port=_postgres_port(),
        user=username,
        password=password,
        database="postgres",
        timeout=10,
    )
    try:
        exists = await connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = $1)",
            database,
        )
        if not exists:
            try:
                await connection.execute(f'CREATE DATABASE "{database}"')
            except DuplicateDatabaseError:
                # Another initializer created the database between check and DDL.
                pass
    finally:
        await connection.close()


def main() -> None:
    configure_database_url_from_components()
    if os.getenv("POSTGRES_AUTO_CREATE_DB") == "true":
        asyncio.run(ensure_database_exists())
    os.execv("/code/start.sh", ["/code/start.sh"])


if __name__ == "__main__":
    main()
