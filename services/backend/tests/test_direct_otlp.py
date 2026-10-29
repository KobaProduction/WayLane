"""Direct OTLP/HTTP acceptance against a one-shot localhost receiver."""
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from opentelemetry import trace
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from app.observability import runtime
from app.routers import browser_telemetry


def test_browser_diagnostics_are_structurally_bounded():
    from pydantic import ValidationError
    payload = browser_telemetry.BrowserEvent(
        kind="uncaught_error", route_group="dashboard",
    )
    assert payload.model_dump() == {"kind": "uncaught_error", "route_group": "dashboard"}
    with pytest.raises(ValidationError):
        browser_telemetry.BrowserEvent(
            kind="uncaught_error", route_group="dashboard", stack="private-secret",
        )
    with pytest.raises(ValidationError):
        browser_telemetry.BrowserEvent(
            kind="uncaught_error", route_group="https://example.test/?token=ABC",
        )


@pytest.mark.asyncio
async def test_browser_diagnostics_per_admin_rate_limit():
    browser_telemetry._events.clear()
    body = browser_telemetry.BrowserEvent(kind="unhandled_rejection", route_group="other")
    for _ in range(10):
        assert (await browser_telemetry.capture_browser_error(
            body=body, admin=SimpleNamespace(id=12),
        ))["accepted"]
    with pytest.raises(HTTPException) as error:
        await browser_telemetry.capture_browser_error(body=body, admin=SimpleNamespace(id=12))
    assert error.value.status_code == 429
    assert (await browser_telemetry.capture_browser_error(
        body=body, admin=SimpleNamespace(id=13),
    ))["accepted"]
    browser_telemetry._events.clear()


def test_otlp_endpoint_is_optional_and_bearer_is_independent(monkeypatch):
    monkeypatch.delenv("OTLP_ENDPOINT", raising=False)
    monkeypatch.setenv("OTLP_BEARER_TOKEN", "unused-without-endpoint")
    assert runtime.configure_direct_otlp() is None

    runtime._validate_exporter_destination("https://example.invalid", "")
    runtime._validate_exporter_destination("https://example.invalid", "optional-token")
    runtime._validate_exporter_destination("http://otel-collector:4318", "")
    runtime._validate_exporter_destination("http://otel-collector:4318", "optional-token")
    runtime._validate_exporter_destination("http://10.0.0.25:4318", "")
    with pytest.raises(RuntimeError):
        runtime._validate_exporter_destination("http://example.invalid:4318", "")
    with pytest.raises(RuntimeError):
        runtime._validate_exporter_destination("http://192.0.2.10:4318", "token")
    with pytest.raises(RuntimeError):
        runtime._validate_exporter_destination("https://user:pass@example.invalid", "")
    with pytest.raises(RuntimeError):
        runtime._validate_exporter_destination("http://otel-collector:4318/?query=1", "")
    assert runtime._telemetry is None


def test_otlp_uses_no_auth_header_when_endpoint_is_set_but_token_is_empty(monkeypatch):
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    class ExporterCaptured(Exception):
        pass

    seen = {}

    def capture(self, *args, **kwargs):
        seen.update(kwargs)
        raise ExporterCaptured

    monkeypatch.setenv("OTLP_ENDPOINT", "https://telemetry.example.invalid")
    monkeypatch.setenv("OTLP_BEARER_TOKEN", "")
    monkeypatch.setattr(OTLPSpanExporter, "__init__", capture)
    with pytest.raises(ExporterCaptured):
        runtime.configure_direct_otlp()
    assert seen["headers"] == {}
    assert seen["endpoint"] == "https://telemetry.example.invalid/v1/traces"


