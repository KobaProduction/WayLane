from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import middlewares
from app.subscription.identity import _TOKEN_PATTERN


def test_old_numeric_and_username_subscription_tokens_have_no_parser():
    """Only UUID + access code is supported; legacy user tokens cannot authenticate."""
    for token in ("v3,789,1731000000", "user_789", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.signature"):
        assert _TOKEN_PATTERN.fullmatch(token) is None


@pytest.mark.parametrize(
    ("origins", "credentials"),
    [("*", None), ("https://panel.example.com", "true")],
)
def test_cors_credentials_only_for_explicit_origins(monkeypatch, origins, credentials):
    monkeypatch.setattr(middlewares, "cors_settings", SimpleNamespace(allowed_origins=[origins]))
    app = FastAPI()
    middlewares.setup_middleware(app)
    app.get("/ping")(lambda: {"ok": True})

    response = TestClient(app).get("/ping", headers={"Origin": "https://panel.example.com", "Cookie": "session=1"})

    assert response.headers.get("access-control-allow-credentials") == credentials
    expected_origin = "*" if origins == "*" else "https://panel.example.com"
    assert response.headers.get("access-control-allow-origin") == expected_origin
