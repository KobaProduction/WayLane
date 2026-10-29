"""Direct OTLP/HTTP telemetry with sanitized logging and no browser credentials.

The existing stdlib logger remains the runtime logging facade. An OTLP copy
of each selected log uses only event metadata; arbitrary exception messages,
URLs, payloads and stack traces are never forwarded to SigNoz.
"""

import ipaddress
import logging
import os
import re
import threading
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_telemetry = None


def _resource_value(name: str, default: str) -> str:
    value = os.getenv(name) or default
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", value):
        raise ValueError(f"Invalid telemetry resource label: {name}")
    return value


class _MetadataOnlyOTLPHandler(logging.Handler):
    """Sanitize arbitrary application logs before exporting their metadata."""

    def __init__(self, downstream):
        super().__init__(logging.DEBUG)
        self._downstream = downstream

    def emit(self, record: logging.LogRecord):
        if record.name.startswith(("opentelemetry", "urllib3", "httpcore", "httpx")):
            return
        # Preserve complete text only for our own fixed, reviewed telemetry
        # event vocabulary. Application errors can embed bearer credentials,
        # subscription tokens and private data in formatted messages.
        safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "", record.name)[:80] or "runtime"
        content = (
            record.msg
            if record.name == "waylane.telemetry"
            and isinstance(record.msg, str)
            and re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", record.msg)
            else "runtime.log"
        )
        safe = logging.LogRecord(
            name=safe_name,
            level=record.levelno,
            pathname="",
            lineno=0,
            msg=content,
            args=(),
            exc_info=None,
        )
        if record.name == "waylane.telemetry":
            for key in ("event_kind", "route_group", "response_status"):
                value = getattr(record, key, None)
                if isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", value):
                    setattr(safe, key, value)
                elif key == "response_status" and isinstance(value, int):
                    setattr(safe, key, value)
        self._downstream.emit(safe)


def _validate_exporter_destination(endpoint: str, token: str) -> None:
    """Accept configured HTTPS or internal OTLP/HTTP, with optional Bearer."""
    parsed = urlparse(endpoint)
    host = parsed.hostname
    if not host or parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise RuntimeError("OTLP endpoint must be a credential-free HTTP(S) URL")
    if parsed.query or parsed.fragment or "/v1/" in parsed.path:
        raise RuntimeError("OTLP endpoint must be a base URL without query or signal path")

    if parsed.scheme == "http":
        # HTTP is intended only for a private Docker/cluster collector.
        # Never disclose an optional Bearer token to an arbitrary public host.
        private_host = "." not in host or host.endswith((".internal", ".local"))
        if not private_host:
            try:
                ip = ipaddress.ip_address(host)
                private_host = any(ip in ipaddress.ip_network(cidr) for cidr in (
                    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                ))
            except ValueError:
                private_host = False
        if os.getenv("WAYLANE_OTLP_LOCAL_TEST") == "1" and host in {"127.0.0.1", "localhost"}:
            private_host = True
        if not private_host:
            raise RuntimeError("Plain HTTP OTLP is permitted only on private networks; use HTTPS")


def configure_direct_otlp():
    """Install SDK exporters once in this process.

    An empty endpoint disables export. A configured HTTPS or internal HTTP
    endpoint works with or without Bearer; a token only adds Authorization.
    The browser never receives the token.
    """
    global _telemetry
    with _lock:
        if _telemetry is not None:
            return _telemetry
        endpoint = os.getenv("OTLP_ENDPOINT", "").strip()
        token = os.getenv("OTLP_BEARER_TOKEN", "").strip()
        if not endpoint:
            return None
        _validate_exporter_destination(endpoint, token)

        # Lazy imports keep the normal local development runtime simple.
        from opentelemetry import trace
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        res = Resource.create({
            "service.name": "waylane-backend",
            "service.namespace": _resource_value("OTEL_SERVICE_NAMESPACE", "waylane"),
            "deployment.environment.name": _resource_value(
                "OTEL_DEPLOYMENT_ENVIRONMENT_NAME", "development"
            ),
        })
        base = endpoint.rstrip("/")
        headers = {"Authorization": "Bearer " + token} if token else {}
        traces = TracerProvider(resource=res)
        traces.add_span_processor(BatchSpanProcessor(
            OTLPSpanExporter(endpoint=base + "/v1/traces", headers=headers, timeout=4),
            max_queue_size=512, max_export_batch_size=64,
        ))
        logs = LoggerProvider(resource=res)
        logs.add_log_record_processor(BatchLogRecordProcessor(
            OTLPLogExporter(endpoint=base + "/v1/logs", headers=headers, timeout=4),
            max_queue_size=512, max_export_batch_size=64,
        ))
        trace.set_tracer_provider(traces)
        sanitized = _MetadataOnlyOTLPHandler(LoggingHandler(logger_provider=logs))
        # FastAPI/Uvicorn process loggers are not necessarily root-propagating.
        # Attach at one appropriate ancestor per tree.
        logging.getLogger().addHandler(sanitized)
        logging.getLogger("uvicorn").addHandler(sanitized)
        _telemetry = (traces, logs, sanitized)
        logging.getLogger("waylane.telemetry").info("otel.started")
        return _telemetry


def flush_and_shutdown_direct_otlp():
    global _telemetry
    with _lock:
        state = _telemetry
        if not state:
            return
        traces, logs, handler = state
        logging.getLogger().removeHandler(handler)
        logging.getLogger("uvicorn").removeHandler(handler)
        traces.shutdown()
        logs.shutdown()
        _telemetry = None