def test_direct_otlp_logs_traces_are_sanitized(monkeypatch):
    captured = []

    class Collector(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_POST(self):
            n = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(n)
            captured.append((
                self.path,
                self.headers.get("Authorization"),
                self.headers.get("Content-Type"),
                body,
            ))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("OTLP_ENDPOINT", f"http://127.0.0.1:{server.server_port}")
        monkeypatch.setenv("OTLP_BEARER_TOKEN", "synthetic-local-test-bearer")
        monkeypatch.setenv("WAYLANE_OTLP_LOCAL_TEST", "1")
        monkeypatch.setenv("OTEL_SERVICE_NAMESPACE", "waylane")
        monkeypatch.setenv("OTEL_DEPLOYMENT_ENVIRONMENT_NAME", "development")
        providers = runtime.configure_direct_otlp()
        assert providers
        logging.getLogger("waylane.telemetry").warning(
            "browser.error", extra={
                "event_kind": "uncaught_error",
                "route_group": "dashboard",
            },
        )
        logging.getLogger("security.issue").error(
            "Sensitive URI /sub/synthetic-super-secret?token=synthetic-secret",
        )
        with trace.get_tracer("waylane.test").start_as_current_span("safe.browser.error"):
            logging.getLogger("waylane.telemetry").warning("http.error")
        traces, logs, _ = providers
        assert traces.force_flush(timeout_millis=3000)
        assert logs.force_flush(timeout_millis=3000)
        runtime.flush_and_shutdown_direct_otlp()
        assert {path for path, *_ in captured} >= {"/v1/traces", "/v1/logs"}
        assert all(auth == "Bearer synthetic-local-test-bearer" for _, auth, _, _ in captured)
        assert all("protobuf" in ctype for _, _, ctype, _ in captured)
        log_requests = [
            ExportLogsServiceRequest.FromString(body) for path, _, _, body in captured
            if path == "/v1/logs"
        ]
        assert any(
            rec.body.string_value == "browser.error"
            for packet in log_requests for group in packet.resource_logs
            for scope in group.scope_logs for rec in scope.log_records
        )
        assert all(b"synthetic-secret" not in body for _, _, _, body in captured)
        trace_requests = [
            ExportTraceServiceRequest.FromString(body) for path, _, _, body in captured
            if path == "/v1/traces"
        ]
        assert any(
            span.name == "safe.browser.error"
            for packet in trace_requests for group in packet.resource_spans
            for scope in group.scope_spans for span in scope.spans
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)



@pytest.mark.asyncio
async def test_unauthenticated_browser_events_are_rejected_and_warn_without_secrets(monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from app.routers.authentication import get_current

    app = FastAPI()
    app.include_router(browser_telemetry.router)
    recorded = []
    monkeypatch.setattr(browser_telemetry.log, "warning",
                        lambda event, *args, **kwargs: recorded.append(event))

    async def deny():
        raise HTTPException(status_code=401, detail="invalid synthetic token")

    app.dependency_overrides[get_current] = deny
    browser_telemetry._last_denied_warning = None
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        for _ in range(2):
            response = await client.post(
                "/api/telemetry/browser",
                json={"kind": "uncaught_error", "route_group": "dashboard"},
                headers={"Authorization": "Bearer synthetic-credential"},
            )
            assert response.status_code == 401
    assert recorded == ["browser.unauthenticated"]
    assert all("synthetic" not in text for text in recorded)


@pytest.mark.asyncio
async def test_authenticated_browser_event_is_accepted_and_logged(monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from app.routers.authentication import get_current

    app = FastAPI()
    app.include_router(browser_telemetry.router)
    recorded = []
    monkeypatch.setattr(browser_telemetry.log, "warning",
                        lambda event, *args, **kwargs: recorded.append(event))

    async def allow():
        return SimpleNamespace(id=203)

    app.dependency_overrides[get_current] = allow
    browser_telemetry._events.clear()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/telemetry/browser",
            json={"kind": "uncaught_error", "route_group": "settings"},
        )
    assert response.status_code == 202
    assert response.json() == {"accepted": True}
    assert recorded == ["browser.error"]
    browser_telemetry._events.clear()


def test_first_unknown_caller_warning_does_not_wait_for_60s_uptime():
    assert browser_telemetry._warning_due(None, 3.0)
    assert not browser_telemetry._warning_due(3.0, 15.0)
    assert browser_telemetry._warning_due(3.0, 63.0)
