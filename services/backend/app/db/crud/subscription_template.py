from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import NextPlan, SubscriptionTemplate, template_group_association
from app.models.subscription_template import (
    SubscriptionTemplateCreate,
    SubscriptionTemplateListQuery,
    SubscriptionTemplateModify,
    SubscriptionTemplateSimpleListQuery,
    SubscriptionTemplateSimpleSortField,
    SubscriptionTemplateSimpleSortOption,
)

from .admin_role import remove_ids_from_role_access
from .group import get_groups_by_ids


def _build_user_template_simple_sort_clause(sort_option: SubscriptionTemplateSimpleSortOption):
    field_map = {
        SubscriptionTemplateSimpleSortField.id: SubscriptionTemplate.id,
        SubscriptionTemplateSimpleSortField.template_name: SubscriptionTemplate.name,
    }
    column = field_map[sort_option.field]
    return column.desc() if sort_option.value.startswith("-") else column.asc()


async def load_subscription_template_attrs(template: SubscriptionTemplate):
    await template.awaitable_attrs.groups


async def create_subscription_template(
    db: AsyncSession, subscription_template: SubscriptionTemplateCreate
) -> SubscriptionTemplate:
    """
    Creates a new user template in the database.

    Args:
        db (AsyncSession): Database session.
        user_template (SubscriptionTemplateCreate): The user template creation data.

    Returns:
        SubscriptionTemplate: The created user template object.
    """

    db_subscription_template = SubscriptionTemplate(
        name=subscription_template.name,
        data_limit=subscription_template.data_limit,
        hwid_limit=subscription_template.hwid_limit,
        expire_duration=subscription_template.expire_duration,
        groups=await get_groups_by_ids(db, subscription_template.group_ids, load_users=False, load_inbounds=False)
        if subscription_template.group_ids
        else None,
        extra_settings=subscription_template.extra_settings.dict() if subscription_template.extra_settings else None,
        status=subscription_template.status,
        reset_usages=subscription_template.reset_usages,
        on_hold_timeout=subscription_template.on_hold_timeout,
        is_disabled=subscription_template.is_disabled,
        data_limit_reset_strategy=subscription_template.data_limit_reset_strategy,
    )

    db.add(db_subscription_template)
    await db.commit()
    await db.refresh(db_subscription_template)
    await load_subscription_template_attrs(db_subscription_template)
    return db_subscription_template


async def modify_subscription_template(
    db: AsyncSession, db_subscription_template: SubscriptionTemplate, modified_user_template: SubscriptionTemplateModify
) -> SubscriptionTemplate:
    """
    Updates a user template's details.

    Args:
        db (AsyncSession): Database session.
        db_user_template (SubscriptionTemplate): The user template object to be updated.
        modified_user_template (SubscriptionTemplateModify): The modified user template data.

    Returns:
        SubscriptionTemplate: The updated user template object.
    """
    if modified_user_template.name is not None:
        db_subscription_template.name = modified_user_template.name
    if modified_user_template.data_limit is not None:
        db_subscription_template.data_limit = modified_user_template.data_limit
    if "hwid_limit" in modified_user_template.model_fields_set:
        db_subscription_template.hwid_limit = modified_user_template.hwid_limit
    if modified_user_template.expire_duration is not None:
        db_subscription_template.expire_duration = modified_user_template.expire_duration
    if modified_user_template.group_ids:
        db_subscription_template.groups = await get_groups_by_ids(
            db, modified_user_template.group_ids, load_users=False, load_inbounds=False
        )
    if modified_user_template.extra_settings is not None:
        db_subscription_template.extra_settings = modified_user_template.extra_settings.dict()
    if modified_user_template.status is not None:
        db_subscription_template.status = modified_user_template.status
    if modified_user_template.reset_usages is not None:
        db_subscription_template.reset_usages = modified_user_template.reset_usages
    if modified_user_template.on_hold_timeout is not None:
        db_subscription_template.on_hold_timeout = modified_user_template.on_hold_timeout
    if modified_user_template.is_disabled is not None:
        db_subscription_template.is_disabled = modified_user_template.is_disabled
    if "data_limit_reset_strategy" in modified_user_template.model_fields_set:
        db_subscription_template.data_limit_reset_strategy = modified_user_template.data_limit_reset_strategy

    await db.commit()
    await db.refresh(db_subscription_template)
    await load_subscription_template_attrs(db_subscription_template)
    return db_subscription_template


