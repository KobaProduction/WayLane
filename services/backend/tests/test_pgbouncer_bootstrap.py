"""Verify pooler bootstrap without exposing secrets or requiring Docker."""
import importlib.util
import os
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[3] / "deploy/compose/pgbouncer/start.py"


def run_bootstrap(tmp_path, monkeypatch, username, password, database):
    spec = importlib.util.spec_from_file_location("waylane_pgbouncer_bootstrap", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    original_path = module.Path

    # Redirect only the absolute container scratch directory into the fixture.
    module.Path = lambda value: tmp_path if value == "/tmp/waylane-pgbouncer" else original_path(value)
    monkeypatch.setenv("POSTGRES_USER", username)
    monkeypatch.setenv("POSTGRES_PASSWORD", password)
    monkeypatch.setenv("POSTGRES_DB", database)

    class ExecCalled(Exception):
        pass

    def capture(name, argv):
        assert name == "pgbouncer"
        assert argv[0] == "pgbouncer"
        raise ExecCalled

    monkeypatch.setattr(module.os, "execvp", capture)
    with pytest.raises(ExecCalled):
        module.main()

    auth = tmp_path / "userlist.txt"
    cfg = tmp_path / "pgbouncer.ini"
    assert auth.stat().st_mode & 0o777 == 0o600
    assert cfg.stat().st_mode & 0o777 == 0o600
    assert password not in cfg.read_text()
    return auth.read_text(), cfg.read_text()


def test_pooler_auth_file_escapes_credentials_and_restricts_file_mode(tmp_path, monkeypatch):
    user = 'my"admin'
    secret = 'fake"pass:@/£'
    auth, config = run_bootstrap(tmp_path, monkeypatch, user, secret, "test")
    assert auth == '"my""admin" "fake""pass:@/£"\n'
    assert "pool_mode = session" in config
    assert "auth_type = scram-sha-256" in config


def test_pooler_does_not_accept_missing_secret(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("waylane_pgbouncer_invalid", SOURCE)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    monkeypatch.setenv("POSTGRES_USER", "test")
    monkeypatch.setenv("POSTGRES_PASSWORD", "")
    monkeypatch.setenv("POSTGRES_DB", "test")
    with pytest.raises(SystemExit):
        module.main()
