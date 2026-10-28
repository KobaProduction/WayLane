#!/usr/bin/env python3
"""Construct a PostgreSQL DSN without exposing the password in Compose.

Used only by bundled PostgreSQL topology. Standalone backend deployments
consume the existing SQLALCHEMY_DATABASE_URL, avoiding credential aliases.
"""
import os

from sqlalchemy.engine import URL


def configure_database_url_from_components() -> None:
    """Recreate the API database URL for one-shot managed CLI commands."""
    username = os.getenv("POSTGRES_USER", "")
    password = os.getenv("POSTGRES_PASSWORD", "")
    database = os.getenv("POSTGRES_DB") or "waylane"
    if not username or not password:
        raise SystemExit("POSTGRES_USER and POSTGRES_PASSWORD must be nonempty")
    url = URL.create(
        "postgresql+asyncpg",
        username=username, password=password, host=os.getenv("POSTGRES_HOST", "postgres"),
        port=5432, database=database,
    )
    os.environ["SQLALCHEMY_DATABASE_URL"] = url.render_as_string(hide_password=False)


def main() -> None:
    configure_database_url_from_components()
    os.execv("/code/start.sh", ["/code/start.sh"])


if __name__ == "__main__":
    main()
