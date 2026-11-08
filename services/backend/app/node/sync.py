import asyncio
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_object_session

from app.db import GetDB
from app.db.models import Admin, AdminRole, AdminStatus, Subscription
from app.models.subscription_models import SubscriptionNotificationResponse
from app.nats.node_rpc import encode_node_command, node_nats_client
from app.nats.proto_utils import serialize_proto_message, serialize_proto_messages
from app.node import node_manager
from app.node.bridge import register_refresh_handler
from app.node.subscription import (
    _serialize_subscription_for_node,
    core_subscriptions,
    removal_subscriptions,
    serialize_subscription,
    serialize_subscriptions_for_node,
)
from app.utils.logger import get_logger
from config import nats_settings, runtime_settings
from waylane_node_bridge.common.service_pb2 import Subscription as ProtoSubscription

logger = get_logger("node-sync")


def _chunk_serialized_subscriptions_for_nats(users: list[dict]) -> list[list[dict]]:
    if not users:
        return []

    max_payload_bytes = max(1024, nats_settings.node_command_max_payload_bytes)
    max_batch_size = max(1, nats_settings.node_update_users_batch_size)
    chunks: list[list[dict]] = []
    current: list[dict] = []
    envelope_bytes = len(encode_node_command("update_users", {"subscriptions": []}))
    current_bytes = envelope_bytes

    for user in users:
        # Reuse the wire encoder so JSON escaping and the command envelope are counted exactly.
        single_user_bytes = len(encode_node_command("update_users", {"subscriptions": [user]}))
        user_bytes = single_user_bytes - envelope_bytes
        candidate_bytes = current_bytes + user_bytes + (1 if current else 0)  # JSON array comma
        if current and (len(current) >= max_batch_size or candidate_bytes > max_payload_bytes):
            chunks.append(current)
            current = []
            candidate_bytes = single_user_bytes

        current.append(user)
        current_bytes = candidate_bytes

        if single_user_bytes > max_payload_bytes:
            logger.warning("Single serialized subscription update exceeds configured NATS node command payload limit")

    if current:
        chunks.append(current)

    return chunks


def _loaded_admin_sync_blocked(admin: Admin) -> bool | None:
    state = getattr(admin, "__dict__", {})
    status = state.get("status")
    if status is None:
        return None
    if status not in (AdminStatus.limited, AdminStatus.disabled):
        return False

    role = state.get("role")
    if role is None:
        return None

    if status == AdminStatus.limited:
        return bool(role.disconnect_users_when_limited)
    return bool(role.disconnect_users_when_disabled)


async def _subscription_sync_blocked(db_subscription: Subscription) -> bool:
    if not db_subscription.admin_id:
        return False

    admin = getattr(db_subscription, "__dict__", {}).get("admin")
    if admin is not None:
        loaded_result = _loaded_admin_sync_blocked(admin)
        if loaded_result is not None:
            return loaded_result

    session = async_object_session(db_subscription)
    if session is None:
        return False

    stmt = (
        select(Admin.status, AdminRole.disconnect_users_when_limited, AdminRole.disconnect_users_when_disabled)
        .select_from(Admin)
        .join(AdminRole, AdminRole.id == Admin.role_id)
        .where(Admin.id == db_subscription.admin_id)
    )
    row = (await session.execute(stmt)).one_or_none()
    return bool(row and ((row[0] == AdminStatus.limited and row[1]) or (row[0] == AdminStatus.disabled and row[2])))


