"""Source-level guarantees for ordinary and Coolify Compose variants."""
import importlib.util
from pathlib import Path

import pytest
import yaml
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[3]


def read(path):
    return yaml.safe_load((ROOT / path).read_text())


def test_bundled_stack_requires_credentials_and_uses_persistent_private_db():
    doc = read("docker-compose.yaml")
    services = doc["services"]
    assert set(services) == {"postgres", "pgbouncer", "backend", "frontend"}
    assert services["postgres"]["environment"]["POSTGRES_USER"] == "${WAYLANE_POSTGRES_USERNAME:-waylane}"
    assert services["postgres"]["environment"]["POSTGRES_PASSWORD"] == "${WAYLANE_POSTGRES_PASSWORD:?}"
    assert services["postgres"]["environment"]["POSTGRES_DB"] == "${WAYLANE_POSTGRES_DATABASE:-waylane}"
    assert services["backend"]["environment"]["POSTGRES_DB"] == "${WAYLANE_POSTGRES_DATABASE:-waylane}"
    assert services["backend"]["environment"]["POSTGRES_USER"] == services["postgres"]["environment"]["POSTGRES_USER"]
    assert services["backend"]["environment"]["POSTGRES_PASSWORD"] == services["postgres"]["environment"]["POSTGRES_PASSWORD"]
    assert "SQLALCHEMY_DATABASE_URL" not in services["backend"]["environment"]
    assert "ports" not in services["postgres"]
    assert "ports" not in services["backend"]
    assert services["postgres"]["networks"] == ["database"]
    pool = services["pgbouncer"]
    assert pool["networks"]["database"]["aliases"] == ["database"]
    assert pool["environment"]["POSTGRES_USER"] == services["postgres"]["environment"]["POSTGRES_USER"]
    assert pool["environment"]["POSTGRES_PASSWORD"] == services["postgres"]["environment"]["POSTGRES_PASSWORD"]
    assert "ports" not in pool
    assert pool["user"] == "10001:10001"
    assert services["backend"]["environment"]["POSTGRES_HOST"] == "database"
    assert "postgres" not in services["backend"]["depends_on"]
    assert services["backend"]["depends_on"]["pgbouncer"]["condition"] == "service_healthy"
    assert doc["networks"]["database"]["internal"] is True
    assert "postgres_data:/var/lib/postgresql/data" in services["postgres"]["volumes"]
    assert services["backend"]["entrypoint"] == [
        "/code/.venv/bin/python", "/code/compose_db_entrypoint.py"
    ]
    ignore = (ROOT / "services/backend/.dockerignore").read_text().splitlines()
    assert "compose_db_entrypoint.py" not in ignore
    assert "scripts" in ignore  # Regression: keep script outside ignored path.
    assert (ROOT / "services/backend/compose_db_entrypoint.py").is_file()
    assert services["backend"]["environment"]["UVICORN_HOST"] == "waylane-api"
    assert services["backend"]["networks"]["application"]["aliases"] == ["waylane-api"]
    assert "waylane-api:8000/health" in services["backend"]["healthcheck"]["test"][1]
    assert services["frontend"]["ports"] == ["127.0.0.1:8080:8080"]
    assert services["frontend"]["build"]["args"]["VITE_BASE_API"] == "/"


def test_extends_adapters_redeclare_top_level_networks_and_volumes():
    bundled = read("docker-compose.coolify.yaml")
    assert bundled["networks"]["database"]["internal"] is True
    assert set(bundled["networks"]) == {"application", "database"}
    assert set(bundled["volumes"]) == {"postgres_data", "backend_data"}
    for service in ("postgres", "pgbouncer", "backend"):
        env = bundled["services"][service]["environment"]
        assert env["POSTGRES_USER"] == "${WAYLANE_POSTGRES_USERNAME:-waylane}"
        assert env["POSTGRES_PASSWORD"] == "${WAYLANE_POSTGRES_PASSWORD:?}"
        assert env["POSTGRES_DB"] == "${WAYLANE_POSTGRES_DATABASE:-waylane}"

    backend = read("services/backend/docker-compose.coolify.yaml")
    assert "backend_data" in backend["volumes"]


