"""NATS JetStream KV backends for WayLane node bridge shared memory."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import time
from collections.abc import Awaitable, Callable
from itertools import islice
from typing import Any
from uuid import uuid4

import nats
import nats.js.errors as nats_js_errors
from nats.js.client import JetStreamContext
from nats.js.kv import KeyValue

from app.nats import needs_shared_bridge_memory
from app.nats.client import create_nats_client, get_jetstream_context, get_or_create_kv_bucket
from app.nats.kv_cas import CasKv, cas_retry_backoff, kv_cas_json, kv_get_json
from app.nats.kv_index import KvKeyIndex
from app.nats.kv_watch import watch_kv
from app.utils.logger import get_logger
from config import nats_settings
from waylane_node_bridge.common.service_pb2 import Subscription as WireSubscription
from waylane_node_bridge.storage import (
    ClaimedSubscription,
    LifecycleLease,
    LifecycleOperation,
    LifecycleStatus,
    NodeLifecycleState,
)

logger = get_logger("node-nats-memory")

WORKER_ID = f"{os.getpid()}:{uuid4().hex[:8]}"
# Stay under default NATS max_payload (1MiB) with headroom for JSON framing.
_MAX_SUBSCRIPTION_SYNC_VALUE_BYTES = min(900_000, nats_settings.node_command_max_payload_bytes)

_nc: nats.NATS | None = None
_subscription_sync_kv: KeyValue | None = None
_lifecycle_kv: KeyValue | None = None
_subscription_sync_store: NatsSubscriptionSyncStore | None = None
_lifecycle_coordinator: NatsNodeLifecycleCoordinator | None = None
_init_lock = asyncio.Lock()


def _encode_subscription(subscription: WireSubscription) -> str:
    return base64.b64encode(subscription.SerializeToString()).decode("ascii")


def _decode_subscription(data: str) -> WireSubscription:
    subscription = WireSubscription()
    subscription.ParseFromString(base64.b64decode(data.encode("ascii")))
    return subscription


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _empty_lifecycle_doc() -> dict[str, Any]:
    return {"state": None, "lease": None}


def _state_from_dict(data: dict[str, Any] | None) -> NodeLifecycleState:
    if not data:
        return NodeLifecycleState()
    operation = data.get("operation")
    return NodeLifecycleState(
        desired=LifecycleStatus(data.get("desired", LifecycleStatus.UNKNOWN)),
        observed=LifecycleStatus(data.get("observed", LifecycleStatus.UNKNOWN)),
        epoch=int(data.get("epoch", 0)),
        operation=LifecycleOperation(operation) if operation else None,
        owner=data.get("owner"),
        node_version=data.get("node_version", "") or "",
        core_version=data.get("core_version", "") or "",
        updated_at=float(data.get("updated_at", 0.0) or 0.0),
    )


def _state_to_dict(state: NodeLifecycleState) -> dict[str, Any]:
    return {
        "desired": state.desired.value,
        "observed": state.observed.value,
        "epoch": state.epoch,
        "operation": state.operation.value if state.operation else None,
        "owner": state.owner,
        "node_version": state.node_version,
        "core_version": state.core_version,
        "updated_at": state.updated_at,
    }


RefreshResolver = Callable[[str, list[str]], Awaitable[list[WireSubscription]]]


class NatsSubscriptionSyncStore:
    """One CAS-guarded KV document per (node, subscription UUID) for the shared delta queue.

    Key ``p.<node>.<digest(subscription_uuid)>`` holds ``{"subscription_uuid", "subscription"}`` plus an optional
    ``"claim": {"worker", "until"}`` while a worker is delivering it. Every state
    transition is a revision-guarded write:

    - enqueue: latest payload wins and replaces any in-flight claim, so a stale
      delivery can never acknowledge or requeue over a newer state;
    - claim: in-place update adding the claim, exclusive by CAS;
    - ack: delete guarded by the claim revision (fails when a newer payload
      arrived, leaving that payload pending);
    - requeue / expiry: clear the claim by CAS, or let anyone re-claim once the
      lease has expired. An old payload therefore cannot resurrect a newer one
      even after the newer one was delivered and removed.

    A refresh marker ``{"subscription_uuid", "refresh": true}`` asks whichever worker scans
    next to re-derive the subscription from the source of truth; the resolved payload
    replaces exactly that marker revision, never a newer enqueue.

    A full snapshot never drains the queue destructively: ``capture_queued``
    records the current revisions behind the fence, and ``retire_captured``
    deletes exactly those revisions only after the snapshot was applied.
    """

    def __init__(self, kv: CasKv):
        self._kv = kv
        self._update_listeners: dict[str, set[Callable[[], None]]] = {}
        self._key_index = KvKeyIndex(kv, on_put=self._notify_pending_update)
        self._write_slots = asyncio.Semaphore(32)
        # key -> (revision, lease expiry) as last read for that revision, so
        # scans skip other workers' in-flight subscriptions (and quiescence polls skip
        # unchanged unclaimed entries) without a KV read.
        self._claims_seen: dict[str, tuple[int, float]] = {}
        # Returns current payloads for refresh markers. Installed by the
        # application layer; markers stay queued until it succeeds.
        self.refresh_resolver: RefreshResolver | None = None

    def subscribe_updates(self, node_id: str, callback: Callable[[], None]) -> Callable[[], None]:
        """Wake only controllers attached to the node receiving a KV PUT."""
        node_id = str(node_id)
        callbacks = self._update_listeners.setdefault(node_id, set())
        callbacks.add(callback)

        def unsubscribe() -> None:
            subscribers = self._update_listeners.get(node_id)
            if subscribers is not None:
                subscribers.discard(callback)
                if not subscribers:
                    self._update_listeners.pop(node_id, None)

        return unsubscribe

    def _notify_pending_update(self, key: str) -> None:
        parts = key.split(".", 2)
        if len(parts) != 3 or parts[0] != "p":
            return
        for callback in tuple(self._update_listeners.get(parts[1], ())):
            callback()

    async def close(self) -> None:
        self._update_listeners.clear()
        await self._key_index.close()

    async def has_pending(self, node_id: str) -> bool:
        """Keep polling for leased work without scanning every KV value."""
        return bool(await self._key_index.entries(self._pending_prefix(node_id)))

    async def _run_bounded(self, operation, items) -> None:
        async def run(item):
            async with self._write_slots:
                await operation(item)

        items = iter(items)
        while batch := list(islice(items, 32)):
            # Bound both live tasks and concurrent KV operations during full syncs.
            results = await asyncio.gather(*(run(item) for item in batch), return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    raise result

    def _pending_prefix(self, node_id: str) -> str:
        return f"p.{node_id}."

    def _fence_key(self, node_id: str) -> str:
        return f"f.{node_id}.sync"

    def _pending_key(self, node_id: str, subscription_uuid: str) -> str:
        return f"{self._pending_prefix(node_id)}{_digest(subscription_uuid)}"

    @staticmethod
    def _b64(text: str) -> str:
        return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")

    @staticmethod
    def _unb64(text: str) -> str:
        try:
            return base64.urlsafe_b64decode(text.encode("ascii")).decode("utf-8")
        except ValueError, UnicodeDecodeError:
            return ""

    @classmethod
    def _token(cls, key: str, revision: int, subscription_uuid: str, claim_id: str) -> str:
        # digest.revision.subscription_uuid(base64url).claim_id: opaque to the bridge, and
        # self-describing for acknowledgements, requeues, and claim releases.
        return f"{key.rpartition('.')[2]}.{revision}.{cls._b64(subscription_uuid)}.{claim_id}"

    def _token_parts(self, node_id: str, token: str) -> tuple[str, int, str, str] | None:
        parts = token.split(".")
        if len(parts) != 4 or not parts[0] or not parts[1].isdigit():
            return None
        digest, revision, subscription_uuid, claim_id = parts
        return f"{self._pending_prefix(node_id)}{digest}", int(revision), self._unb64(subscription_uuid), claim_id

    def _ensure_value_size(self, key: str, value: dict[str, Any]) -> None:
        size = len(json.dumps(value, separators=(",", ":")).encode())
        if size > _MAX_SUBSCRIPTION_SYNC_VALUE_BYTES:
            raise RuntimeError(
                f"subscription sync KV value for key={key} is {size} bytes; exceeds limit {_MAX_SUBSCRIPTION_SYNC_VALUE_BYTES}"
            )

    @staticmethod
    def _encode(value: dict[str, Any]) -> bytes:
        return json.dumps(value, separators=(",", ":")).encode()

    # --- enqueue / restore ---------------------------------------------------

    async def _enqueue_one(self, node_id: str, subscription_uuid: str, subscription: WireSubscription) -> None:
        key = self._pending_key(node_id, subscription_uuid)
        value = {"subscription_uuid": subscription_uuid, "subscription": _encode_subscription(subscription)}
        self._ensure_value_size(key, value)
        for attempt in range(32):
            doc, rev = await kv_get_json(self._kv, key)
            # Keep an unexpired claim visible: its owner's RPC may still be in
            # flight and a full snapshot must be able to wait for it. The owner
            # releases the claim when its acknowledgement finds a newer payload.
            claim = (doc or {}).get("claim")
            payload = dict(value)
            if isinstance(claim, dict) and float(claim.get("until", 0)) > time.time():
                payload["claim"] = claim
            try:
                revision = (
                    await self._kv.create(key, self._encode(payload))
                    if rev == 0
                    else await self._kv.update(key, self._encode(payload), last=rev)
                )
            except nats.errors.Error as exc:
                logger.debug("Enqueue CAS attempt failed for key=%s revision=%s: %s", key, rev, exc)
                if attempt < 31:
                    await cas_retry_backoff()
                continue
            self._key_index.observe_put(key, revision)
            self._claims_seen[key] = (revision, float(payload["claim"]["until"]) if "claim" in payload else 0.0)
            return
        raise RuntimeError(f"failed to enqueue NATS KV key={key} after CAS retries")

    async def _release_claim(self, key: str, claim_id: str) -> None:
        """Drop exactly our claim from an entry that changed underneath us, so its newer payload is claimable now.

        Ownership is the claim's unique id, not the worker: the same worker may
        already hold a newer claim on this entry after the old lease expired.
        """
        if not claim_id:
            return
        for attempt in range(8):
            doc, rev = await kv_get_json(self._kv, key)
            claim = (doc or {}).get("claim")
            if not isinstance(claim, dict) or claim.get("id") != claim_id:
                return
            released = {field: value for field, value in doc.items() if field != "claim"}
            try:
                revision = await self._kv.update(key, self._encode(released), last=rev)
            except nats.errors.Error:
                if attempt < 7:
                    await cas_retry_backoff()
                continue
            self._key_index.observe_put(key, revision)
            self._claims_seen[key] = (revision, 0.0)
            return

    async def enqueue_subscriptions(self, node_id: str, subscriptions: list[WireSubscription]) -> None:
        if not subscriptions:
            return
        # Latest payload per subscription_uuid wins (dedupe across the batch first).
        by_subscription_uuid = {subscription.subscription_uuid: subscription for subscription in subscriptions}
        await self._run_bounded(
            lambda subscription: self._enqueue_one(node_id, subscription.subscription_uuid, subscription),
            by_subscription_uuid.values(),
        )

    async def _create_if_absent(self, key: str, value: dict[str, Any]) -> bool:
        """Create-only write. Only a demonstrated CAS conflict counts as "newer present".

        Transport failures propagate so callers never discard work they failed to write.
        """
        self._ensure_value_size(key, value)
        try:
            revision = await self._kv.create(key, self._encode(value))
        except nats_js_errors.KeyWrongLastSequenceError as exc:
            logger.debug("Create skipped for key=%s (newer payload present): %s", key, exc)
            return False
        self._key_index.observe_put(key, revision)
        return True

    async def _restore_one(self, node_id: str, subscription: WireSubscription) -> None:
        await self._create_if_absent(
            self._pending_key(node_id, subscription.subscription_uuid),
            {"subscription_uuid": subscription.subscription_uuid, "subscription": _encode_subscription(subscription)},
        )

    async def _request_refresh_one(self, node_id: str, subscription_uuid: str) -> bool:
        """Mark a subscription for re-derivation unless a payload (or claim) is already queued for it.

        Create-only: an existing entry is either a newer payload that will be
        delivered anyway or an in-flight claim that must stay visible.
        """
        return await self._create_if_absent(
            self._pending_key(node_id, subscription_uuid), {"subscription_uuid": subscription_uuid, "refresh": True}
        )

    async def request_refresh(self, node_id: str, subscription_uuids: list[str]) -> None:
        """Durably ask any worker to re-derive these subscriptions before the next delivery.

        The marker holds no payload. A claim scan hands its subscription_uuid to the refresh
        resolver and replaces exactly the marker's revision with the result, so
        a newer API enqueue in the meantime is never overwritten. The marker
        survives until that succeeds.
        """
        if not subscription_uuids:
            return
        await self._run_bounded(
            lambda subscription_uuid: self._request_refresh_one(node_id, subscription_uuid),
            dict.fromkeys(subscription_uuids),
        )

    # --- claim / ack / requeue -----------------------------------------------

    async def _claim_doc(
        self, key: str, doc: dict[str, Any], revision: int, worker_id: str, lease_seconds: float
    ) -> ClaimedSubscription | None:
        subscription_uuid, subscription_b64 = doc["subscription_uuid"], doc["subscription"]
        until = time.time() + lease_seconds
        claim_id = uuid4().hex
        value = {
            "subscription_uuid": subscription_uuid,
            "subscription": subscription_b64,
            "claim": {"id": claim_id, "worker": worker_id, "until": until},
        }
        self._ensure_value_size(key, value)
        try:
            # Revision-guarded so two workers cannot claim the same payload.
            claimed_revision = await self._kv.update(key, self._encode(value), last=revision)
        except nats_js_errors.KeyWrongLastSequenceError as exc:
            # Only a demonstrated CAS conflict is a lost race. Transport
            # failures propagate: the worker keeps its wake signal and retries
            # with backoff instead of idling out on durable pending work.
            logger.debug("Claim race for pending key=%s: %s", key, exc)
            return None
        self._key_index.observe_put(key, claimed_revision)
        self._claims_seen[key] = (claimed_revision, until)
        return ClaimedSubscription(
            token=self._token(key, claimed_revision, subscription_uuid, claim_id),
            subscription=_decode_subscription(subscription_b64),
        )

    async def _resolve_refresh_markers(
        self, node_id: str, markers: dict[str, tuple[str, int]], worker_id: str, lease_seconds: float
    ) -> list[ClaimedSubscription]:
        """Replace refresh markers with current payloads and claim them for delivery."""
        if not markers:
            return []
        if self.refresh_resolver is None:
            logger.warning(
                "No refresh resolver installed; %s subscription(s) on node %s stay queued", len(markers), node_id
            )
            return []
        try:
            payloads = {
                subscription.subscription_uuid: subscription
                for subscription in await self.refresh_resolver(node_id, list(markers))
            }
        except Exception as exc:
            logger.warning(
                "Refresh of %s subscription(s) for node %s failed; retrying later: %s", len(markers), node_id, exc
            )
            return []
        claimed: list[ClaimedSubscription] = []
        for subscription_uuid, (key, revision) in markers.items():
            subscription = payloads.get(subscription_uuid)
            if subscription is None:
                # Nothing to send for this subscription; retire the marker unless it changed.
                try:
                    await self._kv.delete(key, last=revision)
                    self._key_index.discard(key, revision)
                except Exception as exc:
                    logger.debug("Refresh marker key=%s changed before retirement: %s", key, exc)
                continue
            # CAS against the marker revision: a payload enqueued after the
            # marker was read (possibly newer than our database read) wins.
            item = await self._claim_doc(
                key,
                {"subscription_uuid": subscription_uuid, "subscription": _encode_subscription(subscription)},
                revision,
                worker_id,
                lease_seconds,
            )
            if item is not None:
                claimed.append(item)
        return claimed

    async def claim_subscriptions(
        self, node_id: str, worker_id: str, limit: int, lease_seconds: float
    ) -> list[ClaimedSubscription]:
        if limit <= 0:
            return []
        # Apply the same batch budget as direct subscription updates. Large shared
        # claims can exceed the node RPC deadline and repeatedly requeue work.
        limit = min(limit, max(1, nats_settings.node_update_subscriptions_batch_size))

        result: list[ClaimedSubscription] = []
        markers: dict[str, tuple[str, int]] = {}
        entries = await self._key_index.entries(self._pending_prefix(node_id))
        if len(self._claims_seen) > len(entries) + 256:
            self._claims_seen = {key: seen for key, seen in self._claims_seen.items() if key in entries}
        for pending_key, indexed_revision in entries.items():
            if len(result) + len(markers) >= limit:
                break
            now = time.time()
            seen = self._claims_seen.get(pending_key)
            if seen is not None and seen[0] == indexed_revision and seen[1] > now:
                continue
            doc, rev = await kv_get_json(self._kv, pending_key)
            if doc is None:
                self._key_index.discard(pending_key, indexed_revision)
                self._claims_seen.pop(pending_key, None)
                continue
            claim = doc.get("claim")
            if isinstance(claim, dict) and float(claim.get("until", 0)) > now:
                self._claims_seen[pending_key] = (rev, float(claim["until"]))
                continue
            self._claims_seen[pending_key] = (rev, 0.0)
            subscription_uuid = doc.get("subscription_uuid")
            if not isinstance(subscription_uuid, str):
                continue
            if doc.get("refresh") and not isinstance(doc.get("subscription"), str):
                markers[subscription_uuid] = (pending_key, rev)
                continue
            if not isinstance(doc.get("subscription"), str):
                continue
            item = await self._claim_doc(pending_key, doc, rev, worker_id, lease_seconds)
            if item is not None:
                result.append(item)
        result.extend(await self._resolve_refresh_markers(node_id, markers, worker_id, lease_seconds))
        return result

    async def _ack_one(self, node_id: str, token: str, unconfirmed: list[str]) -> None:
        parts = self._token_parts(node_id, token)
        if parts is None:
            return
        key, revision, subscription_uuid, claim_id = parts
        try:
            deleted = await self._kv.delete(key, last=revision)
        except Exception as exc:
            logger.debug("Ack CAS failed for key=%s revision=%s: %s", key, revision, exc)
            deleted = False
        if deleted:
            self._key_index.discard(key, revision)
            self._claims_seen.pop(key, None)
            return
        doc, _ = await kv_get_json(self._kv, key)
        if doc is None:
            if subscription_uuid:
                # The payload we delivered was removed by someone else (a newer
                # delivery or a full sync). Our copy may have landed last, so
                # the caller should re-derive the current state.
                unconfirmed.append(subscription_uuid)
            return
        # A newer payload is pending and will be delivered after ours; make it
        # claimable immediately if it still carries our (preserved) claim.
        await self._release_claim(key, claim_id)

    async def ack_subscriptions(self, node_id: str, tokens: list[str]) -> list[str]:
        """Acknowledge delivered claims; returns subscription_uuids whose latest state could not be confirmed."""
        if not tokens:
            return []
        unconfirmed: list[str] = []
        await self._run_bounded(lambda token: self._ack_one(node_id, token, unconfirmed), tokens)
        return unconfirmed

    async def _requeue_one(self, node_id: str, item: ClaimedSubscription, failed: list[str]) -> None:
        parts = self._token_parts(node_id, item.token)
        if parts is None:
            return
        key, revision, _, claim_id = parts
        value = {
            "subscription_uuid": item.subscription.subscription_uuid,
            "subscription": _encode_subscription(item.subscription),
        }
        self._ensure_value_size(key, value)
        try:
            new_revision = await self._kv.update(key, self._encode(value), last=revision)
        except Exception as exc:
            # A newer payload replaced our claim, or the work was drained; the
            # stale copy must not come back. Report it so the caller can repair
            # from the source of truth when needed.
            logger.debug("Requeue skipped for key=%s revision=%s: %s", key, revision, exc)
            failed.append(item.subscription.subscription_uuid)
            await self._release_claim(key, claim_id)
            return
        self._key_index.observe_put(key, new_revision)
        self._claims_seen[key] = (new_revision, 0.0)

    async def requeue_subscriptions(self, node_id: str, claimed_users: list[ClaimedSubscription]) -> list[str]:
        """Return claimed payloads to the queue; returns subscription_uuids that could not be requeued."""
        if not claimed_users:
            return []
        failed: list[str] = []
        await self._run_bounded(lambda item: self._requeue_one(node_id, item, failed), claimed_users)
        return failed

    async def _refresh_claimed_one(self, node_id: str, item: ClaimedSubscription) -> None:
        parts = self._token_parts(node_id, item.token)
        if parts is None:
            return
        key, revision, subscription_uuid, claim_id = parts
        marker = {"subscription_uuid": subscription_uuid or item.subscription.subscription_uuid, "refresh": True}
        try:
            new_revision = await self._kv.update(key, self._encode(marker), last=revision)
        except nats_js_errors.KeyWrongLastSequenceError:
            # Newer payload present (it wins; release our claim on it) or the
            # entry is gone (recreate the marker).
            if not await self._create_if_absent(key, marker):
                await self._release_claim(key, claim_id)
            return
        self._key_index.observe_put(key, new_revision)
        self._claims_seen[key] = (new_revision, 0.0)

    async def refresh_claimed(self, node_id: str, claimed_users: list[ClaimedSubscription]) -> None:
        """Turn delivered-but-unconfirmable claims into refresh markers.

        Used when a delivery crossed a full snapshot: replaying the delivered
        payload could regress state the snapshot already carried, so the
        current state is re-derived instead. A newer queued payload always wins.
        """
        if not claimed_users:
            return
        await self._run_bounded(lambda item: self._refresh_claimed_one(node_id, item), claimed_users)

    # --- full-snapshot capture / retire / clear --------------------------------

    async def _discover_entries(self, node_id: str) -> dict[str, int]:
        """Keys and newest known revisions for a node, caught up to the current stream end.

        The live index may lag behind other workers' writes, so the tail after
        its checkpoint is replayed in order (deletes included) before the
        result is trusted. Nothing here relies on cached claim state alone.
        """
        prefixes = (self._pending_prefix(node_id),)
        if not isinstance(self._kv, KeyValue):
            entries: dict[str, int] = {}
            for prefix in prefixes:
                entries.update(await self._key_index.entries(prefix))
            return entries
        entries, revision = await self._key_index.snapshot_entries(prefixes)
        watcher = await watch_kv(self._kv, f"*.{node_id}.*", snapshot_only=True, start_revision=revision + 1)
        try:
            async for entry in watcher:
                if entry is None:
                    break
                if not entry.key.startswith(prefixes):
                    continue
                if entry.operation in ("DEL", "PURGE"):
                    entries.pop(entry.key, None)
                else:
                    entries[entry.key] = max(entries.get(entry.key, 0), entry.revision)
        finally:
            await watcher.stop()
        return entries

    async def capture_queued(self, node_id: str) -> tuple[dict[str, int], int]:
        """Record the current revision of every unclaimed queued entry for a node.

        Returns ``(captured, active_claims)``. Nothing is deleted. Entries under
        an unexpired claim are counted, not captured: their owner's delivery is
        in flight and, if it crosses a full snapshot, re-derives the current
        state. Entries whose newest revision was already read are not re-read.
        """
        captured: dict[str, int] = {}
        active = 0
        now = time.time()
        known = await self._discover_entries(node_id)

        async def capture(item: tuple[str, int]) -> None:
            nonlocal active
            key, newest = item
            seen = self._claims_seen.get(key)
            if seen is not None and newest > 0 and seen[0] == newest:
                rev, until = seen
            else:
                doc, rev = await kv_get_json(self._kv, key)
                if doc is None:
                    self._claims_seen.pop(key, None)
                    return
                claim = doc.get("claim")
                until = float(claim.get("until", 0)) if isinstance(claim, dict) else 0.0
                self._claims_seen[key] = (rev, until)
            if until > now:
                active += 1
                return
            captured[key] = rev

        await self._run_bounded(capture, known.items())
        return captured, active

    async def retire_captured(self, node_id: str, captured: dict[str, int]) -> int:
        """Delete exactly the captured revisions; anything newer survives for delivery."""
        retired = 0

        async def retire(item: tuple[str, int]) -> None:
            nonlocal retired
            key, revision = item
            try:
                if not await self._kv.delete(key, last=revision):
                    return
            except Exception as exc:
                logger.debug("Captured entry key=%s changed before retirement: %s", key, exc)
                return
            retired += 1
            self._key_index.discard(key, revision)
            self._claims_seen.pop(key, None)

        await self._run_bounded(retire, captured.items())
        return retired

    async def _clear_one(self, key: str) -> None:
        doc, rev = await kv_get_json(self._kv, key)
        if doc is None:
            return
        try:
            await self._kv.delete(key, last=rev)
        except Exception as exc:
            logger.debug("Failed to clear key=%s: %s", key, exc)
            return
        self._claims_seen.pop(key, None)

    async def clear(self, node_id: str) -> None:
        """Drop all queued work for a node (node removal / explicit bridge flush)."""
        await self._run_bounded(self._clear_one, list(await self._discover_entries(node_id)))

    # --- full-sync fence -------------------------------------------------
    #
    # A full snapshot is only coherent if no delta enqueued after the snapshot
    # was read lands on the node before the snapshot does. The fence is a
    # leased marker with a generation that survives release: workers refuse to
    # claim while it is active, and a delivery whose claim observed an older
    # generation is requeued instead of acknowledged, so it is re-applied on
    # top of the snapshot.

    async def begin_full_sync(self, node_id: str, worker_id: str, lease_seconds: float) -> str | None:
        key = self._fence_key(node_id)
        for attempt in range(8):
            now = time.time()
            doc, rev = await kv_get_json(self._kv, key)
            if doc is not None and float(doc.get("until", 0)) > now:
                return None
            token = f"{worker_id}:{uuid4()}"
            generation = int((doc or {}).get("generation", 0)) + 1
            value = {
                "generation": generation,
                "token": token,
                "worker": worker_id,
                "started_at": now,
                "until": now + lease_seconds,
            }
            if await kv_cas_json(self._kv, key, value, rev):
                return token
            if attempt < 7:
                await cas_retry_backoff()
        return None

    async def end_full_sync(self, node_id: str, token: str) -> None:
        key = self._fence_key(node_id)
        for attempt in range(8):
            doc, rev = await kv_get_json(self._kv, key)
            if doc is None or doc.get("token") != token:
                return
            doc["until"] = 0
            doc["token"] = None
            if await kv_cas_json(self._kv, key, doc, rev):
                return
            if attempt < 7:
                await cas_retry_backoff()
        logger.warning("Full-sync fence release CAS exhausted for node_id=%s", node_id)

    async def renew_full_sync(self, node_id: str, token: str, lease_seconds: float) -> bool:
        key = self._fence_key(node_id)
        for attempt in range(8):
            now = time.time()
            doc, rev = await kv_get_json(self._kv, key)
            if doc is None or doc.get("token") != token:
                return False
            if float(doc.get("until", 0)) <= now:
                # Expired: siblings may already deliver again. Never resurrect it.
                return False
            doc["until"] = now + lease_seconds
            if await kv_cas_json(self._kv, key, doc, rev):
                return True
            if attempt < 7:
                await cas_retry_backoff()
        return False

    async def fence_state(self, node_id: str) -> tuple[bool, int]:
        """(active, generation) for the node's full-sync fence."""
        doc, _ = await kv_get_json(self._kv, self._fence_key(node_id))
        if doc is None:
            return False, 0
        return float(doc.get("until", 0)) > time.time(), int(doc.get("generation", 0))

    async def full_sync_active(self, node_id: str) -> bool:
        active, _ = await self.fence_state(node_id)
        return active

    async def clear_full_sync(self, node_id: str) -> None:
        key = self._fence_key(node_id)
        doc, rev = await kv_get_json(self._kv, key)
        if doc is None:
            return
        try:
            await self._kv.delete(key, last=rev)
        except Exception as exc:
            logger.debug("Failed to clear full-sync fence key=%s: %s", key, exc)


