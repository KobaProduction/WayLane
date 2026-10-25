"""Guard early WayLane revision-ID reconciliation against accidental data changes."""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import Base
from scripts.reconcile_legacy_waylane_revision import (
    RevisionReconciliationError,
    reconcile_revision,
)


@pytest.mark.parametrize("starting_revision,should_succeed", [
    ("waylane_0004", True),
    ("waylane_0003", False),
    ("c8d2e4f6a1b3", False),
    ("custom_unrecognized", False),
])
def test_preview_revision_requires_matching_schema_and_only_changes_stamp(
    tmp_path, starting_revision, should_succeed
):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'preview.db'}"

    async def exercise():
        engine = create_async_engine(database_url)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            await connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": starting_revision},
            )
            await connection.execute(
                text("INSERT INTO jwt (id, secret_key) VALUES (1, 'preserved-secret')"),
            )
        await engine.dispose()

        if should_succeed:
            result = await reconcile_revision(database_url)
            assert not result.applied
            assert result.old_revision == starting_revision
            assert result.new_revision == "9266245ebd3d"
        else:
            with pytest.raises(RevisionReconciliationError):
                await reconcile_revision(database_url, apply=True)

        engine = create_async_engine(database_url)
        async with engine.connect() as connection:
            assert (await connection.scalar(text("SELECT version_num FROM alembic_version"))) == starting_revision
            assert (await connection.scalar(text("SELECT secret_key FROM jwt WHERE id=1"))) == "preserved-secret"
        await engine.dispose()

        if should_succeed:
            result = await reconcile_revision(database_url, apply=True)
            assert result.applied
            engine = create_async_engine(database_url)
            async with engine.connect() as connection:
                assert (await connection.scalar(text("SELECT version_num FROM alembic_version"))) == "9266245ebd3d"
                assert (await connection.scalar(text("SELECT secret_key FROM jwt WHERE id=1"))) == "preserved-secret"
            await engine.dispose()
            with pytest.raises(RevisionReconciliationError):
                await reconcile_revision(database_url, apply=True)

    asyncio.run(exercise())
