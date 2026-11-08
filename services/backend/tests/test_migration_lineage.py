"""Preserve the historical PasarGuard upgrade path for existing databases."""

import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.db import models

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
    assert all(re.fullmatch(r"[0-9a-f]{12}", head) for head in history.get_heads())
    assert history.get_revision("21bec95874f4").down_revision == "c8d2e4f6a1b3"
    assert history.get_revision("bd1c0d80d86f").down_revision == "21bec95874f4"
    assert history.get_revision("985f97eefe3d").down_revision == "bd1c0d80d86f"
    assert history.get_revision("9266245ebd3d").down_revision == "985f97eefe3d"
    revisions = list(history.walk_revisions())
    assert len(revisions) >= 164
    assert all(re.fullmatch(r"[a-f0-9]{12}", rev.revision) for rev in revisions)
    assert all(re.fullmatch(r"[a-f0-9]{12}_.*\.py", Path(rev.path).name) for rev in revisions)
    assert not any(rev.revision.startswith("waylane_") for rev in revisions)
    new_revisions = [
        history.get_revision(revision) for revision in (
            "21bec95874f4", "bd1c0d80d86f", "985f97eefe3d", "9266245ebd3d"
        )
    ]
    assert all(re.fullmatch(r"[a-f0-9]{12}_.+\.py", Path(rev.path).name) for rev in new_revisions)


def test_waylane_compatibility_bridge_must_never_recreate_existing_tables():
    source = (MIGRATIONS / "versions" / "21bec95874f4_align_waylane_json_types.py").read_text()
    tree = ast.parse(source)
    forbidden_calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "op"
            and node.func.attr not in {"get_bind", "alter_column"}
        ):
            forbidden_calls.append(node.func.attr)
    assert not forbidden_calls, "Bridge must only adjust existing column types"
    assert "bootstrap_seed" not in source
    assert "secrets" not in source
    assert "create_table" not in source
    assert "drop_table" not in source


def test_revision_map_cli_keeps_product_user_unavailable():
    """Frozen revisions remain loadable without exporting User in runtime ORM."""
    assert "User" not in vars(models)
    assert not hasattr(models, "User")

    backend_dir = Path(__file__).resolve().parents[1]
    for args in (("heads",), ("history", "-r", "-3:head")):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=backend_dir,
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "d9f2e8714c33" in result.stdout

    # Neither command may install a permanent compatibility alias.
    assert "User" not in vars(models)
    assert not hasattr(models, "User")
