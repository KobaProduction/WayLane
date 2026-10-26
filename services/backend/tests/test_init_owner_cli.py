"""Safety checks for the local CLI owner bootstrap."""

import json
import stat

import pytest

from cli.admin import (
    OwnerInitializationError,
    _generate_owner_password,
    _require_local_development_database,
    _write_private_credentials,
)
from app.models.admin import AdminCreate


def test_local_database_is_the_only_supported_bootstrap_target():
    _require_local_development_database(
        "postgresql+asyncpg://waylane:hidden@127.0.0.1:15432/waylane_dev"
    )
    for candidate in (
        "postgresql+asyncpg://waylane:hidden@db.example.com:15432/waylane_dev",
        "postgresql+asyncpg://waylane:hidden@127.0.0.1:5432/waylane_dev",
        "postgresql+asyncpg://waylane:hidden@127.0.0.1:15432/production",
        "sqlite+aiosqlite:///db.sqlite3",
    ):
        with pytest.raises(OwnerInitializationError):
            _require_local_development_database(candidate)


def test_generated_password_meets_normal_admin_validation():
    AdminCreate(username="waylane_dev_owner", password=_generate_owner_password(), role_id=1)


def test_credentials_file_is_private_and_cannot_be_overwritten(tmp_path):
    dest = tmp_path / "dev" / "credentials.json"
    _write_private_credentials(dest, "dev", "never-printed")
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600
    assert json.loads(dest.read_text()) == {"username": "dev", "password": "never-printed"}
    with pytest.raises(FileExistsError):
        _write_private_credentials(dest, "other", "other")
    assert json.loads(dest.read_text())["username"] == "dev"


def test_credentials_symlink_is_not_followed(tmp_path):
    target = tmp_path / "target.json"
    target.write_text("untouched")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises((FileExistsError, OSError)):
        _write_private_credentials(link, "owner", "password")
    assert target.read_text() == "untouched"
