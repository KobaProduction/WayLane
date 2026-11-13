import asyncio

from sqlalchemy import select

from app import notification
from app.db import AsyncSession
from app.db.crud.group import (
    create_group,
    get_group,
    get_group_subscription_uuids,
    get_groups_by_ids,
    get_groups_simple,
    load_group_attrs,
    modify_group,
    remove_group,
    remove_groups,
)
from app.db.crud.subscription import get_subscriptions
from app.db.crud.wireguard import sync_users_allocations
from app.db.models import Admin, Group as DBGroup, Subscription, subscriptions_groups_association
from app.models.group import (
    BulkGroupsActionResponse,
    BulkGroupSelection,
    Group,
    GroupCreate,
    GroupListQuery,
    GroupModify,
    GroupResponse,
    GroupSimple,
    GroupSimpleListQuery,
    GroupsResponse,
    GroupsSimpleResponse,
    RemoveGroupsResponse,
)
from app.models.subscription_models import SubscriptionListQuery
from app.operation import BaseOperation
from app.operation.permissions import apply_group_access
from app.subscription_delivery import sync_subscriptions
from app.utils.logger import get_logger

logger = get_logger("group-operation")


class GroupOperation(BaseOperation):
    async def _reject_core_managed_group_deletion(self, db: AsyncSession, group_ids: list[int]) -> None:
        """A provider-side group deletion must not silently erase Core's desired group assignment."""
        if not group_ids:
            return
        # Lock in a deterministic order to serialize concurrent local group
        # deletions before checking the impacted subscription bindings.
        await db.execute(
            select(DBGroup.id)
            .where(DBGroup.id.in_(sorted(set(group_ids))))
            .order_by(DBGroup.id)
            .with_for_update(of=DBGroup)
        )
        managed_subscription = (
            await db.execute(
                select(Subscription.uuid)
                .join(
                    subscriptions_groups_association,
                    subscriptions_groups_association.c.subscription_uuid == Subscription.uuid,
                )
                .where(
                    subscriptions_groups_association.c.groups_id.in_(group_ids),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if managed_subscription is not None:
            await self.raise_error("Cannot delete group assigned to a Core-managed subscription", 409, db=db)

    async def _get_group_with_access(self, db: AsyncSession, group_id: int, admin: Admin) -> Group:
        """Fetch a group, returning 404 if it doesn't exist or is outside the admin's allowed set."""
        allowed = apply_group_access(admin, [group_id])
        # If allowed is an empty list, the id was filtered out → not accessible
        if allowed is not None and group_id not in allowed:
            await self.raise_error("Group not found", 404)
        db_group = await self.get_validated_group(db, group_id)
        return db_group

    async def _sync_users_allocations(self, db: AsyncSession, users) -> None:
        try:
            await sync_users_allocations(db, users)
        except ValueError as exc:  # WireGuard subnet exhausted
            await self.raise_error(message=str(exc), code=400, db=db)

    @staticmethod
    async def _get_users_for_sync(db: AsyncSession, uuids: list) -> list[Subscription]:
        """Load users for a node sync, 10k usernames per query (asyncpg caps a statement at 32767 bind params)."""
        users: list[Subscription] = []
        for start in range(0, len(uuids), 10_000):
            users += await get_subscriptions(
                db,
                query=SubscriptionListQuery(uuids=uuids[start : start + 10_000]),
                load_admin_role=True,
                load_usage_logs=False,
                load_group_inbounds=True,
            )
        return users

    async def create_group(self, db: AsyncSession, new_group: GroupCreate, admin: Admin) -> Group:
        await self.check_inbound_tags(new_group.inbound_tags)
        db_group = await create_group(db, new_group)

        group = GroupResponse.model_validate(db_group)

        asyncio.create_task(notification.create_group(group, admin.username))

        logger.info(f'Group "{group.name}" created by admin "{admin.username}"')
        return group

    async def get_all_groups(self, db: AsyncSession, query: GroupListQuery, admin: Admin) -> GroupsResponse:
        query.ids = apply_group_access(admin, query.ids)
        db_groups, count = await get_group(db, query)
        return GroupsResponse(groups=db_groups, total=count)

    async def get_groups_simple(
        self,
        db: AsyncSession,
        query: GroupSimpleListQuery,
        admin: Admin,
    ) -> GroupsSimpleResponse:
        """Get lightweight group list with only id and name"""
        query.ids = apply_group_access(admin, query.ids)
        rows, total = await get_groups_simple(db=db, query=query)
        groups = [GroupSimple(id=row[0], name=row[1]) for row in rows]
        return GroupsSimpleResponse(groups=groups, total=total)

    async def modify_group(self, db: AsyncSession, group_id: int, modified_group: GroupModify, admin: Admin) -> Group:
        db_group = await self._get_group_with_access(db, group_id, admin)
        if modified_group.inbound_tags is not None:
            await self.check_inbound_tags(modified_group.inbound_tags)
        db_group = await modify_group(db, db_group, modified_group)

        users = await get_subscriptions(
            db,
            query=SubscriptionListQuery(group_ids=[db_group.id]),
            load_admin_role=True,
            load_usage_logs=False,
            load_group_inbounds=True,
        )
        await self._sync_users_allocations(db, users)
        await db.commit()
        await sync_subscriptions(users)

        group = GroupResponse.model_validate(db_group)

        asyncio.create_task(notification.modify_group(group, admin.username))

        logger.info(f'Group "{group.name}" modified by admin "{admin.username}"')
        return group

    async def remove_group(self, db: AsyncSession, group_id: int, admin: Admin) -> None:
        db_group = await self._get_group_with_access(db, group_id, admin)
        await self._reject_core_managed_group_deletion(db, [db_group.id])

        uuid_list = await get_group_subscription_uuids(db, [db_group.id])

        await remove_group(db, db_group)

        users = await self._get_users_for_sync(db, uuid_list)
        await self._sync_users_allocations(db, users)
        await db.commit()
        await sync_subscriptions(users)

        logger.info(f'Group "{db_group.name}" deleted by admin "{admin.username}"')

        asyncio.create_task(notification.remove_group(db_group.id, admin.username))

    async def bulk_remove_groups_by_id(
        self, db: AsyncSession, bulk_groups: BulkGroupSelection, admin: Admin
    ) -> RemoveGroupsResponse:
        """Remove multiple groups by ID"""
        requested_ids = list(bulk_groups.ids)
        allowed_ids = apply_group_access(admin, requested_ids)
        # Fetch all allowed groups in one query
        db_groups = await get_groups_by_ids(db, allowed_ids or [], load_users=False, load_inbounds=False)
        # Verify all requested ids were found and accessible
        found_ids = {g.id for g in db_groups}
        for gid in requested_ids:
            if gid not in found_ids:
                await self.raise_error("Group not found", 404)

        group_ids = [g.id for g in db_groups]
        await self._reject_core_managed_group_deletion(db, group_ids)
        group_names = [g.name for g in db_groups]
        affected_uuids = await get_group_subscription_uuids(db, group_ids)

        await remove_groups(db, group_ids)

        if affected_uuids:
            users = await self._get_users_for_sync(db, affected_uuids)
            await self._sync_users_allocations(db, users)
            await db.commit()
            await sync_subscriptions(users)

        for name, group_id in zip(group_names, group_ids):
            logger.info(f'Group "{name}" deleted by admin "{admin.username}"')
            asyncio.create_task(notification.remove_group(group_id, admin.username))

        return RemoveGroupsResponse(groups=group_names, count=len(db_groups))

    @staticmethod
    def _build_bulk_action_response(groups: list[Group]) -> BulkGroupsActionResponse:
        names = [group.name for group in groups]
        return BulkGroupsActionResponse(groups=names, count=len(names))

    async def bulk_set_groups_disabled(
        self,
        db: AsyncSession,
        bulk_groups: BulkGroupSelection,
        admin: Admin,
        *,
        is_disabled: bool,
    ) -> BulkGroupsActionResponse:
        requested_ids = list(bulk_groups.ids)
        allowed_ids = apply_group_access(admin, requested_ids)
        db_groups = await get_groups_by_ids(db, allowed_ids or [], load_users=False, load_inbounds=False)
        found_ids = {g.id for g in db_groups}
        for gid in requested_ids:
            if gid not in found_ids:
                await self.raise_error("Group not found", 404)

        groups_to_update = [db_group for db_group in db_groups if db_group.is_disabled != is_disabled]

        for db_group in groups_to_update:
            db_group.is_disabled = is_disabled

        # Committed below together with the WireGuard allocations, so an exhausted subnet changes nothing.
        await db.flush()

        for db_group in groups_to_update:
            await db.refresh(db_group)
            await load_group_attrs(db, db_group)

        if groups_to_update:
            users = await get_subscriptions(
                db,
                query=SubscriptionListQuery(group_ids=[group.id for group in groups_to_update]),
                load_admin_role=True,
                load_usage_logs=False,
                load_group_inbounds=True,
            )
            await self._sync_users_allocations(db, users)
            await db.commit()
            await sync_subscriptions(users)

        for db_group in groups_to_update:
            group = GroupResponse.model_validate(db_group)
            asyncio.create_task(notification.modify_group(group, admin.username))
            logger.info(
                f'Group "{db_group.name}" bulk {"disabled" if is_disabled else "enabled"} by admin "{admin.username}"'
            )

        return self._build_bulk_action_response(groups_to_update)