async def remove_subscription_template(db: AsyncSession, db_subscription_template: SubscriptionTemplate):
    """
    Removes a user template from the database.

    Args:
        db (AsyncSession): Database session.
        db_user_template (SubscriptionTemplate): The user template object to be removed.
    """
    await remove_ids_from_role_access(db, "allowed_template_ids", [db_subscription_template.id])
    await db.delete(db_subscription_template)
    await db.commit()


async def get_subscription_template(db: AsyncSession, subscription_template_id: int) -> SubscriptionTemplate:
    """
    Retrieves a user template by its ID.

    Args:
        db (AsyncSession): Database session.
        user_template_id (int): The ID of the user template.

    Returns:
        SubscriptionTemplate: The user template object.
    """
    subscription_template = (
        (await db.execute(select(SubscriptionTemplate).where(SubscriptionTemplate.id == subscription_template_id)))
        .unique()
        .scalar_one_or_none()
    )
    if subscription_template:
        await load_subscription_template_attrs(subscription_template)
    return subscription_template


async def get_subscription_templates(
    db: AsyncSession, query: SubscriptionTemplateListQuery
) -> list[SubscriptionTemplate]:
    """
    Retrieves a list of user templates with optional pagination.

    Args:
        db (AsyncSession): Database session.
        offset (Union[int, None]): The number of records to skip (for pagination).
        limit (Union[int, None]): The maximum number of records to return.

    Returns:
        List[SubscriptionTemplate]: A list of user template objects.
    """
    stmt = (
        select(SubscriptionTemplate)
        .options(selectinload(SubscriptionTemplate.groups))
        .order_by(SubscriptionTemplate.id.asc())
    )
    # ids=[] (e.g. none of the requested ids are in the admin's allowlist) must match nothing, not everything.
    if query.ids is not None:
        stmt = stmt.where(SubscriptionTemplate.id.in_(query.ids))
    if query.offset:
        stmt = stmt.offset(query.offset)
    if query.limit:
        stmt = stmt.limit(query.limit)

    subscription_templates = (await db.execute(stmt)).scalars().all()
    return subscription_templates


async def get_subscription_templates_simple(
    db: AsyncSession,
    query: SubscriptionTemplateSimpleListQuery,
) -> tuple[list[tuple[int, str]], int]:
    """
    Retrieves lightweight user template data with only id and name.

    Args:
        db: Database session.
        offset: Number of records to skip.
        limit: Number of records to retrieve.
        search: Search term for template name.
        sort: Sort options.
        skip_pagination: If True, ignore offset/limit and return all records (max 1,000).

    Returns:
        Tuple of (list of (id, name) tuples, total_count).
    """
    stmt = select(SubscriptionTemplate.id, SubscriptionTemplate.name)

    if query.ids is not None:
        stmt = stmt.where(SubscriptionTemplate.id.in_(query.ids))
    if query.search:
        search_value = query.search.strip()
        if search_value:
            stmt = stmt.where(SubscriptionTemplate.name.ilike(f"%{search_value}%"))

    if query.sort:
        stmt = stmt.order_by(*[_build_user_template_simple_sort_clause(sort_option) for sort_option in query.sort])
    else:
        stmt = stmt.order_by(SubscriptionTemplate.id.asc())

    # Get count BEFORE pagination (always)
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar()

    # Apply pagination or safety limit
    if not query.all:
        if query.offset:
            stmt = stmt.offset(query.offset)
        if query.limit:
            stmt = stmt.limit(query.limit)
    else:
        stmt = stmt.limit(10000)  # Safety limit when all=true

    result = await db.execute(stmt)
    rows = result.all()

    return rows, total


async def remove_subscription_templates(db: AsyncSession, template_ids: list[int]) -> None:
    """
    Removes multiple user templates from the database by ID.

    Args:
        db (AsyncSession): Database session.
        template_ids (list[int]): List of template IDs to remove.
    """
    if not template_ids:
        return

    await db.execute(
        delete(template_group_association).where(
            template_group_association.c.subscription_template_id.in_(template_ids)
        )
    )
    # A template next plan keeps its limits in the template, so it cannot outlive it: delete it like the
    # single delete's ORM cascade does (with the reference nulled it would renew to unlimited, never expiring).
    await db.execute(delete(NextPlan).where(NextPlan.subscription_template_id.in_(template_ids)))
    await db.execute(delete(SubscriptionTemplate).where(SubscriptionTemplate.id.in_(template_ids)))
    await remove_ids_from_role_access(db, "allowed_template_ids", template_ids)
    await db.commit()
