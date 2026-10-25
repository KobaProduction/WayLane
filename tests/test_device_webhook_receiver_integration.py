"""Live localhost Python HTTP server exercising signed real HWID outbox events.

Starts a separate Python receiver process. It rejects forged, stale and
mismatched requests, deduplicates event IDs, and verifies delivery after
HTTP 503 without requiring a real Telegram bot or external services.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.db.crud.hwid import delete_user_hwid, register_user_hwid, reset_user_hwids
from app.db.models import User
from app.db.subscription_firewall import subscription_device_events, subscription_firewall_config
from app.subscription import device_webhook
from app.subscription.firewall import SCENARIOS


def sign(body: bytes, secret: str, timestamp: str, event_id: str, event_type: str) -> dict:
    return {
        "X-WayLane-Event-ID": event_id,
        "X-WayLane-Event": event_type,
        "X-WayLane-Timestamp": timestamp,
        "X-WayLane-Signature": "sha256=" + hmac.new(
            secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
        ).hexdigest(),
        "Content-Type": "application/json",
    }


@pytest.mark.asyncio
async def test_real_receiver_validates_signed_device_lifecycle_and_retries(tmp_path, monkeypatch):
    # All addresses loopback and all device records synthetic. No real panel or bot.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    key = secrets.token_urlsafe(40)
    monkeypatch.setenv("WAYLANE_DEVICE_WEBHOOK_SECRET", key)
    monkeypatch.setenv("WAYLANE_WEBHOOK_ALLOW_LOCAL_TEST", "1")
    receiver_script = Path(__file__).parent / "support" / "device_webhook_receiver.py"
    env = {**os.environ, "WAYLANE_RECEIVER_FAIL_FIRST": "1"}
    process = subprocess.Popen(
        [sys.executable, str(receiver_script), "--port", str(port)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/device-lifecycle.db")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    sent = []

    @asynccontextmanager
    async def test_db():
        async with sessions() as session:
            yield session

    monkeypatch.setattr(device_webhook, "GetDB", test_db)
    monkeypatch.setattr(device_webhook, "dispatch_after_commit", sent.append)

    try:
        async with httpx.AsyncClient(timeout=4, trust_env=False) as http:
            # Startup can be slower on the shared 2 GiB CI runner; bound the
            # local-only readiness check to 5 seconds instead of a fixed sleep.
            for _ in range(25):
                assert process.poll() is None, "Receiver exited without starting"
                try:
                    probe = await http.get(base_url + "/__local_test__/stats")
                except httpx.ConnectError:
                    await asyncio.sleep(0.2)
                    continue
                assert probe.status_code == 200
                break
            else:
                pytest.fail("Test receiver did not become ready within 5 seconds")

            # Deny requests with absent/incorrect signatures and old timestamps.
            forged_id = str(uuid4())
            forged = json.dumps({
                "id": forged_id, "type": "device.added", "user_id": 10,
                "hwid": "forged", "details": {},
            }).encode()
            assert (await http.post(base_url + "/webhook/devices", content=forged)).status_code == 401
            now = str(int(time.time()))
            wrong = sign(forged, "wrong-signing-secret-with-at-least-32-characters", now, forged_id, "device.added")
            assert (await http.post(base_url + "/webhook/devices", content=forged, headers=wrong)).status_code == 401
            stale = str(int(time.time()) - 700)
            assert (await http.post(
                base_url + "/webhook/devices", content=forged,
                headers=sign(forged, key, stale, forged_id, "device.added"),
            )).status_code == 401
            # Sign one body, transmit different bytes.
            altered = forged.replace(b"forged", b"fakeee")
            assert (await http.post(
                base_url + "/webhook/devices", content=altered,
                headers=sign(forged, key, now, forged_id, "device.added"),
            )).status_code == 401
            mismatched = sign(forged, key, now, str(uuid4()), "device.added")
            assert (await http.post(
                base_url + "/webhook/devices", content=forged, headers=mismatched,
            )).status_code == 400

            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with sessions() as db:
                await db.execute(insert(subscription_firewall_config).values(
                    id=1, enabled=False, scenarios=SCENARIOS,
                    webhook_enabled=True, webhook_url=base_url + "/webhook/devices"
                ))
                db.add(User(username="local-webhook-test"))
                await db.commit()
                user_id = (await db.scalar(select(User.id).where(
                    User.username == "local-webhook-test"
                )))
                assert await register_user_hwid(db, user_id, "local-device-A", "Android", "14", "Phone")
                assert len(sent) == 1

                # One simulated HTTP 503, durable retry and identical event ID.
                first = sent[0]
                assert await device_webhook.deliver_device_event(first) is False
                row = (await db.execute(select(subscription_device_events).where(
                    subscription_device_events.c.id == first
                ))).mappings().one()
                assert row["status"] == "failed" and row["attempts"] == 1
                # Simulate passage of the recorded delay. The scheduler pass
                # must pick up the failed delivery without a manual retry.
                assert (await device_webhook.retry_due_device_events())["selected"] == 0
                await db.execute(
                    update(subscription_device_events)
                    .where(subscription_device_events.c.id == first)
                    .values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))
                )
                await db.commit()
                result = await device_webhook.retry_due_device_events()
                assert result == {"selected": 1, "delivered": 1, "failed": 0}
                assert await device_webhook.deliver_device_event(first) is False

                # Resubscribing with unchanged HWID must not generate new events.
                assert await register_user_hwid(db, user_id, "local-device-A", "Android", "14", "Phone")
                assert len(sent) == 1
                assert await register_user_hwid(db, user_id, "local-device-A", "Android", "15", "Phone")
                assert len(sent) == 2
                assert await delete_user_hwid(db, user_id, "local-device-A")
                assert len(sent) == 3
                assert await register_user_hwid(db, user_id, "local-device-B", "Linux", "6", "Laptop")
                assert len(sent) == 4
                assert await reset_user_hwids(db, user_id) == 1
                assert len(sent) == 5

                for event_id in sent[1:]:
                    assert await device_webhook.deliver_device_event(event_id)
                rows = (await db.execute(
                    select(subscription_device_events.c.id, subscription_device_events.c.event_type,
                           subscription_device_events.c.status, subscription_device_events.c.attempts)
                )).all()
                assert len(rows) == 5
                assert sorted(row[1] for row in rows) == [
                    "device.added", "device.added", "device.removed",
                    "device.reset", "device.updated",
                ]
                assert all(row[2] == "delivered" for row in rows)
                assert [row[3] for row in rows if row[0] == first] == [2]

                # Signed repetition is idempotent at receiver; no second device change.
                accepted_payload = (await db.execute(select(
                    subscription_device_events.c.payload
                ).where(subscription_device_events.c.id == first))).scalar_one()
                raw = json.dumps(accepted_payload, sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=False).encode()
                repeat = sign(raw, key, str(int(time.time())), first, "device.added")
                assert (await http.post(
                    base_url + "/webhook/devices", content=raw, headers=repeat,
                )).status_code == 200

            stats = (await http.get(base_url + "/__local_test__/stats")).json()
            assert stats["accepted_count"] == 5, stats
            assert stats["duplicates"] == 1, stats
            assert stats["rejected"] == {
                "timestamp": 1, "signature": 2, "stale": 1, "mismatch": 1,
            }, stats
            assert stats["accepted_types"] == [
                "device.added", "device.added", "device.removed",
                "device.reset", "device.updated",
            ]
            print("LIVE_WEBHOOK_RECEIVER_VERIFIED", stats)
    finally:
        process.terminate()
        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=4)
        await engine.dispose()
