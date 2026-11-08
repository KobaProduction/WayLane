"""Node RPC transports UUIDv4 only; neither DB nor node carries a numeric subscriber key."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.nats.node_rpc import node_nats_client
from app.node.worker import NodeWorkerService
from app.operation.node import NodeOperation


@pytest.mark.asyncio
async def test_node_rpc_resolves_uuid_and_rejects_legacy_numeric_id():
    subscription_uuid = uuid4()
    db = SimpleNamespace(scalar=AsyncMock(return_value=subscription_uuid))

    assert await NodeWorkerService._subscription_uuid_for_rpc(None, db, {"subscription_uuid": str(subscription_uuid)}) == subscription_uuid
    db.scalar.assert_awaited_once()
    for payload in ({"user_id": 37}, {"subscription_uuid": "37"}, {"subscription_uuid": str(uuid4().hex)}):
        with pytest.raises((RuntimeError, TypeError)):
            await NodeWorkerService._subscription_uuid_for_rpc(None, db, payload)


@pytest.mark.asyncio
async def test_node_rpc_sender_does_not_send_internal_subscription_uuid(monkeypatch):
    sid = str(uuid4())
    request = AsyncMock(return_value={12: 1})
    monkeypatch.setattr(node_nats_client, "request", request)
    node = SimpleNamespace(_rpc_subscription_uuid=AsyncMock(return_value=sid))

    result = await NodeOperation._get_user_online_stats_remote(node, AsyncMock(), 5, uuid4())

    assert result == {12: 1}
    request.assert_awaited_once_with("get_subscription_online_stats", {"node_id": 5, "subscription_uuid": sid})
    node._rpc_subscription_uuid.assert_awaited_once()
