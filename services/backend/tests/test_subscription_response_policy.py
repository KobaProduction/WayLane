"""Policy tests: no grant, inert 200 compatibility response, reasons and concurrency."""
import base64
from urllib.parse import unquote, urlsplit
from ipaddress import ip_address

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.routers.subscription_response_policies import (
    PolicyPreview, PolicySnapshot, get_response_policies,
    preview_response_policy, save_response_policies,
)
from app.subscription.firewall import response_for_failure
from app.subscription.response_policy import (
    Notice, ResponsePolicy, ResponseRule, client_language, evaluate_response,
    failure_reason, matching_rule, matches_success_denial,
)


def policy() -> ResponsePolicy:
    return ResponsePolicy(enabled=True, rules=[
        ResponseRule(id='legacy', name='Legacy Happ notice', ua_value='happ', reason='device_limit', mode='notice_200'),
        ResponseRule(id='modern', name='Modern clients use HTTP', ua_value='clash', reason='device_limit', mode='http_error'),
        ResponseRule(id='block-client', name='Disallow UA', ua_value='forbidden-client', reason='client_blocked', mode='notice_200'),
    ], notices={
        'device_limit': {
            'ru': Notice(title='Лимит устройств', lines=['Лимит достигнут', 'Удалите старое устройство']),
            'en': Notice(title='Device limit', lines=['Remove an old device']),
        },
        'client_blocked': {'ru': Notice(title='Нет доступа', lines=['Это приложение запрещено'])},
    })


def test_policy_user_agents_and_locales_are_explicit_and_ordered():
    p = policy()
    assert matching_rule(p, 'Happ/2.0 Android', 'device_limit').id == 'legacy'
    assert matching_rule(p, 'Clash Meta 2', 'device_limit').id == 'modern'
    assert matching_rule(p, 'SomeOtherApp', 'device_limit') is None
    assert matches_success_denial(p, 'FORBIDDEN-CLIENT/1.0')
    assert not matches_success_denial(p, 'Happ/2.0')
    assert client_language('en-US,en;q=0.8,ru;q=0.2') == 'en'
    assert client_language('fa-IR;q=0.7,en;q=0.9') == 'en'
    assert client_language('zh-CN,ru;q=0.8') == 'zh'
    assert client_language('zz,fr') == 'ru'
    with pytest.raises(ValueError):
        ResponsePolicy(enabled=True, rules=[ResponseRule(id='x', name='x', reason='invalid_link', mode='notice_200')])
    with pytest.raises(ValueError):
        ResponsePolicy(enabled=True, external_denial_codes=['ACCESS_GRANTED'])


def test_compatibility_notice_200_has_only_inert_endpoints_and_zero_server_addresses():
    p = policy()
    response = evaluate_response(
        p, reason='device_limit', user_agent='Happ/2.0', accept='*/*',
        client_format='links_base64', language='ru',
    )
    assert response and response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'
    links = base64.b64decode(response.body).decode().splitlines()
    assert len(links) == 2
    assert all(link.startswith('ss://') and '@192.0.2.1:1#' in link for link in links)
    assert unquote(links[0].split('#', 1)[1]) == 'Лимит достигнут'
    assert all(urlsplit(link).hostname == '192.0.2.1' and urlsplit(link).port == 1
               and not ip_address(urlsplit(link).hostname).is_global for link in links)
    plain = evaluate_response(
        p, reason='device_limit', user_agent='happ/2.0', accept='text/plain',
        client_format='links', language='en-US',
    )
    assert plain and plain.body.decode().startswith('ss://')
    assert 'Remove%20an%20old%20device' in plain.body.decode()
    for incompatible_format, accept in [('clash', '*/*'), ('xray', '*/*'), ('links', 'text/html'),
                                        ('links_base64', 'application/json'),
                                        ('links', 'application/yaml'), (None, '*/*')]:
        assert evaluate_response(p, reason='device_limit', user_agent='Happ', accept=accept,
                                 client_format=incompatible_format, language='ru') is None
    assert evaluate_response(p, reason='device_limit', user_agent='Clash Meta', accept='*/*',
                             client_format='links_base64', language='ru') is None