async def _blocked_admin_ids_for_subscriptions(users: list[Subscription]) -> set[int]:
    admin_ids = {user.admin_id for user in users if user.admin_id is not None}
    if not admin_ids:
        return set()

    loaded_admins_by_id = {
        user.admin_id: admin
        for user in users
        if user.admin_id is not None and (admin := getattr(user, "__dict__", {}).get("admin")) is not None
    }
    if set(loaded_admins_by_id) == admin_ids:
        loaded_results = {
            admin.id: blocked
            for admin in loaded_admins_by_id.values()
            if (blocked := _loaded_admin_sync_blocked(admin)) is not None
        }
        if set(loaded_results) == admin_ids:
            return {admin_id for admin_id, blocked in loaded_results.items() if blocked}

    session = next((async_object_session(user) for user in users if async_object_session(user) is not None), None)
    if session is None:
        return set()

    stmt = (
        select(Admin.id)
        .join(AdminRole, AdminRole.id == Admin.role_id)
        .where(
            Admin.id.in_(admin_ids),
            (
                ((Admin.status == AdminStatus.limited) & (AdminRole.disconnect_users_when_limited.is_(True)))
                | ((Admin.status == AdminStatus.disabled) & (AdminRole.disconnect_users_when_disabled.is_(True)))
            ),
        )
    )
    return set((await session.execute(stmt)).scalars().all())


if runtime_settings.role.runs_node:

    async def _dispatch_subscription_update(proto_subscription):
        await node_manager.update_user(proto_subscription)

    async def _dispatch_subscriptions_update(proto_subscriptions, *, await_dispatch: bool = False):
        """Dispatch a batch locally, optionally waiting for node queue acceptance."""
        if await_dispatch:
            await node_manager.update_users(proto_subscriptions, await_dispatch=True)
        else:
            await node_manager.update_users(proto_subscriptions)

else:
    # Preserve the existing internal NATS action names until API and worker
    # deployment are atomically switched; they are not product identifiers.

    async def _dispatch_subscription_update(proto_subscription):
        user_dict = serialize_proto_message(proto_subscription)
        await node_nats_client.publish("update_user", {"user": user_dict})

    async def _dispatch_subscriptions_update(proto_subscriptions, *, await_dispatch: bool = False):
        """Publish bounded batches to the node worker before returning."""
        users_dicts = serialize_proto_messages(proto_subscriptions)
        for users_chunk in _chunk_serialized_subscriptions_for_nats(users_dicts):
            await node_nats_client.publish("update_users", {"subscriptions": users_chunk})


class CurrentStateReader:
    """Coalesces concurrent current-state reads into indexed queries, one per gather window.

    A query starts only after every request it serves was registered, so a
    delivery is never answered from a read that began before its own claim (and
    therefore before the enqueue and commit that triggered it). Requests that
    arrive while a query is running are served by the next query; the reader
    keeps draining until nothing is pending. There is no cache. Each query
    carries at most ``max_ids_per_query`` ids.
    """

    def __init__(self, gather_seconds: float = 0.005, max_ids_per_query: int = 400, query_timeout: float = 20.0):
        self._gather_seconds = gather_seconds
        self._max_ids = max(1, max_ids_per_query)
        # A hung database call must fail its own batch, not block every later
        # request behind it; waiters see a TimeoutError and their delivery is requeued.
        self._query_timeout = query_timeout
        self._pending: dict[UUID, list[asyncio.Future]] = {}
        self._task: asyncio.Task | None = None

    async def get(self, subscription_uuids: list[UUID]) -> dict[UUID, ProtoSubscription | None]:
        """Node payloads keyed by subscription UUID; ``None`` means no persisted subscription."""
        if not subscription_uuids:
            return {}
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        for subscription_uuid in subscription_uuids:
            self._pending.setdefault(subscription_uuid, []).append(future)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="node-current-state-reader")
        result = await future
        return {subscription_uuid: result.get(subscription_uuid) for subscription_uuid in subscription_uuids}

    async def _query(self, subscription_uuids: list[UUID]) -> dict[UUID, ProtoSubscription | None]:
        result: dict[UUID, ProtoSubscription | None] = dict.fromkeys(subscription_uuids)
        async with asyncio.timeout(self._query_timeout), GetDB() as db:
            for start in range(0, len(subscription_uuids), self._max_ids):
                chunk = subscription_uuids[start : start + self._max_ids]
                for proto in await core_subscriptions(db, subscription_uuids=chunk):
                    result[UUID(proto.subscription_uuid)] = proto
                # Revoked subscriptions become removal payloads retaining stored credentials.
                if missing := [subscription_uuid for subscription_uuid in chunk if result[subscription_uuid] is None]:
                    for proto in await removal_subscriptions(db, missing):
                        result[UUID(proto.subscription_uuid)] = proto
        return result

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._gather_seconds)
            # Snapshot the requests registered so far; later ones wait for the
            # next query. Nothing awaits between an empty check and returning,
            # so a request registered after this point always finds a task to
            # start or a loop that will pick it up.
            pending, self._pending = self._pending, {}
            if not pending:
                return
            futures = {future for waiters in pending.values() for future in waiters}
            try:
                result = await self._query(list(pending))
            except asyncio.CancelledError:
                for future in futures:
                    if not future.done():
                        future.cancel()
                raise
            except Exception as exc:
                for future in futures:
                    if not future.done():
                        future.set_exception(exc)
                continue
            for future in futures:
                if not future.done():
                    future.set_result(result)


