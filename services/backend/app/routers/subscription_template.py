from fastapi import APIRouter, Depends, status

from app.db import AsyncSession, get_db
from app.models.admin import AdminDetails
from app.models.subscription_template import (
    BulkSubscriptionTemplatesActionResponse,
    BulkSubscriptionTemplateSelection,
    RemoveSubscriptionTemplatesResponse,
    SubscriptionTemplateCreate,
    SubscriptionTemplateModify,
    SubscriptionTemplateResponse,
    SubscriptionTemplatesSimpleResponse,
)
from app.operation import OperatorType
from app.operation.subscription_template import SubscriptionTemplateOperation
from app.utils import responses

from .authentication import require_permission
from .dependencies import get_subscription_template_list_query, get_subscription_template_simple_list_query

router = APIRouter(tags=["Subscription Template"], prefix="/api/subscription_template")
template_operator = SubscriptionTemplateOperation(OperatorType.API)


@router.post(
    "",
    response_model=SubscriptionTemplateResponse,
    status_code=status.HTTP_201_CREATED,
    responses={403: responses._403},
)
async def create_subscription_template(
    new_subscription_template: SubscriptionTemplateCreate,
    db: AsyncSession = Depends(get_db),
    admin: AdminDetails = Depends(require_permission("templates", "create")),
):
    """
    Create a new subscription template

    - **name** can be up to 64 characters
    - **data_limit** must be in bytes and larger or equal to 0
    - **expire_duration** must be in seconds and larger or equat to 0
    - **group_ids** list of group ids
    """
    return await template_operator.create_subscription_template(db, new_subscription_template, admin)


@router.get("/{template_id}", response_model=SubscriptionTemplateResponse)
async def get_subscription_template(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    admin: AdminDetails = Depends(require_permission("templates", "read")),
):
    """Get Subscription Template information with id"""
    return await template_operator._get_template_with_access(db, template_id, admin)


@router.put("/{template_id}", response_model=SubscriptionTemplateResponse, responses={403: responses._403})
async def modify_subscription_template(
    template_id: int,
    modify_subscription_template: SubscriptionTemplateModify,
    db: AsyncSession = Depends(get_db),
    admin: AdminDetails = Depends(require_permission("templates", "update")),
):
    """
    Modify Subscription Template

    - **name** can be up to 64 characters
    - **data_limit** must be in bytes and larger or equal to 0
    - **expire_duration** must be in seconds and larger or equat to 0
    - **group_ids** list of group ids
    """
    return await template_operator.modify_subscription_template(db, template_id, modify_subscription_template, admin)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT, responses={403: responses._403})
async def remove_subscription_template(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    admin: AdminDetails = Depends(require_permission("templates", "delete")),
):
    """Remove a Subscription Template by its ID"""
    await template_operator.remove_subscription_template(db, template_id, admin)
    return {}


@router.get("s", response_model=list[SubscriptionTemplateResponse])
async def get_subscription_templates(
    query=Depends(get_subscription_template_list_query),
    db: AsyncSession = Depends(get_db),
    admin: AdminDetails = Depends(require_permission("templates", "read")),
):
    """Get a list of Subscription Templates with optional pagination"""
    return await template_operator.get_subscription_templates(db, query, admin)


@router.get(
    "s/simple",
    response_model=SubscriptionTemplatesSimpleResponse,
    summary="Get lightweight subscription template list",
    description="Returns only id and name for subscription templates. Optimized for dropdowns and autocomplete.",
)
async def get_subscription_templates_simple(
    query=Depends(get_subscription_template_simple_list_query),
    db: AsyncSession = Depends(get_db),
    admin: AdminDetails = Depends(require_permission("templates", "read_simple")),
):
    """Get lightweight subscription template list with only id and name"""
    return await template_operator.get_subscription_templates_simple(db=db, query=query, admin=admin)


@router.post(
    "s/bulk/delete",
    response_model=RemoveSubscriptionTemplatesResponse,
    responses={400: responses._400, 403: responses._403, 404: responses._404},
)
async def bulk_delete_subscription_templates(
    bulk_templates: BulkSubscriptionTemplateSelection,
    db: AsyncSession = Depends(get_db),
    admin: AdminDetails = Depends(require_permission("templates", "delete")),
):
    """Delete selected subscription templates by ID."""
    return await template_operator.bulk_remove_subscription_templates(db, bulk_templates, admin)


@router.post(
    "s/bulk/disable",
    response_model=BulkSubscriptionTemplatesActionResponse,
    responses={400: responses._400, 403: responses._403, 404: responses._404},
)
async def bulk_disable_subscription_templates(
    bulk_templates: BulkSubscriptionTemplateSelection,
    db: AsyncSession = Depends(get_db),
    admin: AdminDetails = Depends(require_permission("templates", "update")),
):
    """Disable selected subscription templates by ID."""
    return await template_operator.bulk_set_subscription_templates_disabled(db, bulk_templates, admin, is_disabled=True)


@router.post(
    "s/bulk/enable",
    response_model=BulkSubscriptionTemplatesActionResponse,
    responses={400: responses._400, 403: responses._403, 404: responses._404},
)
async def bulk_enable_subscription_templates(
    bulk_templates: BulkSubscriptionTemplateSelection,
    db: AsyncSession = Depends(get_db),
    admin: AdminDetails = Depends(require_permission("templates", "update")),
):
    """Enable selected subscription templates by ID."""
    return await template_operator.bulk_set_subscription_templates_disabled(
        db, bulk_templates, admin, is_disabled=False
    )
