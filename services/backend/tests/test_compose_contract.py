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
    assert set(services) == {"postgres", "backend", "frontend"}
    assert services["postgres"]["environment"]["POSTGRES_USER"] == "${POSTGRES_USER:?}"
    assert services["postgres"]["environment"]["POSTGRES_PASSWORD"] == "${POSTGRES_PASSWORD:?}"
    assert services["postgres"]["environment"]["POSTGRES_DB"] == "${POSTGRES_DB:-waylane}"
    assert services["backend"]["environment"]["POSTGRES_DB"] == "${POSTGRES_DB:-waylane}"
    assert "SQLALCHEMY_DATABASE_URL" not in services["backend"]["environment"]
    assert "ports" not in services["postgres"]
    assert "ports" not in services["backend"]
    assert services["postgres"]["networks"] == ["database"]
    assert doc["networks"]["database"]["internal"] is True
    assert "postgres_data:/var/lib/postgresql/data" in services["postgres"]["volumes"]
    assert services["backend"]["entrypoint"] == [
        "/code/.venv/bin/python", "/code/compose_db_entrypoint.py"
    ]
    ignore = (ROOT / "services/backend/.dockerignore").read_text().splitlines()
    assert "compose_db_entrypoint.py" not in ignore
    assert "scripts" in ignore  # Regression: keep script outside ignored path.
    assert (ROOT / "services/backend/compose_db_entrypoint.py").is_file()
    assert services["frontend"]["ports"] == ["127.0.0.1:8080:8080"]
    assert services["frontend"]["build"]["args"]["VITE_BASE_API"] == "/"


def test_bundled_postgres_dsn_handles_uri_reserved_password(monkeypatch):
    filename = ROOT / "services/backend/compose_db_entrypoint.py"
    spec = importlib.util.spec_from_file_location("waylane_bundled_dsn", filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("POSTGRES_USER", "username")
    monkeypatch.setenv("POSTGRES_PASSWORD", "ab:@/#!+%&£")
    monkeypatch.setenv("POSTGRES_DB", "custom-db")

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
    assert url.host == "postgres"
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


def test_root_ingress_routes_browser_telemetry_to_backend():
    content = (ROOT / "deploy/compose/all-in-one/nginx.conf").read_text()
    assert "location /api/" in content
    assert "location /sub/" in content
    assert "proxy_pass http://backend:8000" in content
    assert "server_tokens off;" in content
