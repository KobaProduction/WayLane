"""WayLane API and worker agree on native subscription commands only."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.nats.proto_utils import serialize_proto_message, serialize_proto_messages
from app.node import worker as node_worker
from config import NatsSettings
from waylane_node_bridge.common.service_pb2 import Subscription


def test_node_subscription_kv_bucket_rejects_legacy_user_namespace():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="new v2 subscription KV bucket"):
        NatsSettings(NATS_NODE_SUBSCRIPTION_SYNC_KV_BUCKET="waylane_node_user_sync")

    assert NatsSettings.model_fields["node_subscription_sync_kv_bucket"].default == "waylane_node_subscription_sync"


@pytest.mark.asyncio
async def test_worker_accepts_subscription_commands_and_rejects_old_actions(monkeypatch: pytest.MonkeyPatch):
    worker = node_worker.NodeWorkerService()
    single = AsyncMock()
    batch = AsyncMock()
    monkeypatch.setattr(node_worker.node_manager, "update_subscription", single)
    monkeypatch.setattr(node_worker.node_manager, "update_subscriptions", batch)

    assert {"update_subscription", "update_subscriptions", "sync_node_subscriptions"}.issubset(worker._command_handlers)
    assert {"update_user", "update_users", "sync_node_users"}.isdisjoint(worker._command_handlers)

    subscription_uuid = str(uuid4())
    message = Subscription(subscription_uuid=subscription_uuid, inbounds=["xray"])
    await worker._dispatch_command("update_subscription", {"subscription": serialize_proto_message(message)})
    single.assert_awaited_once()
    assert single.await_args.args[0].subscription_uuid == subscription_uuid

    await worker._dispatch_command("update_subscriptions", {"subscriptions": serialize_proto_messages([message])})
    batch.assert_awaited_once()
    assert [item.subscription_uuid for item in batch.await_args.args[0]] == [subscription_uuid]

    await worker._dispatch_command("update_user", {"user": serialize_proto_message(message)})
    await worker._dispatch_command("update_subscription", {"user": serialize_proto_message(message)})
    assert single.await_count == 1


@pytest.mark.asyncio
async def test_worker_node_sync_uses_explicit_subscription_flush_flag(monkeypatch: pytest.MonkeyPatch):
    worker = node_worker.NodeWorkerService()
    worker._node_operator.sync_node_subscriptions = AsyncMock()
    monkeypatch.setattr(node_worker.core_manager, "_reload_from_cache", AsyncMock())

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(node_worker, "GetDB", Session)
    await worker._dispatch_command("sync_node_subscriptions", {"node_id": 9, "flush_subscriptions": False})
    worker._node_operator.sync_node_subscriptions.assert_awaited_once()
    assert worker._node_operator.sync_node_subscriptions.await_args.kwargs == {
        "node_id": 9,
        "flush_subscriptions": False,
    }
