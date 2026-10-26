"""Basic contract for a flat, independently deployed WayLane monorepo."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
OWNERS = ("backend", "frontend", "node")


def main():
    for owner in OWNERS:
        path = ROOT / "services" / owner
        for name in ("AGENTS.md", "Dockerfile", "README.md", "LICENSE", "UPSTREAM.md"):
            assert (path / name).is_file(), f"Missing: {owner}/{name}"
        workflow = ROOT / ".github/workflows" / f"{owner}.yml"
        data = yaml.load(workflow.read_text(), Loader=yaml.BaseLoader)
        paths = data["on"]["push"]["paths"]
        assert f"services/{owner}/**" in paths
        assert all(f"services/{other}/**" not in paths for other in OWNERS if other != owner)
        assert (path / ".env.example").exists()
        print(f"{owner}: independently scoped CI and service files")
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
