"""Regression: credential-bearing subscription URLs never enter access logs."""
from app.middlewares.request_logging import RequestProcessTimeLoggingMiddleware, safe_access_path
from config import subscription_env_settings


def test_subscription_token_and_suffix_are_redacted():
    prefix = "/" + subscription_env_settings.path.strip("/")
    token = "synthetic-private-subscription-token"
    assert safe_access_path(f"{prefix}/{token}") == prefix + "/[REDACTED]"
    assert safe_access_path(f"{prefix}/{token}/info") == prefix + "/[REDACTED]"
    assert safe_access_path(f"{prefix}/{token}/raw") == prefix + "/[REDACTED]"


def test_non_subscription_routes_are_preserved():
    assert safe_access_path("/health") == "/health"
    assert safe_access_path("/api/telemetry/browser") == "/api/telemetry/browser"


def test_request_logger_never_records_subscription_token_or_query_string():
    import asyncio
    from unittest.mock import Mock

    prefix = "/" + subscription_env_settings.path.strip("/")
    token = "synthetic-private-subscription-token"
    query = "access_token=synthetic-private-query-token"

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def send(message):
        pass

    logger = Mock()
    middleware = RequestProcessTimeLoggingMiddleware(app, access_logger=logger)
    scope = {
        "type": "http",
        "path": f"{prefix}/{token}/info",
        "query_string": query.encode(),
        "client": ("127.0.0.1", 1234),
        "method": "GET",
        "http_version": "1.1",
    }
    asyncio.run(middleware(scope, None, send))
    messages = [str(call) for call in logger.info.call_args_list]
    assert any(prefix + "/[REDACTED]" in message for message in messages)
    assert token not in str(messages)
    assert "synthetic-private-query-token" not in str(messages)