_current_state = CurrentStateReader()


async def refresh_node_subscriptions(node_id: str, subscription_uuids: list[str]) -> list[ProtoSubscription]:
    """Resolve fresh Node subscription payloads immediately before queued delivery.

    Only canonical UUIDv4 identifiers belong to WayLane-owned subscriptions.
    Unknown UUIDs produce an empty-credential removal so the existing queue
    can retain credentials needed to revoke a previously installed account.
    Non-UUID external account keys are left to their queue payloads.
    """
    ids: list[UUID] = []
    for value in dict.fromkeys(subscription_uuids):
        try:
            candidate = UUID(value)
        except TypeError, ValueError, AttributeError:
            continue
        if candidate.version == 4 and str(candidate) == value:
            ids.append(candidate)
    if not ids:
        return []
    state = await _current_state.get(ids)
    return [
        proto if proto is not None else ProtoSubscription(subscription_uuid=str(subscription_uuid))
        for subscription_uuid, proto in state.items()
    ]


register_refresh_handler(refresh_node_subscriptions)


async def sync_subscription_access(db_subscription: Subscription) -> None:
    """Dispatch the current commercial admission state before acknowledging it.

    The node delivery queue re-reads committed DB state, so older deliveries
    cannot re-install credentials after a newer payment suspension.
    """
    if await _subscription_sync_blocked(db_subscription):
        return
    await _dispatch_subscription_update(await serialize_subscription(db_subscription))


async def sync_subscription(db_subscription: Subscription) -> None:
    if await _subscription_sync_blocked(db_subscription):
        return

    proto_subscription = await serialize_subscription(db_subscription)
    asyncio.create_task(_dispatch_subscription_update(proto_subscription))


async def remove_subscription(user: SubscriptionNotificationResponse) -> None:
    proto_subscription = _serialize_subscription_for_node(user.uuid, user.proxy_settings.dict())
    asyncio.create_task(_dispatch_subscription_update(proto_subscription))


async def remove_subscription_awaited(user: SubscriptionNotificationResponse) -> None:
    """Await node removal dispatch before committing a subscription deletion."""
    proto_subscription = _serialize_subscription_for_node(user.uuid, user.proxy_settings.dict())
    await _dispatch_subscription_update(proto_subscription)


async def remove_subscriptions(users: list[Subscription], *, await_dispatch: bool = False) -> None:
    """Batch-remove subscriptions, optionally awaiting node dispatch."""
    if not users:
        return
    proto_subscriptions = [_serialize_subscription_for_node(u.uuid, u.proxy_settings) for u in users]
    if await_dispatch:
        await _dispatch_subscriptions_update(proto_subscriptions, await_dispatch=True)
    else:
        asyncio.create_task(_dispatch_subscriptions_update(proto_subscriptions))


async def sync_subscriptions(users: list[Subscription], *, await_dispatch: bool = False) -> None:
    """Sync eligible subscriptions, optionally awaiting node dispatch."""
    blocked_admin_ids = await _blocked_admin_ids_for_subscriptions(users)
    filtered = [user for user in users if user.admin_id not in blocked_admin_ids]
    proto_subscriptions = await serialize_subscriptions_for_node(filtered)
    if await_dispatch:
        await _dispatch_subscriptions_update(proto_subscriptions, await_dispatch=True)
    else:
        asyncio.create_task(_dispatch_subscriptions_update(proto_subscriptions))
