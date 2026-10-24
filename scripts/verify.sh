#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v uv >/dev/null || { echo "uv is required" >&2; exit 2; }
uv lock --check
uv run --no-project --python 3.14 python -m compileall -q app cli dashboard/__init__.py main.py waylane-cli.py
test -f LICENSE
test -f app/templates/subscription/index.html
test -f dashboard/__init__.py
# Fail closed when legacy migrations sneak back into a fresh WayLane checkout.
python - <<'PYTHON'
from pathlib import Path
import ast
import json

directory = Path("app/db/migrations/versions")
versions = sorted(directory.glob("*.py"))
assert versions, "WayLane requires a versioned baseline migration"
revisions = {}
for file in versions:
    tree = ast.parse(file.read_text())
    entries = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"revision", "down_revision"}
    }
    assert entries["revision"].startswith("waylane_"), "Legacy migration found"
    revisions[entries["revision"]] = entries["down_revision"]
assert revisions.get("waylane_0001", "missing") is None
heads = set(revisions) - {parent for parent in revisions.values() if parent}
assert len(heads) == 1, f"Multiple Alembic heads: {heads}"
current = next(iter(heads))
seen = set()
while current is not None:
    assert current in revisions and current not in seen, "Missing parent or cycle"
    seen.add(current)
    current = revisions[current]
assert seen == set(revisions), "Found a disconnected Alembic branch"
defaults = json.loads(Path("app/db/migrations/bootstrap_seed.json").read_text())
assert len(defaults["admin_roles"]) == 3
assert {role["id"] for role in defaults["admin_roles"]} == {1, 2, 3}
assert len(defaults["client_templates"]) == 5
assert isinstance(defaults["settings"][0]["notification_settings"], dict)
assert "jwt" not in defaults and "admins" not in defaults
PYTHON
echo "WayLane backend bootstrap verification passed (syntax/lock/assets)."
