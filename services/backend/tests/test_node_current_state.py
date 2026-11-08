"""UUIDv4 node-current-state delivery: coalescing, freshness, failure isolation and removals."""

import asyncio
import contextlib
from uuid import UUID

import pytest

from app.node import sync as node_sync
from waylane_node_bridge import create_proxy
from waylane_node_bridge.common.service_pb2 import Subscription


def _uuid(index: int) -> UUID:
    """Deterministic canonical UUIDv4 for testing odd/even subscriber states."""
    return UUID(int=index, version=4)


class FakeDB:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def proto(subscription_uuid: UUID, inbounds=("in",)) -> Subscription:
    return Subscription(subscription_uuid=str(subscription_uuid), inbounds=list(inbounds))


@pytest.fixture
def reader(monkeypatch):
    monkeypatch.setattr(node_sync, "GetDB", FakeDB)
    calls = []

    async def core_subscriptions(db, inbound_tags=None, allowed_protocols=None, subscription_uuids=None):
        calls.append(sorted(subscription_uuids))
        return [proto(sid) for sid in subscription_uuids if sid.int % 2 == 1]

    async def removal_subscriptions(db, subscription_uuids):
        # Multiples of 4 still exist but are disabled/blocked and require a
        # revocation retaining WireGuard credentials. Other even UUIDs are gone.
        return [
            Subscription(
                subscription_uuid=str(sid),
                proxies=create_proxy(wireguard_public_key="key", wireguard_peer_ips=["10.0.0.2/32"]),
            )
            for sid in subscription_uuids
            if sid.int % 4 == 0
        ]

    monkeypatch.setattr(node_sync, "core_subscriptions", core_subscriptions)
    monkeypatch.setattr(node_sync, "removal_subscriptions", removal_subscriptions)
    instance = node_sync.CurrentStateReader(gather_seconds=0.01, max_ids_per_query=400, query_timeout=0.5)
    monkeypatch.setattr(node_sync, "_current_state", instance)
    return instance, calls


@pytest.mark.asyncio
async def test_concurrent_requests_share_one_indexed_query(reader):
    instance, calls = reader
    first, second = await asyncio.gather(instance.get([_uuid(1), _uuid(2)]), instance.get([_uuid(3)]))
    assert calls == [[_uuid(1), _uuid(2), _uuid(3)]]
    assert set(first) == {_uuid(1), _uuid(2)}
    assert first[_uuid(1)].subscription_uuid == str(_uuid(1))
    assert first[_uuid(2)] is None
    assert second[_uuid(3)].subscription_uuid == str(_uuid(3))


@pytest.mark.asyncio
async def test_request_registered_after_a_query_started_is_served_by_the_next_query(monkeypatch, reader):
    instance, calls = reader
    started, release = asyncio.Event(), asyncio.Event()
    original = node_sync.core_subscriptions

    async def blocking_core_users(db, **kwargs):
        started.set()
        await release.wait()
        return await original(db, **kwargs)

    monkeypatch.setattr(node_sync, "core_subscriptions", blocking_core_users)
    first = asyncio.create_task(instance.get([_uuid(1)]))
    await started.wait()
    second = asyncio.create_task(instance.get([_uuid(3)]))
    await asyncio.sleep(0.05)
    assert not second.done()
    release.set()
    assert (await first)[_uuid(1)].subscription_uuid == str(_uuid(1))
    assert (await second)[_uuid(3)].subscription_uuid == str(_uuid(3))
    assert calls == [[_uuid(1)], [_uuid(3)]]


@pytest.mark.asyncio
async def test_hung_query_fails_only_its_batch_and_later_requests_recover(monkeypatch, reader):
    instance, _ = reader
    original = node_sync.core_subscriptions
    hang = True

    async def maybe_hanging(db, **kwargs):
        if hang:
            await asyncio.sleep(10)
        return await original(db, **kwargs)

    monkeypatch.setattr(node_sync, "core_subscriptions", maybe_hanging)
    with pytest.raises(TimeoutError):
        await instance.get([_uuid(1)])
    hang = False
    assert (await instance.get([_uuid(3)]))[_uuid(3)].subscription_uuid == str(_uuid(3))
    assert not instance._pending


@pytest.mark.asyncio
async def test_cancelling_one_waiter_does_not_affect_its_sibling(monkeypatch, reader):
    instance, calls = reader
    started, release = asyncio.Event(), asyncio.Event()
    original = node_sync.core_subscriptions

    async def blocking_core_users(db, **kwargs):
        started.set()
        await release.wait()
        return await original(db, **kwargs)

    monkeypatch.setattr(node_sync, "core_subscriptions", blocking_core_users)
    first = asyncio.create_task(instance.get([_uuid(1)]))
    second = asyncio.create_task(instance.get([_uuid(3)]))
    await started.wait()
    first.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await first
    release.set()
    assert (await second)[_uuid(3)].subscription_uuid == str(_uuid(3))
    assert calls == [[_uuid(1), _uuid(3)]]


@pytest.mark.asyncio
async def test_large_batches_are_split_into_bounded_queries(reader):
    instance, calls = reader
    uuids = [_uuid(index) for index in range(1, 901)]
    result = await instance.get(uuids)
    assert len(calls) == 3 and all(len(call) <= 400 for call in calls)
    assert sum(1 for value in result.values() if value is not None and value.inbounds) == 450
    assert sum(1 for value in result.values() if value is not None and not value.inbounds) == 225


@pytest.mark.asyncio
async def test_refresh_resolves_missing_disabled_and_blocked_users_to_removals(reader):
    del reader
    payloads = {
        sub.subscription_uuid: sub
        for sub in await node_sync.refresh_node_subscriptions(
            "9", [str(_uuid(1)), str(_uuid(2)), "not-a-uuid", str(_uuid(4))]
        )
    }
    assert list(payloads[str(_uuid(1))].inbounds) == ["in"]
    assert list(payloads[str(_uuid(2))].inbounds) == []
    assert list(payloads[str(_uuid(4))].inbounds) == []
    assert payloads[str(_uuid(4))].proxies.wireguard.public_key == "key"
    assert not payloads[str(_uuid(2))].HasField("proxies")
    assert "not-a-uuid" not in payloads


@pytest.mark.asyncio
async def test_twelve_nodes_delivering_the_same_user_share_reads(reader):
    _, calls = reader
    results = await asyncio.gather(
        *(node_sync.refresh_node_subscriptions(str(node), [str(_uuid(1))]) for node in range(12))
    )
    assert all(list(result[0].inbounds) == ["in"] for result in results)
    assert len(calls) <= 2


@pytest.mark.asyncio
async def test_empty_and_invalid_uuid_requests_do_not_query(reader):
    instance, calls = reader
    assert await node_sync.refresh_node_subscriptions("9", ["not-a-uuid"]) == []
    assert await instance.get([]) == {}
    assert calls == []
