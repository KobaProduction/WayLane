"""Small authenticated, rate-limited frontend diagnostic relay.

No raw URLs, stack traces, free-form text, tokens, cookies or IP metadata.
The browser sends only fixed event-kind and coarse page group values.
"""
import logging
import threading
import time
from collections import OrderedDict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.routing import APIRoute
from starlette.requests import Request
from pydantic import BaseModel, ConfigDict

from app.models.admin import AdminDetails
from .authentication import get_current

# Anonymous attempts have no authenticated identity. Emit only one fixed
# warning per minute to prevent public 401 spam exhausting the log exporter.
_last_denied_warning: float | None = None
_denied_lock = threading.Lock()


def _warning_due(last: float | None, now: float) -> bool:
    # A newly started process must emit its very first warning even when
    # system monotonic uptime is under 60 seconds (ephemeral CI workers).
    return last is None or now - last >= 60


class AuthenticatedTelemetryRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def wrapped(request: Request):
            global _last_denied_warning
            try:
                return await handler(request)
            except HTTPException as exc:
                if exc.status_code in (401, 403):
                    now = time.monotonic()
                    with _denied_lock:
                        if _warning_due(_last_denied_warning, now):
                            _last_denied_warning = now
                            log.warning("browser.unauthenticated")
                raise

        return wrapped


router = APIRouter(
    prefix="/api/telemetry", tags=["Runtime telemetry"],
    route_class=AuthenticatedTelemetryRoute,
)
_events: OrderedDict[int, tuple[float, int]] = OrderedDict()
_lock = threading.Lock()
log = logging.getLogger("waylane.telemetry")


class BrowserEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["uncaught_error", "unhandled_rejection", "chunk_load_error"]
    route_group: Literal["login", "dashboard", "settings", "other"]


@router.post("/browser", status_code=202)
async def capture_browser_error(
    body: BrowserEvent,
    admin: AdminDetails = Depends(get_current),
):
    now = time.monotonic()
    with _lock:
        cutoff = now - 60
        for key, (first, _) in list(_events.items()):
            if first < cutoff:
                _events.pop(key, None)
        if len(_events) > 1024:
            _events.popitem(last=False)
        first, count = _events.get(admin.id, (now, 0))
        if now - first >= 60:
            first, count = now, 0
        if count >= 10:
            raise HTTPException(429, "Telemetry rate limit reached")
        _events[admin.id] = (first, count + 1)
    log.warning(
        "browser.error",
        extra={"event_kind": body.kind, "route_group": body.route_group},
    )
    return {"accepted": True}
