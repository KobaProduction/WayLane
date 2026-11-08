import asyncio

from sqlalchemy.exc import IntegrityError

from app import notification
from app.db import AsyncSession
from app.db.crud.subscription_template import (
    create_subscription_template,
    get_subscription_templates,
    get_subscription_templates_simple,
    load_subscription_template_attrs,
    modify_subscription_template,
    remove_subscription_template,
    remove_subscription_templates,
)
from app.db.models import Admin, SubscriptionTemplate
from app.models.subscription_template import (
    BulkSubscriptionTemplatesActionResponse,
    BulkSubscriptionTemplateSelection,
    RemoveSubscriptionTemplatesResponse,
    SubscriptionTemplateCreate,
    SubscriptionTemplateListQuery,
    SubscriptionTemplateModify,
    SubscriptionTemplateResponse,
    SubscriptionTemplateSimple,
    SubscriptionTemplateSimpleListQuery,
    SubscriptionTemplatesSimpleResponse,
)
from app.operation import BaseOperation
from app.operation.permissions import apply_template_access
from app.utils.logger import get_logger

logger = get_logger("user-template-operation")


class SubscriptionTemplateOperation(BaseOperation):
    async def _get_template_with_access(self, db: AsyncSession, template_id: int, admin: Admin) -> SubscriptionTemplate:
        """Fetch a user template, returning 404 if outside the admin's allowed set."""
        allowed = apply_template_access(admin, [template_id])
        if allowed is not None and template_id not in allowed:
            await self.raise_error("Subscription Template not found", 404)
        return await self.get_validated_subscription_template(db, template_id)

    async def create_subscription_template(
        self, db: AsyncSession, new_subscription_template: SubscriptionTemplateCreate, admin: Admin
    ) -> SubscriptionTemplateResponse:
        await self.validate_all_groups(db, new_subscription_template, admin)
        try:
            db_subscription_template = await create_subscription_template(db, new_subscription_template)
        except IntegrityError:
            await self.raise_error("Template by this name already exists", 409, db=db)

        subscription_template = SubscriptionTemplateResponse.model_validate(db_subscription_template)

        asyncio.create_task(notification.create_subscription_template(subscription_template, admin.username))

        logger.info(f'Subscription template "{db_subscription_template.name}" created by admin "{admin.username}"')
        return db_subscription_template

    async def modify_subscription_template(
        self, db: AsyncSession, template_id: int, modified_user_template: SubscriptionTemplateModify, admin: Admin
    ) -> SubscriptionTemplateResponse:
        db_subscription_template = await self._get_template_with_access(db, template_id, admin)
        if modified_user_template.group_ids is not None:
            await self.validate_all_groups(
                db,
                modified_user_template,
                admin,
                existing_group_ids=set(db_subscription_template.group_ids or []),
            )
        try:
            db_subscription_template = await modify_subscription_template(db, db_subscription_template, modified_user_template)
        except IntegrityError:
            await self.raise_error("Template by this name already exists", 409, db=db)

        subscription_template = SubscriptionTemplateResponse.model_validate(db_subscription_template)

        asyncio.create_task(notification.modify_subscription_template(subscription_template, admin.username))

        logger.info(f'Subscription template "{db_subscription_template.name}" modified by admin "{admin.username}"')
        return db_subscription_template

    async def remove_subscription_template(self, db: AsyncSession, template_id: int, admin: Admin) -> None:
        db_subscription_template = await self._get_template_with_access(db, template_id, admin)
        await remove_subscription_template(db, db_subscription_template)
        logger.info(f'Subscription template "{db_subscription_template.name}" deleted by admin "{admin.username}"')

        asyncio.create_task(notification.remove_subscription_template(db_subscription_template.name, admin.username))

    async def get_subscription_templates(
        self, db: AsyncSession, query: SubscriptionTemplateListQuery, admin: Admin
    ) -> list[SubscriptionTemplateResponse]:
        query.ids = apply_template_access(admin, query.ids)
        return await get_subscription_templates(db, query)

    async def get_subscription_templates_simple(
        self, db: AsyncSession, query: SubscriptionTemplateSimpleListQuery, admin: Admin
    ) -> SubscriptionTemplatesSimpleResponse:
        """Get lightweight user template list with only id and name"""
        query.ids = apply_template_access(admin, query.ids)
        rows, total = await get_subscription_templates_simple(db=db, query=query)
        templates = [SubscriptionTemplateSimple(id=row[0], name=row[1]) for row in rows]
        return SubscriptionTemplatesSimpleResponse(templates=templates, total=total)

    async def bulk_remove_subscription_templates(
        self, db: AsyncSession, bulk_templates: BulkSubscriptionTemplateSelection, admin: Admin
    ) -> RemoveSubscriptionTemplatesResponse:
        """Remove multiple user templates by ID"""
        requested_ids = list(bulk_templates.ids)
        allowed_ids = apply_template_access(admin, requested_ids)
        # Fetch all in one query
        db_templates = await get_subscription_templates(db, SubscriptionTemplateListQuery(ids=allowed_ids or []))
        found_ids = {t.id for t in db_templates}
        for tid in requested_ids:
            if tid not in found_ids:
                await self.raise_error("Subscription Template not found", 404)

        template_ids = [t.id for t in db_templates]
        template_names = [t.name for t in db_templates]

        # Batch delete using CRUD function
        await remove_subscription_templates(db, template_ids)

        # Log and notify
        for name in template_names:
            logger.info(f'Subscription template "{name}" deleted by admin "{admin.username}"')
            asyncio.create_task(notification.remove_subscription_template(name, admin.username))

        return RemoveSubscriptionTemplatesResponse(templates=template_names, count=len(db_templates))

    @staticmethod
    def _build_bulk_action_response(templates: list) -> BulkSubscriptionTemplatesActionResponse:
        names = [template.name for template in templates]
        return BulkSubscriptionTemplatesActionResponse(templates=names, count=len(names))

    async def bulk_set_subscription_templates_disabled(
        self,
        db: AsyncSession,
        bulk_templates: BulkSubscriptionTemplateSelection,
        admin: Admin,
        *,
        is_disabled: bool,
    ) -> BulkSubscriptionTemplatesActionResponse:
        requested_ids = list(bulk_templates.ids)
        allowed_ids = apply_template_access(admin, requested_ids)
        db_templates = await get_subscription_templates(db, SubscriptionTemplateListQuery(ids=allowed_ids or []))
        found_ids = {t.id for t in db_templates}
        for tid in requested_ids:
            if tid not in found_ids:
                await self.raise_error("Subscription Template not found", 404)

        templates_to_update = [db_template for db_template in db_templates if db_template.is_disabled != is_disabled]

        for db_template in templates_to_update:
            db_template.is_disabled = is_disabled

        await db.commit()

        for db_template in templates_to_update:
            await db.refresh(db_template)
            await load_subscription_template_attrs(db_template)
            subscription_template = SubscriptionTemplateResponse.model_validate(db_template)
            asyncio.create_task(notification.modify_subscription_template(subscription_template, admin.username))
            logger.info(
                f'Subscription template "{db_template.name}" bulk {"disabled" if is_disabled else "enabled"} by admin "{admin.username}"'
            )

        return self._build_bulk_action_response(templates_to_update)
