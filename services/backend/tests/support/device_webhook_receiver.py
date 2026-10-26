"""Isolated localhost HMAC-SHA256 test receiver for WayLane device events.

Only for integration tests. Bind strictly to loopback, never expose publicly.
Verifies signature over "<unix timestamp>.<raw body>", freshness, matching
event headers, and deduplicates event IDs. Configurable one-time HTTP 503
simulates recipient downtime. Never prints credentials or device identifiers.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import time
from threading import Lock

MAX_BODY_BYTES = 65536
CLOCK_SKEW_SECONDS = 300


class Receiver(ThreadingHTTPServer):
    def __init__(self, bind, secret: bytes, *, fail_first: bool):
        super().__init__(bind, Handler)
        self.secret = secret
        self.fail_first = fail_first
        self.accepted: dict[str, str] = {}
        self.reject_counts: dict[str, int] = {}
        self.duplicates = 0
        self.attempts = 0
        self.lock = Lock()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        # Suppress raw URLs/headers, including identifiers and sensitive data.
        return

    def answer(self, code: int, data: dict | None = None):
        body = json.dumps(data or {}, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def reject(self, code: int, reason: str):
        with self.server.lock:
            self.server.reject_counts[reason] = self.server.reject_counts.get(reason, 0) + 1
        self.answer(code, {"status": "rejected", "reason": reason})

    def do_GET(self):
        if self.path != "/__local_test__/stats":
            self.answer(404)
            return
        with self.server.lock:
            data = {
                "accepted_count": len(self.server.accepted),
                "accepted_types": sorted(self.server.accepted.values()),
                "duplicates": self.server.duplicates,
                "rejected": dict(self.server.reject_counts),
                "attempts": self.server.attempts,
            }
        self.answer(200, data)

    def do_POST(self):
        if self.path != "/webhook/devices":
            self.reject(404, "path")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.reject(400, "length")
            return
        if not 0 < length <= MAX_BODY_BYTES:
            self.reject(413, "length")
            return
        body = self.rfile.read(length)
        raw_stamp = self.headers.get("X-WayLane-Timestamp", "")
        signature = self.headers.get("X-WayLane-Signature", "")
        event_id = self.headers.get("X-WayLane-Event-ID", "")
        event_type = self.headers.get("X-WayLane-Event", "")
        if not raw_stamp.isascii() or not raw_stamp.isdigit() or len(raw_stamp) not in (10, 13):
            self.reject(401, "timestamp")
            return
        ts = int(raw_stamp)
        if abs(time.time() - ts) > CLOCK_SKEW_SECONDS:
            self.reject(401, "stale")
            return
        expected = "sha256=" + hmac.new(
            self.server.secret, raw_stamp.encode("ascii") + b"." + body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            self.reject(401, "signature")
            return
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.reject(400, "json")
            return
        if not isinstance(payload, dict) or not event_id or not event_type:
            self.reject(400, "headers")
            return
        if event_id != payload.get("id") or event_type != payload.get("type"):
            self.reject(400, "mismatch")
            return
        if event_type not in {"device.added", "device.updated", "device.removed", "device.reset"}:
            self.reject(400, "type")
            return
        with self.server.lock:
            self.server.attempts += 1
            if event_id in self.server.accepted:
                self.server.duplicates += 1
                self.answer(200, {"status": "already_processed"})
                return
            if self.server.fail_first:
                self.server.fail_first = False
                self.answer(503, {"status": "temporary_failure"})
                return
            self.server.accepted[event_id] = event_type
        self.answer(200, {"status": "accepted"})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args()
    key = os.getenv("WAYLANE_DEVICE_WEBHOOK_SECRET", "")
    if len(key) < 32:
        raise SystemExit("WAYLANE_DEVICE_WEBHOOK_SECRET must have 32+ characters")
    server = Receiver(
        ("127.0.0.1", args.port), key.encode(),
        fail_first=os.getenv("WAYLANE_RECEIVER_FAIL_FIRST") == "1",
    )
    print(f"RECEIVER_READY {server.server_address[1]}", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