def test_bundled_postgres_dsn_handles_uri_reserved_password(monkeypatch):
    filename = ROOT / "services/backend/compose_db_entrypoint.py"
    spec = importlib.util.spec_from_file_location("waylane_bundled_dsn", filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("POSTGRES_USER", "username")
    monkeypatch.setenv("POSTGRES_PASSWORD", "ab:@/#!+%&£")
    monkeypatch.setenv("POSTGRES_DB", "custom-db")
    monkeypatch.setenv("POSTGRES_HOST", "database")

    class Executed(Exception):
        pass

    def capture(path, argv):
        assert (path, argv) == ("/code/start.sh", ["/code/start.sh"])
        raise Executed

    monkeypatch.setattr(module.os, "execv", capture)
    with pytest.raises(Executed):
        module.main()
    url = make_url(module.os.environ["SQLALCHEMY_DATABASE_URL"])
    assert url.username == "username"
    assert url.password == "ab:@/#!+%&£"
    assert url.host == "database"
    assert url.database == "custom-db"
    monkeypatch.delenv("POSTGRES_PASSWORD")
    with pytest.raises(SystemExit):
        module.main()


def test_split_apps_do_not_bundle_postgres_or_other_service():
    backend = read("services/backend/docker-compose.yaml")
    frontend = read("services/frontend/docker-compose.yaml")
    assert set(backend["services"]) == {"backend"}
    assert set(frontend["services"]) == {"frontend"}
    assert backend["services"]["backend"]["environment"]["SQLALCHEMY_DATABASE_URL"] == "${SQLALCHEMY_DATABASE_URL:?}"
    assert backend["services"]["backend"]["environment"]["UVICORN_HOST"] == "backend"
    assert "backend:8000/health" in backend["services"]["backend"]["healthcheck"]["test"][1]
    assert "POSTGRES_PASSWORD" not in backend["services"]["backend"]["environment"]
    assert frontend["services"]["frontend"]["build"]["args"]["VITE_BASE_API"] == "${VITE_BASE_API:-/}"
    assert "environment" not in frontend["services"]["frontend"]
    assert frontend["services"]["frontend"]["ports"] == ["127.0.0.1:8081:8080"]


@pytest.mark.parametrize(
    ("adapter", "expected", "forbidden"),
    [
        (
            "docker-compose.coolify.yaml",
            {"services/backend/**", "services/frontend/**", "docker-compose.yaml"},
            {"services/node/**"},
        ),
        (
            "services/backend/docker-compose.coolify.yaml",
            {"services/backend/**"},
            {"services/frontend/**", "services/node/**"},
        ),
        (
            "services/frontend/docker-compose.coolify.yaml",
            {"services/frontend/**"},
            {"services/backend/**", "services/node/**"},
        ),
    ],
)
def test_coolify_watch_scopes_and_extend_source(adapter, expected, forbidden):
    manifest = read(adapter)
    watched = set(manifest["x-watch-path-coolify"])
    assert watched >= expected
    assert not (watched & forbidden)
    assert all(s["extends"]["file"] == "docker-compose.yaml" for s in manifest["services"].values())


def test_coolify_bundled_frontend_declares_only_public_managed_route():
    doc = read("docker-compose.coolify.yaml")
    assert doc["services"]["frontend"]["environment"] == {
        "SERVICE_FQDN_FRONTEND_8080": "/",
    }
    assert "environment" not in doc["services"]["postgres"] or (
        "SERVICE_FQDN_POSTGRES_5432" not in doc["services"]["postgres"]["environment"]
    )
    assert "SERVICE_FQDN_BACKEND_8000" not in doc["services"]["backend"]["environment"]


def test_bundle_subscription_access_logging_off():
    content = (ROOT / "deploy/compose/all-in-one/nginx.conf").read_text()
    assert "location /sub/" in content
    subscription_location = content.split("location /sub/ {", 1)[1].split("location =", 1)[0]
    assert "access_log off;" in subscription_location


def test_root_ingress_routes_browser_telemetry_to_backend():
    content = (ROOT / "deploy/compose/all-in-one/nginx.conf").read_text()
    assert "location /api/" in content
    assert "location /sub/" in content
    assert "proxy_pass http://backend:8000" in content
    assert "server_tokens off;" in content


def test_runtime_includes_webhook_http_client_dependency():
    import tomllib

    config = tomllib.loads((ROOT / 'services/backend/pyproject.toml').read_text())
    runtime = config['project']['dependencies']
    assert any(value.startswith('httpx') for value in runtime)


def test_managed_backend_pooler_is_private_and_shares_only_approved_credentials():
    doc = read("deploy/backend-postgres/docker-compose.yaml")
    services = doc["services"]
    assert set(services) == {"postgres", "pgbouncer", "backend"}
    assert doc["networks"]["database"]["internal"] is True
    assert "ports" not in services["postgres"]
    assert "ports" not in services["pgbouncer"]
    assert services["pgbouncer"]["networks"]["database"]["aliases"] == ["database"]
    assert services["backend"]["environment"]["POSTGRES_HOST"] == "database"
    assert services["backend"]["ports"] == ["127.0.0.1:8000:8000"]
    assert services["postgres"]["volumes"] == ["postgres_data:/var/lib/postgresql/data"]
    for service in ("postgres", "pgbouncer", "backend"):
        env = services[service]["environment"]
        assert env["POSTGRES_PASSWORD"] == "${WAYLANE_POSTGRES_PASSWORD:?}"
        assert env["POSTGRES_USER"] == "${WAYLANE_POSTGRES_USERNAME:-waylane}"
        assert env["POSTGRES_DB"] == "${WAYLANE_POSTGRES_DATABASE:-waylane}"
    adapter = read("deploy/backend-postgres/docker-compose.coolify.yaml")
    assert set(adapter["services"]) == set(services)
    for service in services:
        assert adapter["services"][service]["extends"]["file"] == "docker-compose.yaml"
    assert "deploy/compose/pgbouncer/**" in adapter["x-watch-path-coolify"]
    assert "services/frontend/**" not in adapter["x-watch-path-coolify"]


def test_split_frontend_has_one_coolify_domain_and_never_backend_credentials():
    adapter = read("services/frontend/docker-compose.coolify.yaml")
    env = adapter["services"]["frontend"]["environment"]
    assert env == {"SERVICE_FQDN_FRONTEND_8080": "/"}
    assert adapter["services"]["frontend"]["build"]["args"]["VITE_BASE_API"] == "${VITE_BASE_API:-/}"


def test_node_is_an_independent_required_authenticated_resource():
    generic = read("services/node/docker-compose.yaml")
    coolify = read("services/node/docker-compose.coolify.yaml")
    assert set(generic["services"]) == {"node"}
    svc = generic["services"]["node"]
    assert svc["environment"]["API_KEY"] == "${API_KEY:?}"
    assert svc["network_mode"] == "host"
    assert svc["cap_add"] == ["NET_ADMIN"]
    assert svc.get("privileged") is not True
    assert "/var/lib/pg-node:/var/lib/pg-node" in svc["volumes"]
    assert coolify["services"]["node"]["environment"]["API_KEY"] == "${API_KEY:?}"
    assert coolify["x-watch-path-coolify"] == ["services/node/**"]
    assert "SERVICE_FQDN_NODE_62050" not in coolify["services"]["node"]["environment"]


def test_pgbouncer_config_is_session_pooling_scram_and_never_trust_auth():
    from pathlib import Path
    source = (ROOT / "deploy/compose/pgbouncer/start.py").read_text()
    assert "auth_type = scram-sha-256" in source
    assert "pool_mode = session" in source
    assert "listen_port = 5432" in source
    assert "host=postgres port=5432" in source
    assert "os.chmod(auth_file, 0o600)" in source
    assert "password" in source
    assert "auth_type = trust" not in source


@pytest.mark.parametrize("exists", [True, False])
@pytest.mark.asyncio
async def test_backend_creates_only_missing_database(monkeypatch, exists):
    from unittest.mock import AsyncMock

    path = ROOT / "services/backend/compose_db_entrypoint.py"
    spec = importlib.util.spec_from_file_location("waylane_owner_db", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("POSTGRES_USER", "waylane")
    monkeypatch.setenv("POSTGRES_PASSWORD", "test_only_password")
    monkeypatch.setenv("POSTGRES_DB", "waylane")
    connection = AsyncMock()
    connection.fetchval.return_value = exists
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr(module.asyncpg, "connect", connect)

    await module.ensure_database_exists()

    assert connect.await_args.kwargs["host"] == "postgres"
    assert connect.await_args.kwargs["database"] == "postgres"
    assert connect.await_args.kwargs["user"] == "waylane"
    connection.fetchval.assert_awaited_once_with(
        "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = $1)", "waylane"
    )
    if exists:
        connection.execute.assert_not_awaited()
    else:
        connection.execute.assert_awaited_once_with('CREATE DATABASE "waylane"')
    connection.close.assert_awaited_once()
