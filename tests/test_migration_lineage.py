"""Preserve the historical PasarGuard upgrade path for existing databases."""

import ast
import hashlib
import json
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

MIGRATIONS = Path("app/db/migrations")


def test_original_pasarguard_history_is_intact_and_waylane_extends_it():
    manifest = json.loads((MIGRATIONS / "legacy_history_sha256.json").read_text())
    assert len(manifest) == 160
    for filename, sha in manifest.items():
        content = (MIGRATIONS / "versions" / filename).read_bytes()
        assert hashlib.sha256(content).hexdigest() == sha, filename

    history = ScriptDirectory.from_config(Config("alembic.ini"))
    assert history.get_bases() == ["94a5cc12c0d6"]
    assert len(history.get_heads()) == 1
    assert history.get_heads()[0].startswith("waylane_")
    assert history.get_revision("waylane_0001").down_revision == "c8d2e4f6a1b3"
    assert history.get_revision("waylane_0002").down_revision == "waylane_0001"
    assert history.get_revision("waylane_0003").down_revision == "waylane_0002"
    assert history.get_revision("waylane_0004").down_revision == "waylane_0003"
    assert len(list(history.walk_revisions())) >= 164


def test_waylane_compatibility_bridge_must_never_recreate_existing_tables():
    source = (MIGRATIONS / "versions" / "waylane_0001_pasarguard_compatibility_bridge.py").read_text()
    tree = ast.parse(source)
    forbidden_calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            if node.func.value.id == "op" and node.func.attr not in {"get_bind", "alter_column"}:
                forbidden_calls.append(node.func.attr)
    assert not forbidden_calls, "Bridge must only adjust existing column types"
    assert "bootstrap_seed" not in source
    assert "secrets" not in source
    assert "create_table" not in source
    assert "drop_table" not in source
