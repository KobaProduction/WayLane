"""Basic contract for a flat, independently deployed WayLane monorepo."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
OWNERS = ("backend", "frontend", "node")


def main():
    for owner in OWNERS:
        path = ROOT / "services" / owner
        if owner == "node":
            # The inner Node is an independently updatable runtime image.
            files = ("AGENTS.md", "node.Dockerfile", "main.go",
                     "go.mod", "README.md", "LICENSE")
        else:
            files = ("AGENTS.md", "Dockerfile", "README.md", "LICENSE", "UPSTREAM.md")
        for name in files:
            assert (path / name).is_file(), f"Missing: {owner}/{name}"
        workflow = ROOT / ".github/workflows" / f"{owner}.yml"
        data = yaml.load(workflow.read_text(), Loader=yaml.BaseLoader)
        paths = data["on"]["push"]["paths"]
        assert f"services/{owner}/**" in paths
        assert all(f"services/{other}/**" not in paths for other in OWNERS if other != owner)
        # Only backend/frontend own a service .env template. Node secrets
        # belong to its mode-scoped Git-backed Compose runtime.
        if owner != "node":
            assert (path / ".env.example").exists()
        print(f"{owner}: independently scoped CI and service files")
    agent = ROOT / "services/agent"
    for name in ("AGENTS.md", "README.md", "agent.Dockerfile", "bootstrap.Dockerfile",
                 "node-seed.Dockerfile", "bootstrap.sh", "main.go", "go.mod", "verify.sh", "LICENSE"):
        assert (agent / name).is_file(), f"Missing Agent runtime: {name}"
    assert not (ROOT / "services/node/bootstrap.sh").exists()
    assert not (ROOT / "services/node/runtime.go").exists()
    rel = yaml.load(
        (ROOT / ".github/workflows/release-image.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    assert list(rel["on"]) == ["release"]
    assert (ROOT / "shared/contracts/README.md").is_file()
    assert (ROOT / "services/external/README.md").is_file()
    assert not list((ROOT / "services/external").glob("docker-compose*.yaml"))
    print("Monorepo layout verified; external services not packaged")


if __name__ == "__main__":
    main()
