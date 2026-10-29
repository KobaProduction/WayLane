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
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_MIGRATION_HOST",
        "EXTERNAL_NETWORK_NAME",
        "OTLP_ENDPOINT",
        "OTLP_BEARER_TOKEN",
    ):
        assert key in adapter["services"]["backend"]["environment"]
    assert "services/node/**" not in adapter["x-watch-path-coolify"]
    for name, value in base["services"].items():
        assert "ports" not in value, name


def test_host_port_endpoints_produce_separate_encoded_urls(monkeypatch):
    for key in ("SQLALCHEMY_DATABASE_URL", "SQLALCHEMY_MIGRATION_DATABASE_URL", "POSTGRES_USER"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("POSTGRES_USERNAME", "waylane_login")
    monkeypatch.setenv("POSTGRES_PASSWORD", "pass:@/#!+%&")
    monkeypatch.setenv("POSTGRES_DATABASE", "waylane_db")
    monkeypatch.setenv("POSTGRES_HOST", "database")
    monkeypatch.setenv("POSTGRES_PORT", "6432")
    monkeypatch.setenv("POSTGRES_MIGRATION_HOST", "postgres")
    module = load_entrypoint()
    module.configure_database_url_from_components()
    normal = make_url(module.os.environ["SQLALCHEMY_DATABASE_URL"])
    migration = make_url(module.os.environ["SQLALCHEMY_MIGRATION_DATABASE_URL"])
    assert (normal.host, normal.port) == ("database", 6432)
    assert (migration.host, migration.port) == ("postgres", 6432)
    for url in (normal, migration):
        assert url.username == "waylane_login"
        assert url.password == "pass:@/#!+%&"
        assert url.database == "waylane_db"
        assert url.drivername == "postgresql+asyncpg"


def test_blank_migration_endpoint_reuses_runtime_address(monkeypatch):
    monkeypatch.setenv("POSTGRES_USERNAME", "login")
    monkeypatch.setenv("POSTGRES_PASSWORD", "test-synthetic-password")
    monkeypatch.setenv("POSTGRES_HOST", "db-host")
    monkeypatch.setenv("POSTGRES_PORT", "5444")
    monkeypatch.setenv("POSTGRES_MIGRATION_HOST", "")
    module = load_entrypoint()
    module.configure_database_url_from_components()
    assert module.os.environ["SQLALCHEMY_DATABASE_URL"] == module.os.environ["SQLALCHEMY_MIGRATION_DATABASE_URL"]


@pytest.mark.parametrize(
    "host",
    [
        "postgresql+asyncpg://postgres:5432/db",
        "postgres:5432",
        "postgres/db",
        "user@postgres",
        "postgres?password=x",
        "",
        "example.invalid:5432",
    ],
)
def test_invalid_postgres_hostname_rejected(host):
    with pytest.raises(SystemExit, match="POSTGRES_HOST"):
        load_entrypoint()._postgres_host(host, "POSTGRES_HOST")


@pytest.mark.parametrize("port", ["", "abc", "0", "65536", "-1", "5432x"])
def test_invalid_postgres_port_rejected(monkeypatch, port):
    monkeypatch.setenv("POSTGRES_PORT", port)
    with pytest.raises(SystemExit, match="POSTGRES_PORT"):
        load_entrypoint()._postgres_port()


def test_missing_database_credentials_fails_closed(monkeypatch):
    for key in ("POSTGRES_USERNAME", "POSTGRES_USER", "POSTGRES_PASSWORD", "SQLALCHEMY_DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("POSTGRES_HOST", "database")
    with pytest.raises(SystemExit, match="POSTGRES_USERNAME"):
        load_entrypoint().configure_database_url_from_components()