class NatsNodeLifecycleCoordinator:
    def __init__(self, kv: CasKv):
        self._kv = kv

    def _key(self, node_id: str) -> str:
        return f"lifecycle.{node_id}"

    async def try_acquire(
        self, node_id: str, worker_id: str, operation: LifecycleOperation, lease_seconds: float
    ) -> LifecycleLease | None:
        key = self._key(node_id)
        for attempt in range(32):
            now = time.time()
            doc, rev = await kv_get_json(self._kv, key)
            if doc is None:
                doc = _empty_lifecycle_doc()

            lease_data = doc.get("lease")
            if lease_data is not None and float(lease_data.get("expires_at", 0)) > now:
                return None

            state = _state_from_dict(doc.get("state"))
            epoch = state.epoch + 1
            lease = LifecycleLease(
                node_id=node_id,
                worker_id=worker_id,
                operation=operation,
                token=f"{worker_id}:{uuid4()}",
                epoch=epoch,
                lease_seconds=lease_seconds,
            )
            state.epoch = epoch
            state.operation = operation
            state.owner = worker_id
            state.updated_at = now
            if operation is LifecycleOperation.START:
                state.desired = LifecycleStatus.HEALTHY
                state.observed = LifecycleStatus.STARTING
            elif operation is LifecycleOperation.STOP:
                state.desired = LifecycleStatus.STOPPED
                state.observed = LifecycleStatus.STOPPING

            doc["state"] = _state_to_dict(state)
            doc["lease"] = {
                "token": lease.token,
                "worker_id": worker_id,
                "operation": operation.value,
                "epoch": epoch,
                "lease_seconds": lease_seconds,
                "expires_at": now + lease_seconds,
            }
            if await kv_cas_json(self._kv, key, doc, rev):
                return lease
            if attempt < 31:
                await cas_retry_backoff()
        return None

    async def release(self, lease: LifecycleLease, state_update: NodeLifecycleState | None = None) -> None:
        key = self._key(lease.node_id)
        for attempt in range(32):
            now = time.time()
            doc, rev = await kv_get_json(self._kv, key)
            if doc is None:
                return
            lease_data = doc.get("lease")
            if lease_data is None or lease_data.get("token") != lease.token:
                return

            state = state_update or _state_from_dict(doc.get("state"))
            if state.epoch != lease.epoch:
                state.epoch = lease.epoch
            state.operation = None
            state.owner = None
            state.updated_at = now
            doc["state"] = _state_to_dict(state)
            doc["lease"] = None
            if await kv_cas_json(self._kv, key, doc, rev):
                return
            if attempt < 31:
                await cas_retry_backoff()
        logger.warning("Lifecycle release CAS exhausted for node_id=%s key=%s", lease.node_id, key)

    async def heartbeat(self, lease: LifecycleLease) -> bool:
        key = self._key(lease.node_id)
        for attempt in range(32):
            now = time.time()
            doc, rev = await kv_get_json(self._kv, key)
            if doc is None:
                return False
            lease_data = doc.get("lease")
            if lease_data is None or lease_data.get("token") != lease.token:
                return False
            lease_data["expires_at"] = now + lease.lease_seconds
            doc["lease"] = lease_data
            if await kv_cas_json(self._kv, key, doc, rev):
                return True
            if attempt < 31:
                await cas_retry_backoff()
        logger.warning("Lifecycle heartbeat CAS exhausted for node_id=%s key=%s", lease.node_id, key)
        return False

    async def get_state(self, node_id: str) -> NodeLifecycleState | None:
        doc, _ = await kv_get_json(self._kv, self._key(node_id))
        if doc is None or doc.get("state") is None:
            return None
        return _state_from_dict(doc.get("state"))

    async def has_active_lease(self, node_id: str) -> bool:
        """True when another worker still holds an unexpired lifecycle lease."""
        doc, _ = await kv_get_json(self._kv, self._key(node_id))
        if doc is None:
            return False
        lease_data = doc.get("lease")
        if not lease_data:
            return False
        return float(lease_data.get("expires_at", 0)) > time.time()

    async def update_observed(self, node_id: str, observed: LifecycleStatus, expected_epoch: int | None = None) -> None:
        key = self._key(node_id)
        for attempt in range(32):
            now = time.time()
            doc, rev = await kv_get_json(self._kv, key)
            if doc is None:
                doc = _empty_lifecycle_doc()
            state = _state_from_dict(doc.get("state"))
            if expected_epoch is not None and state.epoch != expected_epoch:
                return
            state.observed = observed
            state.updated_at = now
            doc["state"] = _state_to_dict(state)
            if await kv_cas_json(self._kv, key, doc, rev):
                return
            if attempt < 31:
                await cas_retry_backoff()
        logger.warning("Lifecycle update_observed CAS exhausted for node_id=%s key=%s", node_id, key)

    async def clear(self, node_id: str) -> None:
        key = self._key(node_id)
        doc, rev = await kv_get_json(self._kv, key)
        if doc is None:
            return
        try:
            await self._kv.delete(key, last=rev)
        except Exception as exc:
            logger.debug("Failed to clear lifecycle key=%s: %s", key, exc)