def test_failure_reasons_are_distinct_but_unknown_commercial_causes_not_invented():
    cases = [
        (HTTPException(403, 'Subscription access denied'), 'commercial_denied'),
        (HTTPException(403, 'Subscription payment required'), 'payment_required'),
        (HTTPException(403, 'Subscription expired'), 'subscription_expired'),
        (HTTPException(403, 'Subscription traffic limit reached'), 'traffic_limit'),
        (HTTPException(403, 'Device limit reached'), 'device_limit'),
        (HTTPException(403, 'External Device limit reached'), 'external_device_limit'),
        (HTTPException(403, 'External Access denied by device-check webhook'), 'external_access_denied'),
        (HTTPException(403, 'External device code: PAYMENT_REQUIRED'), 'external:PAYMENT_REQUIRED'),
        (HTTPException(403, 'Client blocked by delivery policy'), 'client_blocked'),
        (HTTPException(429, 'Slow down'), 'rate_limited'),
        (HTTPException(503, 'Commercial subscription authorization unavailable'), None),
    ]
    for error, expected in cases:
        assert failure_reason(error) == expected


@pytest.mark.asyncio
async def test_policy_admin_persistence_cas_and_preview_do_not_grant_access(tmp_path):
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'policies.db'))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with factory() as session:
            original = await get_response_policies(session, None)
            assert original.revision == 0 and not original.config.enabled
            configured = await save_response_policies(PolicySnapshot(revision=0, config=policy()), session, None)
            assert configured.revision == 1
            assert (await get_response_policies(session, None)).config.rules[0].id == 'legacy'
            preview = await preview_response_policy(PolicyPreview(config=policy(), reason='device_limit',
                user_agent='Happ/2.0', client_format='links_base64', accept_language='en-US'), None)
            assert preview['decision'] == 'notice_200' and preview['preview_title'] == 'Device limit'
            modern = await preview_response_policy(PolicyPreview(config=policy(), reason='device_limit',
                user_agent='Clash', client_format='links_base64'), None)
            assert modern['decision'] == 'http_error' and modern['status'] == 403
            with pytest.raises(HTTPException) as stale:
                await save_response_policies(PolicySnapshot(revision=0, config=policy()), session, None)
            assert stale.value.status_code == 409
            configured.config.enabled = False
            saved = await save_response_policies(PolicySnapshot(revision=1, config=configured.config), session, None)
            assert saved.revision == 2
            failure = HTTPException(403, 'Device limit reached')
            assert await response_for_failure(session, failure, client_accept='*/*', hwid='HWID',
                                               user_agent='Happ', client_format='links_base64') is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_policy_runtime_denial_mode_and_commercial_503_fails_closed(tmp_path):
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'runtime.db'))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with factory() as session:
            await save_response_policies(PolicySnapshot(revision=0, config=policy()), session, None)
            actual = await response_for_failure(session, HTTPException(403,'Device limit reached'),
                client_accept='*/*',hwid=None,user_agent='Happ/2.0',client_format='links_base64',accept_language='ru')
            assert actual is not None and actual.status_code == 200
            assert await response_for_failure(session, HTTPException(503,'Commercial subscription authorization unavailable'),
                client_accept='*/*',hwid='device',user_agent='Happ/2.0',client_format='links_base64',accept_language='ru') is None
            assert await response_for_failure(session, HTTPException(403,'Device limit reached'),
                client_accept='text/html',hwid='device',user_agent='Happ/2.0',client_format='links_base64') is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_saved_user_agent_rule_blocks_validated_client_before_hwid_registration(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.models.settings import ConfigFormat, SubRule, Subscription as SubSettings
    from app.operation import OperatorType
    from app.operation import subscription as subscription_module
    from app.operation.subscription import SubscriptionOperation
    import asyncio

    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'valid-client.db'))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with factory() as db:
            p = policy()
            await save_response_policies(PolicySnapshot(revision=0, config=p), db, None)
            fake = SimpleNamespace(admin=None)
            async def validated(*args, **kwargs):
                return fake
            async def no_hwid(*args, **kwargs):
                raise AssertionError('Blocked client must not reach HWID admission')
            async def settings():
                return SubSettings(rules=[SubRule(pattern='.*', target=ConfigFormat.links_base64)])
            monkeypatch.setattr(SubscriptionOperation, 'get_validated_sub', validated)
            monkeypatch.setattr(SubscriptionOperation, 'validated_subscription', validated)
            monkeypatch.setattr(SubscriptionOperation, 'validate_and_register_hwid', no_hwid)
            monkeypatch.setattr(subscription_module, 'subscription_settings', settings)
            operation = SubscriptionOperation(operator_type=OperatorType.API)
            with pytest.raises(HTTPException) as blocked:
                await operation.user_subscription(db, 'synthetic-only', user_agent='FORBIDDEN-CLIENT/1.0')
            assert blocked.value.status_code == 403
            assert failure_reason(blocked.value) == 'client_blocked'
            response = await response_for_failure(db, blocked.value, client_accept='*/*', hwid=None,
                user_agent='FORBIDDEN-CLIENT/1.0', client_format='links_base64')
            assert response is not None and response.status_code == 200
            assert b'vless://' not in base64.b64decode(response.body)
            # The same validation applies to explicit client format links.
            with pytest.raises(HTTPException) as blocked_explicit:
                await operation.user_subscription_with_client_type(
                    db, 'synthetic-only', ConfigFormat.links, user_agent='FORBIDDEN-CLIENT/1.0'
                )
            assert blocked_explicit.value.status_code == 403
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_external_status_mappings_are_explicit_and_not_commercial_grants(tmp_path, monkeypatch):
    import asyncio
    import json
    from app.subscription.device_webhook import check_legacy_device_access
    from app.db.subscription_firewall import subscription_firewall_config
    from sqlalchemy import insert

    statuses = ['PAYMENT_REQUIRED', 'BOT_ERROR', 'ACCESS_GRANTED', 'LIMIT_DEVICES', 'ACCESS_DENIED']
    async def receiver(reader, writer):
        head = await reader.readuntil(b'\r\n\r\n')
        size = next(int(line.partition(b':')[2].strip()) for line in head.split(b'\r\n')
                    if line.lower().startswith(b'content-length:'))
        await reader.readexactly(size)
        body = json.dumps({'status': statuses.pop(0)}).encode()
        writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '
                     + str(len(body)).encode() + b'\r\nConnection: close\r\n\r\n' + body)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(receiver, '127.0.0.1', 0)
    monkeypatch.setenv('WAYLANE_WEBHOOK_ALLOW_LOCAL_TEST', '1')
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'external.db'))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            await db.execute(insert(subscription_firewall_config).values(
                id=1, enabled=False, scenarios={},
                device_check_url='http://127.0.0.1:' + str(server.sockets[0].getsockname()[1]) + '/check',
            ))
            p = ResponsePolicy(enabled=True, external_denial_codes=['PAYMENT_REQUIRED'], rules=[
                ResponseRule(id='payment', name='Payment from external device service',
                             reason='external:PAYMENT_REQUIRED', ua_value='Happ', mode='notice_200')
            ], notices={'external:PAYMENT_REQUIRED': {
                'en': Notice(title='Payment required', lines=['Please pay']),
            }})
            await save_response_policies(PolicySnapshot(revision=0, config=p), db, None)
            kwargs = dict(token='synthetic-no-real-subscription',ip='127.0.0.1',hwid='synthetic-device',
                          user_agent='Happ/2.0',device_os=None,os_version=None,device_model=None)
            with pytest.raises(HTTPException) as special:
                await check_legacy_device_access(db, **kwargs)
            assert special.value.status_code == 403
            assert failure_reason(special.value) == 'external:PAYMENT_REQUIRED'
            assert await response_for_failure(db, special.value, client_accept='*/*', hwid='synthetic-device',
                user_agent='Happ', accept_language='en-US', client_format='links_base64') is not None
            await check_legacy_device_access(db, **kwargs)  # BOT_ERROR: not used as entitlement.
            await check_legacy_device_access(db, **kwargs)  # ACCESS_GRANTED: not a separate grant.
            with pytest.raises(HTTPException) as limited:
                await check_legacy_device_access(db, **kwargs)
            assert failure_reason(limited.value) == 'external_device_limit'
            with pytest.raises(HTTPException) as denied:
                await check_legacy_device_access(db, **kwargs)
            assert failure_reason(denied.value) == 'external_access_denied'
            assert statuses == []
    finally:
        server.close()
        await server.wait_closed()
        await engine.dispose()
