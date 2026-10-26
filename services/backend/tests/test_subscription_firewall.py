"""Legacy subscription message compatibility and signed device event delivery."""

import asyncio
import base64
import hashlib
import hmac
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from urllib.parse import unquote

import pytest
from fastapi import HTTPException
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.db.crud.hwid import delete_user_hwid, register_user_hwid, reset_user_hwids
from app.db.models import User
from app.db.subscription_firewall import subscription_device_events, subscription_firewall_config
from app.routers.subscription_firewall import FirewallEditor, MessageTemplate
from app.subscription import device_webhook
from app.subscription.firewall import SCENARIOS, render_message, response_for_failure


def test_legacy_message_format_is_base64_and_announce():
    response = render_message("Превышен лимит устройств", ["❗ЛИМИТ УСТРОЙСТВ❗"])
    assert response.status_code == 200
    assert response.media_type == "text/plain"
    body = base64.b64decode(response.body).decode()
    assert body.startswith("ss://")
    assert "@-:1#" in body
    assert unquote(body.split("#", 1)[1]) == "❗ЛИМИТ УСТРОЙСТВ❗"
    title = response.headers["announce"].split(":", 1)[1]
    assert "Превышен лимит устройств" == base64.b64decode(title + "==").decode()


def test_template_validation_and_url_protection(monkeypatch):
    monkeypatch.delenv("WAYLANE_WEBHOOK_ALLOW_LOCAL_TEST", raising=False)
    with pytest.raises(ValueError):
        FirewallEditor(enabled=True, scenarios=SCENARIOS, webhook_url="http://127.0.0.1:8090")
    with pytest.raises(ValueError):
        FirewallEditor(enabled=True, scenarios=SCENARIOS, webhook_url="https://127.0.0.1:8090")
    with pytest.raises(ValueError):
        FirewallEditor(enabled=True, scenarios=SCENARIOS, webhook_url="https://user:secret@example.com/path")
    with pytest.raises(ValueError):
        MessageTemplate(title="ok", lines=[""])
    assert FirewallEditor(enabled=True, scenarios=SCENARIOS, webhook_url="https://example.com/hook")
    monkeypatch.setenv("WAYLANE_WEBHOOK_ALLOWED_INTERNAL_HOSTS", "172.18.0.1")
    assert FirewallEditor(enabled=True, scenarios=SCENARIOS, device_check_url="http://172.18.0.1:8090/webhook/check_device")


