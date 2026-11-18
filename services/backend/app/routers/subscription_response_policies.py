"""Admin-only subscription response policies, separate from device webhooks.

The policy can change response representation or selectively deny supported
client families. It NEVER grants access denied by VPN Core.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.db.subscription_response_policy import subscription_response_policy
from app.models.admin import AdminDetails
from app.subscription.response_policy import (
    ResponsePolicy, client_language, evaluate_response, load_response_policy,
    matching_rule, matches_success_denial, notice_for_reason,
)
from .authentication import require_permission

router = APIRouter(prefix='/api/subscription-response-policies', tags=['Subscription Response Policies'])


class PolicySnapshot(BaseModel):
    revision: int = Field(ge=0)
    config: ResponsePolicy


class PolicyPreview(BaseModel):
    config: ResponsePolicy
    user_agent: str = Field(default='', max_length=512)
    accept: str = Field(default='*/*', max_length=512)
    accept_language: str = Field(default='ru', max_length=512)
    client_format: Literal['links', 'links_base64', 'clash', 'xray', 'sing_box', 'other'] = 'links_base64'
    reason: str = 'commercial_denied'


@router.get('', response_model=PolicySnapshot)
async def get_response_policies(
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission('settings', 'read')),
):
    config, revision = await load_response_policy(db)
    return PolicySnapshot(revision=revision, config=config)


@router.put('', response_model=PolicySnapshot)
async def save_response_policies(
    body: PolicySnapshot,
    db: AsyncSession = Depends(get_db),
    _: AdminDetails = Depends(require_permission('settings', 'update')),
):
    # The revision is a CAS, never a blind replacement. There is no force
    # endpoint because a second operator might have just changed denial rules.
    if body.revision == 0:
        try:
            await db.execute(insert(subscription_response_policy).values(
                id=1, revision=1, config=body.config.model_dump(mode='json'),
            ))
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise HTTPException(409, 'Response policy changed; reload before saving') from exc
        return PolicySnapshot(revision=1, config=body.config)

    result = await db.execute(
        update(subscription_response_policy)
        .where(subscription_response_policy.c.id == 1,
               subscription_response_policy.c.revision == body.revision)
        .values(revision=body.revision + 1, config=body.config.model_dump(mode='json'))
    )
    if result.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, 'Response policy changed; reload before saving')
    await db.commit()
    return PolicySnapshot(revision=body.revision + 1, config=body.config)


@router.post('/preview')
async def preview_response_policy(
    body: PolicyPreview,
    _: AdminDetails = Depends(require_permission('settings', 'read')),
):
    # Preview uses provided unsaved rules, not a live subscriber token or a
    # privileged Core call. No public subscriber identity is available here.
    reason = body.reason
    if reason != 'client_blocked':
        from app.subscription.response_policy import ResponseRule
        ResponseRule(id='preview', name='preview', reason=reason)
    rule = matching_rule(body.config, body.user_agent, reason) if body.config.enabled else None
    base_http = {'invalid_link': 404, 'unsupported_client': 406, 'rate_limited': 429}.get(reason, 403)
    response = evaluate_response(
        body.config, reason=reason, user_agent=body.user_agent,
        accept=body.accept, language=body.accept_language,
        client_format=body.client_format if body.client_format != 'other' else None,
    )
    notice = notice_for_reason(body.config, reason, client_language(body.accept_language))
    if reason == 'client_blocked' and not matches_success_denial(body.config, body.user_agent):
        decision = 'normal_subscription'
    elif response is not None:
        decision = 'notice_200'
    else:
        decision = 'http_error'
    return {
        'decision': decision,
        'status': 200 if response is not None or decision == 'normal_subscription' else base_http,
        'matched_rule_id': rule.id if rule else None,
        'language': client_language(body.accept_language),
        'preview_title': notice.title if response is not None and notice else None,
        'preview_lines': notice.lines if response is not None and notice else [],
        'wire_format': 'links_base64' if response is not None and body.client_format == 'links_base64'
                       else 'links' if response is not None else None,
        'note': 'Preview only: not a real entitlement check, VPN connection, or client refresh.',
    }