async def clear_bridge_memory_for_node(node_id: int | str) -> None:
    store, coordinator, _ = get_bridge_memory()
    nid = str(node_id)
    if store is not None:
        await store.clear(nid)
        await store.clear_full_sync(nid)
    if coordinator is not None:
        await coordinator.clear(nid)


async def ensure_bridge_memory() -> tuple[NatsSubscriptionSyncStore | None, NatsNodeLifecycleCoordinator | None]:
    """Initialize NATS KV bridge memory for multi-uvicorn workers. Idempotent.

    Split-role / single-worker deployments keep the bridge's in-process defaults.
    """
    global _nc, _subscription_sync_kv, _lifecycle_kv, _subscription_sync_store, _lifecycle_coordinator

    if not needs_shared_bridge_memory():
        return None, None

    if _subscription_sync_store is not None and _lifecycle_coordinator is not None:
        return _subscription_sync_store, _lifecycle_coordinator

    async with _init_lock:
        if _subscription_sync_store is not None and _lifecycle_coordinator is not None:
            return _subscription_sync_store, _lifecycle_coordinator

        _nc = await create_nats_client()
        if _nc is None:
            return None, None

        js: JetStreamContext = await get_jetstream_context(_nc)
        _subscription_sync_kv = await get_or_create_kv_bucket(js, nats_settings.node_subscription_sync_kv_bucket)
        _lifecycle_kv = await get_or_create_kv_bucket(js, nats_settings.node_lifecycle_kv_bucket)
        if _subscription_sync_kv is None or _lifecycle_kv is None:
            logger.warning("Failed to create node bridge memory KV buckets")
            if _nc is not None:
                with contextlib.suppress(Exception):
                    await _nc.close()
            _nc = None
            _subscription_sync_kv = None
            _lifecycle_kv = None
            return None, None

        _subscription_sync_store = NatsSubscriptionSyncStore(_subscription_sync_kv)
        _lifecycle_coordinator = NatsNodeLifecycleCoordinator(_lifecycle_kv)
        logger.info("Node bridge NATS memory ready (worker_id=%s)", WORKER_ID)
        return _subscription_sync_store, _lifecycle_coordinator


def get_bridge_memory() -> tuple[NatsSubscriptionSyncStore | None, NatsNodeLifecycleCoordinator | None, str]:
    return _subscription_sync_store, _lifecycle_coordinator, WORKER_ID


async def shutdown_bridge_memory() -> None:
    global _nc, _subscription_sync_kv, _lifecycle_kv, _subscription_sync_store, _lifecycle_coordinator
    if _subscription_sync_store is not None:
        await _subscription_sync_store.close()
    if _nc is not None:
        await _nc.close()
    _nc = None
    _subscription_sync_kv = None
    _lifecycle_kv = None
    _subscription_sync_store = None
    _lifecycle_coordinator = None
