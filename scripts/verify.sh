#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v uv >/dev/null || { echo "uv is required" >&2; exit 2; }
uv lock --check
uv run --no-project --python 3.14 python -m compileall -q app cli dashboard/__init__.py main.py waylane-cli.py
test -f LICENSE
test -f app/templates/subscription/index.html
test -f dashboard/__init__.py
# Retain the exact historical PasarGuard revision lineage for in-place upgrades.
# The first WayLane revision is a forward schema bridge, not a second base.
python - <<'PYTHON'
from pathlib import Path
import ast
import hashlib
import json

root = Path("app/db/migrations")
versions = sorted((root / "versions").glob("*.py"))
manifest = json.loads((root / "legacy_history_sha256.json").read_text())
legacy = {p.name: p for p in versions if not p.name.startswith("waylane_")}
assert len(manifest) == len(legacy) == 160, "Historical migrations are missing or untracked"
for name, sha256 in manifest.items():
    assert name in legacy, f"Missing historical migration {name}"
    assert hashlib.sha256(legacy[name].read_bytes()).hexdigest() == sha256, f"Modified historical migration {name}"

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
    assert entries["revision"] not in revisions, f"Duplicate revision {entries['revision']}"
    revisions[entries["revision"]] = entries["down_revision"]
assert len(revisions) >= 164, "WayLane core migrations are missing"
assert revisions["94a5cc12c0d6"] is None, "Original PasarGuard root was changed"
assert revisions["waylane_0001"] == "c8d2e4f6a1b3", "WayLane must continue the original tip"
for step in range(2, 5):
    assert revisions[f"waylane_{step:04d}"] == f"waylane_{step-1:04d}"
def parents(value):
    if value is None:
        return ()
    if isinstance(value, (tuple, list)):
        return value
    return (value,)

heads = set(revisions) - {parent for links in revisions.values() for parent in parents(links)}
assert len(heads) == 1, f"Unexpected Alembic heads: {heads}"
current_head = next(iter(heads))
assert current_head.startswith("waylane_"), f"No WayLane head: {current_head}"
visiting = set()
seen = set()

def visit(rev):
    assert rev in revisions and rev not in visiting, f"Missing parent or cycle: {rev}"
    if rev in seen:
        return
    visiting.add(rev)
    for parent in parents(revisions[rev]):
        visit(parent)
    visiting.remove(rev)
    seen.add(rev)

visit(current_head)
assert seen == set(revisions), "Disconnected Alembic history"

# Frozen reference only: old migrations seed real databases. Never reseed live DBs.
defaults = json.loads((root / "bootstrap_seed.json").read_text())
assert len(defaults["admin_roles"]) == 3
assert {r["id"] for r in defaults["admin_roles"]} == {1, 2, 3}
assert len(defaults["client_templates"]) == 5
assert isinstance(defaults["settings"][0]["notification_settings"], dict)
assert "jwt" not in defaults and "admins" not in defaults
PYTHON
echo "WayLane backend bootstrap verification passed (syntax/lock/assets and PasarGuard lineage)."
