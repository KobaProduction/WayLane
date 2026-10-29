"""Two-service external deployment, optional telemetry and split migration DSNs."""

import importlib.util
from pathlib import Path

import pytest
import yaml
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = ROOT / "services/backend/compose_db_entrypoint.py"


def load_entrypoint():
    spec = importlib.util.spec_from_file_location("waylane_external_db_entrypoint", ENTRYPOINT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_external_compose_contains_only_api_ui_with_external_network_and_existing_volume():
    base = yaml.safe_load((ROOT / "deploy/external/docker-compose.yaml").read_text())
    adapter = yaml.safe_load((ROOT / "deploy/external/docker-compose.coolify.yaml").read_text())
    assert set(base["services"]) == {"backend", "frontend"}
    assert set(adapter["services"]) == {"backend", "frontend"}
    assert base["networks"]["dependencies"] == {"external": True, "name": "${EXTERNAL_NETWORK_NAME:?}"}
    assert adapter["networks"]["dependencies"] == base["networks"]["dependencies"]
    assert base["services"]["backend"]["networks"]["dependencies"]["aliases"] == ["waylane-api"]
    assert base["services"]["frontend"]["networks"] == ["dependencies", "egress"]
    assert base["services"]["backend"]["volumes"] == ["waylane-data:/var/lib/pasarguard"]
    assert set(adapter["volumes"]) == {"waylane-data"}
    assert base["services"]["backend"]["environment"]["POSTGRES_AUTO_CREATE_DB"] == "false"
    assert base["services"]["frontend"]["build"]["args"]["NGINX_CONFIG"] == "deploy/nginx.bundled.conf"
    assert adapter["services"]["frontend"]["environment"] == {"SERVICE_URL_FRONTEND_8080": "/"}
    for key in (
        "POSTGRES_USERNAME",
        "POSTGRES_PASSWORD",
        "POSTGRES_DATABASE",
        "POSTGRES_URL",
        "POSTGRES_MIGRATION_URL",
        "POSTGRES_MIGRATION_HOST",
        "OTLP_ENDPOINT",
        "OTLP_BEARER_TOKEN",
    ):
        assert key in adapter["services"]["backend"]["environment"]
    assert "services/node/**" not in adapter["x-watch-path-coolify"]
    for name, value in base["services"].items():
        assert "ports" not in value, name


def test_database_components_build_separate_encoded_pgbouncer_and_postgres_urls(monkeypatch):
    for key in (
        "POSTGRES_URL",
        "POSTGRES_MIGRATION_URL",
        "SQLALCHEMY_DATABASE_URL",
        "SQLALCHEMY_MIGRATION_DATABASE_URL",
        "POSTGRES_USER",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("POSTGRES_USERNAME", "waylane_login")
    monkeypatch.setenv("POSTGRES_PASSWORD", "pass:@/#!+%&")
    monkeypatch.setenv("POSTGRES_DATABASE", "waylane_db")
    monkeypatch.setenv("POSTGRES_HOST", "database")
    monkeypatch.setenv("POSTGRES_MIGRATION_HOST", "postgres")
    module = load_entrypoint()
    module.configure_database_url_from_components()
    normal = make_url(module.os.environ["SQLALCHEMY_DATABASE_URL"])
    migration = make_url(module.os.environ["SQLALCHEMY_MIGRATION_DATABASE_URL"])
    assert normal.host == "database"
    assert migration.host == "postgres"
    for value in (normal, migration):
        assert value.username == "waylane_login"
        assert value.password == "pass:@/#!+%&"
        assert value.database == "waylane_db"
        assert value.get_backend_name() == "postgresql"


def test_explicit_urls_override_components_and_one_url_can_serve_both(monkeypatch):
    for key in (
        "POSTGRES_USERNAME",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_MIGRATION_URL",
        "SQLALCHEMY_MIGRATION_DATABASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://user:secret@database:5432/waylane")
    module = load_entrypoint()
    module.configure_database_url_from_components()
    assert module.os.environ["SQLALCHEMY_DATABASE_URL"] == module.os.environ["SQLALCHEMY_MIGRATION_DATABASE_URL"]
    monkeypatch.setenv("POSTGRES_MIGRATION_URL", "postgresql+asyncpg://user:secret@postgres:5432/waylane")
    module.configure_database_url_from_components()
    assert make_url(module.os.environ["SQLALCHEMY_MIGRATION_DATABASE_URL"]).host == "postgres"


def test_missing_database_credentials_and_url_fails_closed(monkeypatch):
    for key in ("POSTGRES_USERNAME", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_URL", "SQLALCHEMY_DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(SystemExit, match="PostgreSQL credentials"):
        load_entrypoint().configure_database_url_from_components()
