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


def configure_database_url_from_components() -> None:
    """Configure separate runtime and migration DSNs without exposing credentials.

    The managed/full stack uses existing POSTGRES_USER/POSTGRES_PASSWORD names.
    The external two-service stack uses resource-scoped POSTGRES_USERNAME and
    POSTGRES_PASSWORD, with optional explicit POSTGRES_URL overrides.
    """
    runtime_override = os.getenv("POSTGRES_URL")
    migration_override = os.getenv("POSTGRES_MIGRATION_URL") or os.getenv("SQLALCHEMY_MIGRATION_DATABASE_URL")

    username = os.getenv("POSTGRES_USERNAME") or os.getenv("POSTGRES_USER", "")
    password = os.getenv("POSTGRES_PASSWORD", "")
    database = resolved_database_name()

    if not username and not runtime_override:
        runtime_override = os.getenv("SQLALCHEMY_DATABASE_URL")

    if not runtime_override:
        if not username or not password:
            raise SystemExit("PostgreSQL credentials or POSTGRES_URL must be configured")
        runtime_url = URL.create(
            "postgresql+asyncpg",
            username=username,
            password=password,
            host=os.getenv("POSTGRES_HOST", "database"),
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            database=database,
        ).render_as_string(hide_password=False)
    else:
        runtime_url = runtime_override

    if migration_override:
        migration_url = migration_override
    elif username and password:
        migration_url = URL.create(
            "postgresql+asyncpg",
            username=username,
            password=password,
            host=os.getenv("POSTGRES_MIGRATION_HOST", os.getenv("POSTGRES_HOST", "database")),
            port=int(os.getenv("POSTGRES_MIGRATION_PORT", os.getenv("POSTGRES_PORT", "5432"))),
            database=database,
        ).render_as_string(hide_password=False)
    else:
        migration_url = runtime_url

    os.environ["SQLALCHEMY_DATABASE_URL"] = runtime_url
    os.environ["SQLALCHEMY_MIGRATION_DATABASE_URL"] = migration_url


async def ensure_database_exists() -> None:
    """Create a missing database as the WayLane owner, before Alembic runs."""
    username = os.environ["POSTGRES_USER"]
    password = os.environ["POSTGRES_PASSWORD"]
    database = resolved_database_name()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", database):
        raise SystemExit("Invalid PostgreSQL database identifier")

    # Use PostgreSQL directly for DDL; the normal application DSN uses PgBouncer.
    connection = await asyncpg.connect(
        host="postgres",
        port=5432,
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
