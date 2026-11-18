from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app.db import AsyncSession, get_db
from app.models.settings import Application, ConfigFormat
from app.models.stats import SubscriptionUsageStatsList
from app.models.subscription_models import SubscriptionAccessResponse
from app.operation import OperatorType
from app.operation.subscription import SubscriptionOperation
from app.subscription.firewall import response_for_failure
from config import subscription_env_settings

from .dependencies import get_subscription_headers, get_subscription_usage_query

router = APIRouter(tags=["Subscription"], prefix=f"/{subscription_env_settings.path}")
subscription_operator = SubscriptionOperation(operator_type=OperatorType.API)


@router.get("/{token}/")
@router.get("/{token}", include_in_schema=False)
async def user_subscription(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
    user_agent: str = Header(default=""),
    accept_language: str = Header(default=""),
    headers=Depends(get_subscription_headers),
):
    """Provides a subscription link based on the user agent (Clash, V2Ray, etc.)."""
    try:
        return await subscription_operator.user_subscription(
            db,
            token=token,
            accept_header=request.headers.get("Accept", ""),
            user_agent=user_agent,
            ip=request.client.host if request.client else None,
            request_url=str(request.url),
            **headers.model_dump(),
        )
    except HTTPException as exc:
        # Legacy client adapters require HTTP 200 with informational proxy
        # links, but only for explicit HWID clients and enabled templates.
        # Use the same configured format selector as the normal response.
        # A 200 notice is only representable by links or links_base64 clients.
        from app.settings import subscription_settings
        config = await subscription_settings()
        matching = subscription_operator.detect_client_rule(user_agent, config.rules)
        fallback = await response_for_failure(
            db, exc, client_accept=request.headers.get('Accept', ''),
            hwid=headers.x_hwid, user_agent=user_agent,
            accept_language=accept_language,
            client_format=matching.target.value if matching else None,
        )
        if fallback is not None:
            return fallback
        raise


@router.head("/{token}/")
@router.head("/{token}", include_in_schema=False)
async def user_subscription_headers(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
    user_agent: str = Header(default=""),
):
    """Provides subscription headers without response body."""
    response_headers = await subscription_operator.user_subscription_headers(
        db,
        token=token,
        accept_header=request.headers.get("Accept", ""),
        user_agent=user_agent,
        request_url=str(request.url),
    )
    return Response(headers=response_headers)


@router.get("/{token}/info", response_model=SubscriptionAccessResponse)
async def get_subscription_info(
    request: Request, token: str, db: AsyncSession = Depends(get_db),
    user_agent: str = Header(default=""),
):
    """Retrieves detailed information about the user's subscription."""
    user_data, response_headers = await subscription_operator.get_subscription_info(
        db, token=token, ip=request.client.host if request.client else None,
        user_agent=user_agent,
    )
    return JSONResponse(content=user_data.model_dump(mode="json"), headers=response_headers)


@router.get("/{token}/raw")
async def user_subscription_raw(
    request: Request, token: str, db: AsyncSession = Depends(get_db),
    user_agent: str = Header(default=""),
):
    return await subscription_operator.user_subscription_raw(
        db, token=token, request_url=str(request.url), user_agent=user_agent,
    )


@router.get("/{token}/apps", response_model=list[Application])
async def get_subscription_apps(
    token: str, db: AsyncSession = Depends(get_db),
    user_agent: str = Header(default=""),
):
    """
    Get applications available for user's subscription.
    """
    return await subscription_operator.get_subscription_apps(db, token, user_agent=user_agent)


@router.get("/{token}/usage", response_model=SubscriptionUsageStatsList)
async def get_sub_user_usage(
    token: str,
    query=Depends(get_subscription_usage_query),
    db: AsyncSession = Depends(get_db),
    user_agent: str = Header(default=""),
):
    """Fetches the usage statistics for the user within a specified date range."""
    return await subscription_operator.get_subscription_usage(
        db, token=token, query=query, user_agent=user_agent,
    )


@router.get("/{token}/{client_type}")
async def user_subscription_with_client_type(
    request: Request,
    token: str,
    client_type: ConfigFormat,
    db: AsyncSession = Depends(get_db),
    user_agent: str = Header(default=""),
    accept_language: str = Header(default=""),
    headers=Depends(get_subscription_headers),
):
    """Explicit client formats share the same deny and response-policy boundary."""
    try:
        return await subscription_operator.user_subscription_with_client_type(
            db, token=token, client_type=client_type, user_agent=user_agent,
            request_url=str(request.url), **headers.model_dump(),
        )
    except HTTPException as exc:
        fallback = await response_for_failure(
            db, exc, client_accept=request.headers.get('Accept', ''),
            hwid=headers.x_hwid, user_agent=user_agent,
            accept_language=accept_language, client_format=client_type.value,
        )
        if fallback is not None:
            return fallback
        raise
