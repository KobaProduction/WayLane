"""Plaintext backend listener must work in private reverse-proxy topology."""

from main import resolve_unencrypted_bind_host


def test_explicit_compose_wildcard_is_accepted(monkeypatch):
    monkeypatch.setenv("UVICORN_HOST", "0.0.0.0")
    assert resolve_unencrypted_bind_host("0.0.0.0") == "0.0.0.0"


def test_unset_or_different_host_is_not_public(monkeypatch):
    monkeypatch.delenv("UVICORN_HOST", raising=False)
    assert resolve_unencrypted_bind_host("0.0.0.0") == "localhost"
    monkeypatch.setenv("UVICORN_HOST", "127.0.0.1")
    assert resolve_unencrypted_bind_host("0.0.0.0") == "localhost"


def test_explicit_private_host_remains_supported(monkeypatch):
    monkeypatch.setenv("UVICORN_HOST", "10.0.0.5")
    assert resolve_unencrypted_bind_host("10.0.0.5") == "10.0.0.5"
