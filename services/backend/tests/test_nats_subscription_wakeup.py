"""A detached/shared NATS writer must wake a connected lazy node worker."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import certifi
import pytest

from app.node.nats_memory import NatsSubscriptionSyncStore
from tests.test_nats_sync_integration import jetstream  # noqa: F401 - isolated NATS fixture
from waylane_node_bridge.common.service_pb2 import Subscription
from waylane_node_bridge.controller import Controller


@pytest.mark.asyncio
async def test_external_kv_enqueue_restarts_idle_node_worker_and_disconnect_unsubscribes(jetstream, monkeypatch):  # noqa: F811
    node_id = f"externally-updated-{uuid4().hex[:8]}"
    reader = NatsSubscriptionSyncStore(jetstream.kv)
    writer = NatsSubscriptionSyncStore(jetstream.kv)
    node = Controller(
        service_url="https://localhost:1",
        server_ca=certifi.contents(),
        api_key=str(uuid4()),
        node_id=node_id,
        subscription_sync_store=reader,
        sync_poll_interval=0.01,
    )
    node._worker_idle_timeout = 0.05
    deliver = AsyncMock(return_value=[])
    monkeypatch.setattr(node, "_sync_batch_subscriptions", deliver, raising=False)
    monkeypatch.setattr(node, "_supports_chunked_sync", AsyncMock(return_value=(False, "fake unchunked core")))
    try:
        await node.connect("0.5.4", "26.3.27")
        async with asyncio.timeout(5):
            while node._sync_worker_task is not None:
                await asyncio.sleep(0.01)
        assert len(reader._update_listeners[node_id]) == 1

        subscription_uuid = str(uuid4())
        await writer.enqueue_subscriptions(node_id, [Subscription(subscription_uuid=subscription_uuid)])
        async with asyncio.timeout(7):
            while not deliver.await_count:
                await asyncio.sleep(0.01)
        assert [item.subscription_uuid for item in deliver.await_args.args[0]] == [subscription_uuid]
        async with asyncio.timeout(7):
            while await reader.has_pending(node_id):
                await asyncio.sleep(0.01)

        await node.disconnect()
        assert node_id not in reader._update_listeners
        await writer.enqueue_subscriptions(node_id, [Subscription(subscription_uuid=str(uuid4()))])
        await asyncio.sleep(0.15)
        deliver.assert_awaited_once()
    finally:
        await node.disconnect()
        await reader.close()
        await writer.close()
