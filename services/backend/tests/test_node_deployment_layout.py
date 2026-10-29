"""Git-owned WayLane Node deployment sources remain independent and synchronized."""

from copy import deepcopy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
MODES = ("classic", "discovery")
WATCHED_NODE_SOURCE = ("services/node/**",)


def _read(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text())


def test_node_deployments_are_outside_service_source_and_have_single_entrypoints():
    for obsolete in (
        "docker-compose.node.yaml",
        "docker-compose.node.classic.yaml",
        "docker-compose.node.classic.coolify.yaml",
        "docker-compose.node.discovery.yaml",
        "docker-compose.node.discovery.coolify.yaml",
    ):
        assert not (ROOT / "services/node" / obsolete).exists()

    assert (ROOT / "deploy/node/README.md").is_file()
    for mode in MODES:
        deployment = ROOT / "deploy/node" / mode
        assert {p.name for p in deployment.iterdir() if p.is_file()} == {
            "docker-compose.yaml",
            "docker-compose.coolify.yaml",
        }
        # The shared deployment README is outside watched mode folders:
        # a documentation-only edit should not trigger redeployment.
        text = (ROOT / "deploy/node/README.md").read_text()
        assert text.count("## Discovery") == 1
        assert text.count("## Classic") == 1
        fences = chr(96) * 3
        watch_block = text.split(f"**{mode.title()}:**", 1)[1]
        watch_block = watch_block.split(fences + "text", 1)[1].split(fences, 1)[0].strip()
        assert watch_block.splitlines() == [
            f"deploy/node/{mode}/*.yaml",
            "services/node/**",
            "!services/node/**/*.md",
        ]
        assert "Base Directory" in text and f"/deploy/node/{mode}" in text
        assert "/docker-compose.coolify.yaml" in text


def test_bootstrap_is_a_single_root_script_with_no_separate_source_folder():
    node = ROOT / "services/node"
    assert not (node / "bootstrap").exists()
    assert (node / "bootstrap.sh").is_file()
    assert (node / "bootstrap.Dockerfile").is_file()
    assert "sh -n bootstrap.sh" in (node / "scripts/verify.sh").read_text()
    dockerfile = (node / "bootstrap.Dockerfile").read_text()
    assert "COPY services/node/bootstrap.sh" in dockerfile
    assert "ENTRYPOINT" in dockerfile
    assert not (node / "agent").exists(), "No nested agent Go module or source directory"
    assert (node / "cmd/node/main.go").is_file()
    assert (node / "node.Dockerfile").is_file()
    image = (node / "node.Dockerfile").read_text()
    assert "COPY services/node/go.mod services/node/go.sum" in image
    assert "COPY services/node/cmd/node" in image
    assert "go build -trimpath" in image
    assert not (node / "cmd/agent").exists()
    assert not (node / "backend").exists()
    assert not (node / "controller").exists()
    assert not (node / "common").exists()
    assert not (node / "cmd/node/go.mod").exists()


def test_classic_and_discovery_share_identical_node_runtime_and_security():
    bases = []
    for mode in MODES:
        source = _read(f"deploy/node/{mode}/docker-compose.yaml")
        assert set(source) == {"services", "volumes"}, "No fixed project name or unrelated x-fields"
        assert set(source["services"]) == {"bootstrap", "agent"}
        assert set(source["volumes"]) == {"agent-state", "rootless-data", "rootless-run"}
        bootstrap, agent = source["services"]["bootstrap"], source["services"]["agent"]
        assert bootstrap["privileged"] is True and bootstrap["restart"] == "no"
        assert bootstrap["network_mode"] == "none"
        assert agent["user"] == "1000:1000" and not agent.get("privileged")
        assert agent["depends_on"]["bootstrap"]["condition"] == "service_completed_successfully"
        assert "ports" not in agent and "ports" not in bootstrap
        assert agent["cap_drop"] == ["ALL"]
        assert agent["read_only"] is True
        assert any(
            isinstance(v, dict) and v["source"] == "/etc/hostname"
            and v["target"] == "/etc/waylane/host-hostname" and v["read_only"]
            for v in agent["volumes"]
        )
        assert not any("/var/run/docker.sock" in str(v) for v in agent["volumes"])
        for unit, dockerfile in (("agent", "services/node/node.Dockerfile"), (
            "bootstrap", "services/node/bootstrap.Dockerfile",
        )):
            build = source["services"][unit]["build"]
            assert build["dockerfile"] == dockerfile
            assert (ROOT / "deploy/node" / mode / build["context"]).resolve() == ROOT.resolve()
        env = agent["environment"]
        assert env["CONTROL_PANEL_URL"] == "${CONTROL_PANEL_URL:?}"
        assert env["DEV_ALLOW_HTTP"] == "${DEV_ALLOW_HTTP:-false}"
        assert "CONTROL_TRANSPORT" not in env and "GRPC_TARGET" not in env
        assert "NODE_NAME" not in env
        required = "NODE_TOKEN" if mode == "classic" else "DISCOVERY_TOKEN"
        empty = "DISCOVERY_TOKEN" if mode == "classic" else "NODE_TOKEN"
        assert env[required] == "${" + required + ":?}"
        assert env[empty] == ""
        normalized = deepcopy(source)
        normalized["services"]["agent"]["environment"]["NODE_TOKEN"] = "CREDENTIAL"
        normalized["services"]["agent"]["environment"]["DISCOVERY_TOKEN"] = "CREDENTIAL"
        bases.append(normalized)
    assert bases[0] == bases[1], "Classic and Discovery runtime/security settings diverged"


def test_coolify_adapters_extend_own_generic_compose_and_redeclare_inputs():
    for mode in MODES:
        doc = _read(f"deploy/node/{mode}/docker-compose.coolify.yaml")
        assert set(doc) == {"services", "volumes"}
        assert set(doc["services"]) == {"bootstrap", "agent"}
        assert set(doc["volumes"]) == {"agent-state", "rootless-data", "rootless-run"}
        bootstrap, agent = doc["services"]["bootstrap"], doc["services"]["agent"]
        for name, service in (("bootstrap", bootstrap), ("agent", agent)):
            assert service["extends"] == {"file": "./docker-compose.yaml", "service": name}
        assert bootstrap["exclude_from_hc"] is True
        env = agent["environment"]
        assert env["CONTROL_PANEL_URL"] == "${CONTROL_PANEL_URL:?}"
        assert env["DEV_ALLOW_HTTP"] == "false"
        assert env["NODE_TOKEN"] == ("${NODE_TOKEN:?}" if mode == "classic" else "")
        assert env["DISCOVERY_TOKEN"] == ("${DISCOVERY_TOKEN:?}" if mode == "discovery" else "")
        assert "CONTROL_TRANSPORT" not in env and "GRPC_DEV_INSECURE" not in env