@pytest.mark.asyncio
async def test_fallback_only_when_enabled_and_hwid_present(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "notices.db"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with factory() as db:
            error = HTTPException(403, "Device limit reached")
            assert await response_for_failure(db, error, client_accept="text/plain", hwid="one", user_agent="Happ/1.0") is None
            await db.execute(insert(subscription_firewall_config).values(
                id=1, enabled=True, scenarios={"device_limit": {
                    "title": "Device cap", "lines": ["Too many devices"]
                }}, webhook_enabled=False))
            await db.commit()
            assert await response_for_failure(db, error, client_accept="text/html", hwid="one", user_agent="Happ/1.0") is None
            assert await response_for_failure(db, error, client_accept="text/plain", hwid=None, user_agent="Happ/1.0") is None
            assert await response_for_failure(
                db, error, client_accept="application/json", hwid="one", user_agent="Clash/1.0"
            ) is None
            response = await response_for_failure(db, error, client_accept="text/plain", hwid="one", user_agent="Happ/1.0")
            assert response.status_code == 200
            assert "Too%20many%20devices" in base64.b64decode(response.body).decode()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_device_mutations_emit_exactly_once_and_retain_retriable_events(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "devices.db"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    triggered = []
    monkeypatch.setattr(device_webhook, "dispatch_after_commit", triggered.append)
    try:
        async with factory() as db:
            await db.execute(insert(subscription_firewall_config).values(
                id=1, enabled=False, scenarios=SCENARIOS, webhook_enabled=True,
                webhook_url="https://example.invalid/webhook",
            ))
            db.add(User(username="device-user"))
            await db.commit()
            user_id = (await db.scalars(select(User.id).where(User.username == "device-user"))).one()
            assert await register_user_hwid(db, user_id, "device-001", "Linux", "1", "Laptop")
            assert await register_user_hwid(db, user_id, "device-001", "Linux", "1", "Laptop")
            assert await register_user_hwid(db, user_id, "device-001", "Linux", "2", "Laptop")
            assert await register_user_hwid(db, user_id, "device-002", "Linux", "1", "Tablet")
            assert await delete_user_hwid(db, user_id, "device-001")
            assert not await delete_user_hwid(db, user_id, "device-001")
            assert await reset_user_hwids(db, user_id) == 1
            assert await reset_user_hwids(db, user_id) == 0
            rows = (await db.execute(
                select(subscription_device_events.c.event_type, subscription_device_events.c.status)
                .order_by(subscription_device_events.c.created_at, subscription_device_events.c.id)
            )).all()
            events = sorted(x[0] for x in rows)
            assert events == ["device.added", "device.added", "device.removed", "device.reset", "device.updated"]
            assert len(triggered) == 5
            assert all(x[1] == "pending" for x in rows)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_signed_webhook_really_delivers_and_retries_to_local_receiver(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "webhook.db"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    secret = "local-only-signing-secret-with-minimum-32-chars"
    monkeypatch.setenv("WAYLANE_DEVICE_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("WAYLANE_WEBHOOK_ALLOW_LOCAL_TEST", "1")

    accepted = []
    failures_remaining = 1

    async def receiver(reader, writer):
        nonlocal failures_remaining
        header = await reader.readuntil(b"\r\n\r\n")
        raw_headers = header.decode("latin1").split("\r\n")
        fields = {}
        for line in raw_headers[1:]:
            if ": " in line:
                name, value = line.split(": ", 1)
                fields[name.lower()] = value
        length = int(fields["content-length"])
        body = await reader.readexactly(length)
        expected = "sha256=" + hmac.new(
            secret.encode(), fields["x-waylane-timestamp"].encode() + b"." + body, hashlib.sha256
        ).hexdigest()
        assert hmac.compare_digest(fields["x-waylane-signature"], expected)
        payload = json.loads(body)
        assert fields["x-waylane-event-id"] == payload["id"]
        accepted.append(payload)
        status = b"503 Service Unavailable" if failures_remaining else b"200 OK"
        failures_remaining = max(0, failures_remaining - 1)
        writer.write(b"HTTP/1.1 " + status + b"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(receiver, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    @asynccontextmanager
    async def test_db():
        async with factory() as db:
            yield db

    monkeypatch.setattr(device_webhook, "GetDB", test_db)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as db:
            await db.execute(insert(subscription_firewall_config).values(
                id=1, enabled=True, scenarios=SCENARIOS,
                webhook_url="http://127.0.0.1:" + str(port) + "/webhook",
                webhook_enabled=True))
            event_id = await device_webhook.enqueue_device_event(
                db, "device.added", 42, "device-003", {"device_os": "Android"}
            )
            await db.commit()
        assert not await device_webhook.deliver_device_event(event_id)
        async with factory() as db:
            assert (await db.scalar(select(subscription_device_events.c.status))) == "failed"
        # Automatic worker skips a failure until its backoff is due.
        assert (await device_webhook.retry_due_device_events())["selected"] == 0
        assert await device_webhook.deliver_device_event(event_id, force=True)
        assert not await device_webhook.deliver_device_event(event_id)
        assert len(accepted) == 2
        assert accepted[0]["id"] == accepted[1]["id"] == event_id
        async with factory() as db:
            row = (await db.execute(select(subscription_device_events))).mappings().one()
            assert row["status"] == "delivered"
            assert row["attempts"] == 2
            assert row["delivered_at"] is not None
    finally:
        server.close()
        await server.wait_closed()
        await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_device_check_webhook_blocks_denied_but_handles_bot_error(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "check.db"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setenv("WAYLANE_WEBHOOK_ALLOW_LOCAL_TEST", "1")
    statuses = ["ACCESS_DENIED", "LIMIT_DEVICES", "BOT_ERROR", "ACCESS_GRANTED"]
    received = []

    async def receiver(reader, writer):
        headers = (await reader.readuntil(b"\r\n\r\n")).decode()
        length = next(int(line.split(":", 1)[1]) for line in headers.split("\r\n")
                      if line.lower().startswith("content-length:"))
        payload = json.loads(await reader.readexactly(length))
        received.append(payload)
        status = statuses.pop(0)
        body = json.dumps({"status": status}).encode()
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                     + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(receiver, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            await db.execute(insert(subscription_firewall_config).values(
                id=1, enabled=True, scenarios=SCENARIOS,
                device_check_url="http://127.0.0.1:" + str(port) + "/webhook/check_device"))
            await db.commit()
            request = dict(
                token="fake-test-token", ip="127.0.0.1", hwid="device-01",
                user_agent="Happ/2.0", device_os="Android",
                os_version="15", device_model="Test phone",
            )
            with pytest.raises(HTTPException) as denied:
                await device_webhook.check_legacy_device_access(db, **request)
            assert denied.value.status_code == 403
            assert "Access denied" in denied.value.detail
            with pytest.raises(HTTPException) as limited:
                await device_webhook.check_legacy_device_access(db, **request)
            assert "Device limit" in limited.value.detail
            await device_webhook.check_legacy_device_access(db, **request)
            await device_webhook.check_legacy_device_access(db, **request)
        assert [item["hwid"]["hwid"] for item in received] == ["device-01"] * 4
        assert all(item["subscription"] == "fake-test-token" for item in received)
    finally:
        server.close()
        await server.wait_closed()
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_designer_persists_edits_and_previews_encoded_payload(tmp_path):
    from app.routers.subscription_firewall import (
        get_firewall_editor, preview_message, save_firewall_editor,
    )
    engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "designer.db"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with factory() as db:
            config = FirewallEditor(
                enabled=True, scenarios={
                    **SCENARIOS,
                    "device_limit": {
                        "title": "Слишком много устройств",
                        "lines": ["Удалите неиспользуемое устройство"],
                    },
                },
                webhook_enabled=False,
            )
            result = await save_firewall_editor(config, db, None)
            assert result.enabled
            stored = await get_firewall_editor(db, None)
            assert stored.scenarios["device_limit"].title == "Слишком много устройств"
            preview = await preview_message(
                "device_limit", stored.scenarios["device_limit"], None
            )
            uri = base64.b64decode(preview["base64_payload"]).decode("utf-8")
            assert uri.startswith("ss://")
            assert unquote(uri.split("#", 1)[1]) == "Удалите неиспользуемое устройство"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_dispatchers_claim_only_one_webhook(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "concurrent.db"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setenv("WAYLANE_DEVICE_WEBHOOK_SECRET", "another-strong-local-test-secret-at-least-32")
    monkeypatch.setenv("WAYLANE_WEBHOOK_ALLOW_LOCAL_TEST", "1")
    delivered = []

    async def receiver(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        delivered.append(True)
        await asyncio.sleep(0.15)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(receiver, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    @asynccontextmanager
    async def test_db():
        async with factory() as db:
            yield db

    monkeypatch.setattr(device_webhook, "GetDB", test_db)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            await db.execute(insert(subscription_firewall_config).values(
                id=1, enabled=False, scenarios=SCENARIOS, webhook_enabled=True,
                webhook_url="http://127.0.0.1:" + str(port) + "/event"))
            event_id = await device_webhook.enqueue_device_event(db, "device.removed", 10, "removed-hwid")
            await db.commit()
        result = await asyncio.gather(
            device_webhook.deliver_device_event(event_id),
            device_webhook.deliver_device_event(event_id),
        )
        assert sorted(result) == [False, True]
        assert len(delivered) == 1
        async with factory() as db:
            row = (await db.execute(select(subscription_device_events))).mappings().one()
            assert row["status"] == "delivered"
            assert row["attempts"] == 1
    finally:
        server.close()
        await server.wait_closed()
        await engine.dispose()
