"""Git-owned independent WayLane resources for one isolated Coolify environment.

These checks verify SOURCE only; actual Coolify parsing, Shared variable
resolution, network membership and live resource isolation require acceptance.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def compose(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text())


def test_postgres_is_only_stable_dependency_and_never_host_published():
    base = compose("deploy/dependencies/docker-compose.yaml")
    adapter = compose("deploy/dependencies/docker-compose.coolify.yaml")
    assert set(base["services"]) == {"waylane-postgres"}
    assert set(adapter["services"]) == {"waylane-postgres"}
    service = base["services"]["waylane-postgres"]
    assert service["image"] == "postgres:17-alpine"
    assert service["environment"] == {
        "POSTGRES_USER": "${POSTGRES_USERNAME:-waylane}",
        "POSTGRES_PASSWORD": "${POSTGRES_PASSWORD:?}",
        "POSTGRES_DB": "${POSTGRES_DATABASE:-waylane}",
    }
    assert service["volumes"] == ["postgres-data:/var/lib/postgresql/data"]
    assert service["expose"] == ["5432"] and "ports" not in service
    assert set(base["volumes"]) == set(adapter["volumes"]) == {"postgres-data"}
    assert "healthcheck" in service
    assert adapter["services"]["waylane-postgres"]["extends"] == {
        "file": "docker-compose.yaml",
        "service": "waylane-postgres",
    }
    assert adapter["services"]["waylane-postgres"]["environment"] == service["environment"]
    assert "x-watch-path-coolify" not in adapter
    assert not any("valkey" in name.lower() for name in base["services"])


def test_applications_are_independent_without_parser_watch_hints():
    backend = compose("services/backend/docker-compose.coolify.yaml")
    frontend = compose("services/frontend/docker-compose.coolify.yaml")
    dependencies = compose("deploy/dependencies/docker-compose.coolify.yaml")
    # Auto-deploy strategy is owned by Coolify: dependencies remain manual,
    # while application changes may trigger without restricted file paths.
    assert all("x-watch-path-coolify" not in c for c in (dependencies, backend, frontend))
    assert set(backend["services"]) == {"waylane-api"}
    assert set(frontend["services"]) == {"frontend"}
    assert all("ports" not in s for c in (dependencies, backend, frontend) for s in c["services"].values())


def test_backend_secret_boundaries_and_otlp_contract():
    b = compose("services/backend/docker-compose.coolify.yaml")
    env = b["services"]["waylane-api"]["environment"]
    assert env["POSTGRES_PASSWORD"] == "${POSTGRES_PASSWORD:?}"
    assert env["WAYLANE_TLS_SECRET_KEY"] == "${WAYLANE_TLS_SECRET_KEY:?}"
    assert env["WAYLANE_DEVICE_WEBHOOK_SECRET"] == "${WAYLANE_DEVICE_WEBHOOK_SECRET:?}"
    assert env["OTEL_ENDPOINT"] == "${OTEL_ENDPOINT:?}"
    assert env["OTEL_BEARER_TOKEN"] == "${OTEL_BEARER_TOKEN:?}"
    assert "SQLALCHEMY_DATABASE_URL" not in env
    assert "SERVICE_URL_WAYLANE_API_8000" not in env
    f = compose("services/frontend/docker-compose.coolify.yaml")
    assert f["services"]["frontend"]["environment"] == {"SERVICE_URL_FRONTEND_8080": "/"}
    assert "OTEL_BEARER_TOKEN" not in repr(f)
    assert "POSTGRES_PASSWORD" not in repr(f)
    assert "WAYLANE_TLS_SECRET_KEY" not in repr(f)


def test_frontend_same_origin_api_and_node_wss_proxy():
    conf = (ROOT / "services/frontend/deploy/nginx.bundled.conf").read_text()
    assert "location /api/" in conf
    assert "resolver 127.0.0.11 valid=10s" in conf
    assert "set $waylane_api_upstream waylane-api:8000;" in conf
    assert conf.count("proxy_pass http://$waylane_api_upstream;") == 3
    ws = conf.split("location = /api/node-control/control/ws {", 1)[1].split("location /api/", 1)[0]
    assert "proxy_set_header Upgrade $http_upgrade;" in ws
    assert 'proxy_set_header Connection "upgrade";' in ws
    assert "access_log off;" in ws
    sub = conf.split("location /sub/ {", 1)[1].split("location = /index.html", 1)[0]
    assert "access_log off;" in sub
    assert "waylane-postgres" not in conf
    for nginx_config in (
        "services/frontend/deploy/nginx.conf",
        "services/frontend/deploy/nginx.bundled.conf",
    ):
        config = (ROOT / nginx_config).read_text()
        assert '"service.namespace":"WayLane","service.name":"frontend"' in config
        assert '"service.name":"waylane-frontend"' not in config


def test_existing_node_remains_independently_deployed():
    node = compose("services/node/docker-compose.node.discovery.coolify.yaml")
    assert set(node["services"]) == {"bootstrap", "agent"}
    assert "DISCOVERY_TOKEN" in node["services"]["agent"]["environment"]
    assert "services/node/**" in node["x-watch-path-coolify"]
    for unrelated in ("services/frontend/**", "services/backend/**", "deploy/dependencies/**"):
        assert unrelated not in node["x-watch-path-coolify"]
