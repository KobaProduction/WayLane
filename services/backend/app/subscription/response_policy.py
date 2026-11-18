"""Subscription response policies: deny/represent only; never grant VPN entitlement.

A synthetic HTTP 200 response contains inert, non-routable notice links. It
cannot include VPN endpoints. Only explicitly configured link subscriptions
can use it. Core authorization remains the sole commercial authority.
"""
from __future__ import annotations

import base64
import re
from typing import Literal
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.subscription_response_policy import subscription_response_policy

Locale = Literal['ru', 'en', 'fa', 'zh']
Mode = Literal['http_error', 'notice_200']
Match = Literal['contains', 'prefix', 'exact']
KNOWN_REASONS = frozenset({
    'client_blocked', 'commercial_denied', 'invalid_link', 'device_limit',
    'hwid_required', 'unsupported_client', 'rate_limited', 'access_denied',
    'payment_required', 'subscription_expired', 'traffic_limit',
    'external_device_limit', 'external_access_denied', 'bot_unavailable',
})
EXTERNAL_CODE = re.compile(r'^[A-Z][A-Z0-9_]{0,63}$')
LOCALES: tuple[Locale, ...] = ('ru', 'en', 'fa', 'zh')
# TEST-NET-1 (RFC 5737) is reserved for documentation, never a real
# WayLane endpoint. Use a syntactically valid IP so compatible clients can
# import and replace stale configs instead of rejecting a malformed host.
# Compatibility with each real client remains a separate DEV acceptance test.
INERT_LINK_PREFIX = 'ss://Y2hhY2hhMjAtaWV0Zi1wb2x5MTMwNTp2NlB3M2FUbEctM0VJX05hV3M0akZVc0FZWFY3UzM2dQ==@192.0.2.1:1#'


