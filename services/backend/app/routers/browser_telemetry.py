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
from pydantic import BaseModel, ConfigDict

from app.models.admin import AdminDetails
from .authentication import get_current

router = APIRouter(prefix="/api/telemetry", tags=["Runtime telemetry"])
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