class Notice(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    lines: list[str] = Field(min_length=1, max_length=8)

    @model_validator(mode='after')
    def check_lines(self):
        if not self.title.strip() or any(not item.strip() or len(item) > 256 for item in self.lines):
            raise ValueError('Each notice title/line must contain 1–256 nonblank characters')
        return self


class ResponseRule(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r'^[a-zA-Z0-9_-]+$')
    name: str = Field(min_length=1, max_length=80)
    ua_match: Match = 'contains'
    ua_value: str = Field(default='', max_length=128)
    reason: str = Field(min_length=1, max_length=80)
    mode: Mode = 'http_error'

    @field_validator('reason')
    @classmethod
    def known_reason(cls, value: str) -> str:
        if value not in KNOWN_REASONS and not (
            value.startswith('external:') and EXTERNAL_CODE.fullmatch(value.removeprefix('external:'))
        ):
            raise ValueError('Unsupported policy reason')
        return value

    @model_validator(mode='after')
    def validate_name(self):
        if not self.name.strip():
            raise ValueError('Rule name must not be blank')
        return self


class ResponsePolicy(BaseModel):
    enabled: bool = False
    rules: list[ResponseRule] = Field(default_factory=list, max_length=100)
    # Keyed by reason and client language. Missing language falls back to ru/en.
    notices: dict[str, dict[Locale, Notice]] = Field(default_factory=dict)
    # Explicit codes from a trusted, configured device-check receiver which
    # must deny, not fallback to an undocumented implicit allow/deny policy.
    external_denial_codes: list[str] = Field(default_factory=list, max_length=64)

    @field_validator('external_denial_codes')
    @classmethod
    def codes(cls, items: list[str]) -> list[str]:
        if len(set(items)) != len(items) or any(not EXTERNAL_CODE.fullmatch(item) for item in items):
            raise ValueError('External codes must be unique uppercase identifiers')
        if any(item in {'ACCESS_GRANTED', 'BOT_ERROR', 'ALLOW', 'OK'} for item in items):
            raise ValueError('Success or unavailable codes cannot become denial rules')
        return items

    @model_validator(mode='after')
    def validate_rules(self):
        if len({rule.id for rule in self.rules}) != len(self.rules):
            raise ValueError('Duplicate policy rule id')
        for reason, languages in self.notices.items():
            ResponseRule(id='validate', name='validate', reason=reason)
            if not languages:
                raise ValueError('Notice needs at least one language')
        for rule in self.rules:
            if rule.mode == 'notice_200' and rule.reason not in self.notices:
                raise ValueError(f'Notice template is required for reason: {rule.reason}')
            if rule.reason.startswith('external:') and rule.reason.removeprefix('external:') not in self.external_denial_codes:
                raise ValueError(f'External denial code not enabled: {rule.reason}')
        return self


def client_language(header: str) -> Locale:
    """Simple weighted Accept-Language selection, defaulting to Russian."""
    accepted: list[tuple[float, int, Locale]] = []
    for position, entry in enumerate(header[:512].split(',')):
        bits = entry.strip().split(';')
        primary = bits[0].strip().lower().split('-', 1)[0]
        if primary not in LOCALES:
            continue
        try:
            quality = next((float(bit.strip()[2:]) for bit in bits[1:] if bit.strip().startswith('q=')), 1.0)
        except ValueError:
            continue
        if 0 < quality <= 1:
            accepted.append((quality, -position, primary))
    return max(accepted)[2] if accepted else 'ru'


def matching_rule(policy: ResponsePolicy, user_agent: str, reason: str) -> ResponseRule | None:
    agent = user_agent[:512].casefold()
    for rule in policy.rules:
        if rule.reason != reason:
            continue
        match = rule.ua_value.casefold()
        if not match or (rule.ua_match == 'contains' and match in agent) or (
            rule.ua_match == 'prefix' and agent.startswith(match)
        ) or (rule.ua_match == 'exact' and agent == match):
            return rule
    return None


def failure_reason(exc: HTTPException) -> str | None:
    # Never substitute 200 for unavailable Core authorization.
    if exc.status_code == 503:
        return None
    if exc.status_code == 404:
        return 'invalid_link'
    if exc.status_code == 406:
        return 'unsupported_client'
    if exc.status_code == 429:
        return 'rate_limited'
    if exc.status_code != 403:
        return None
    detail = str(exc.detail)
    lower = detail.casefold()
    if detail.startswith('External device code:'):
        code = detail.partition(':')[2].strip()
        return f'external:{code}' if EXTERNAL_CODE.fullmatch(code) else None
    if 'subscription payment required' in lower:
        return 'payment_required'
    if 'subscription expired' in lower:
        return 'subscription_expired'
    if 'subscription traffic limit' in lower:
        return 'traffic_limit'
    if 'external device limit' in lower:
        return 'external_device_limit'
    if 'device limit' in lower:
        return 'device_limit'
    if 'hwid header' in lower:
        return 'hwid_required'
    if 'external access denied' in lower or 'device-check webhook' in lower:
        return 'external_access_denied'
    if 'client blocked by delivery policy' in lower:
        return 'client_blocked'
    if 'subscription access denied' in lower:
        return 'commercial_denied'
    return 'access_denied'


def notice_for_reason(policy: ResponsePolicy, reason: str, language: Locale) -> Notice | None:
    languages = policy.notices.get(reason) or {}
    return languages.get(language) or languages.get('ru') or languages.get('en')


def render_inert_notice(notice: Notice, *, as_base64: bool) -> Response:
    """Syntactically compatible notice-only SS links, never usable servers."""
    text = '\n'.join(INERT_LINK_PREFIX + quote(line, safe='') for line in notice.lines)
    body = base64.b64encode(text.encode('utf-8')) if as_base64 else text.encode('utf-8')
    title = base64.b64encode(notice.title.encode('utf-8')).decode('ascii').rstrip('=')
    return Response(
        content=body, status_code=200, media_type='text/plain; charset=utf-8',
        headers={'announce': f'base64:{title}', 'Cache-Control': 'no-store',
                 'X-Content-Type-Options': 'nosniff'},
    )


async def load_response_policy(db: AsyncSession) -> tuple[ResponsePolicy, int]:
    row = (await db.execute(select(subscription_response_policy).where(subscription_response_policy.c.id == 1))).mappings().one_or_none()
    if not row:
        return ResponsePolicy(), 0
    return ResponsePolicy.model_validate(row['config']), row['revision']


def evaluate_response(
    policy: ResponsePolicy, *, reason: str, user_agent: str, accept: str,
    language: str, client_format: str | None,
) -> Response | None:
    if not policy.enabled:
        return None
    rule = matching_rule(policy, user_agent, reason)
    if rule is None or rule.mode == 'http_error':
        return None
    accepted = accept.lower()
    if any(value in accepted for value in (
        'text/html', 'application/json', 'application/yaml', 'text/yaml',
        'application/zip', 'application/octet-stream',
    )):
        return None
    # Fail closed for JSON/YAML/ZIP/unknown and ambiguous format requests.
    if client_format not in ('links', 'links_base64'):
        return None
    notice = notice_for_reason(policy, reason, client_language(language))
    if notice is None:
        return None
    return render_inert_notice(notice, as_base64=client_format == 'links_base64')


def matches_success_denial(policy: ResponsePolicy, user_agent: str) -> bool:
    """A deliberate UA-specific deny rule; never changes commercial allow semantics."""
    return policy.enabled and matching_rule(policy, user_agent, 'client_blocked') is not None
