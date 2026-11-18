"""HTTP admission regression: an active subscription cannot bypass UA delivery rules."""
from fastapi import status

from tests.api import client
from tests.api.helpers import auth_headers


POLICY_ENDPOINT = '/api/subscription-response-policies'


def test_blocked_agent_cannot_bypass_through_browser_or_alternate_public_routes(
    access_token, active_subscription_factory,
):
    headers = auth_headers(access_token)
    initial_response = client.get(POLICY_ENDPOINT, headers=headers)
    assert initial_response.status_code == 200, initial_response.text
    initial = initial_response.json()
    sub = active_subscription_factory()
    url = sub['url']
    body = {
        'revision': initial['revision'],
        'config': {
            'enabled': True,
            'rules': [
                {
                    'id': 'block-test-client',
                    'name': 'Block this test UA',
                    'ua_match': 'contains',
                    'ua_value': 'BlockedTestClient',
                    'reason': 'client_blocked',
                    'mode': 'http_error',
                }
            ],
            'notices': {},
            'external_denial_codes': [],
        },
    }
    created = client.put(POLICY_ENDPOINT, headers=headers, json=body)
    assert created.status_code == status.HTTP_200_OK, created.text
    try:
        blocked = {'User-Agent': 'BlockedTestClient/1.0', 'Accept': 'text/html'}
        for suffix in ('', '/links', '/links_base64', '/xray', '/raw', '/info', '/apps', '/usage'):
            response = client.get(url + suffix, headers=blocked)
            assert response.status_code == 403, (suffix, response.status_code, response.text[:250])
            assert 'Client blocked by delivery policy' in response.text
        head = client.head(url, headers=blocked)
        assert head.status_code == 403, head.status_code
        # UA delivery rules are not commercial revocation and do not alter
        # what a different, unblocked client can request from the same token.
        normal = client.get(url + '/links', headers={'User-Agent': 'Happ/2.0'})
        assert normal.status_code == 200, normal.text[:250]
        assert 'ss://' in normal.text
    finally:
        latest = client.get(POLICY_ENDPOINT, headers=headers)
        assert latest.status_code == 200, latest.text
        restored = client.put(POLICY_ENDPOINT, headers=headers, json={
            'revision': latest.json()['revision'], 'config': initial['config'],
        })
        assert restored.status_code == 200, restored.text
        assert restored.json()['config'] == initial['config']


def test_hwid_limit_returns_notice_200_only_for_compatible_agent_without_live_servers(
    access_token, active_subscription_factory,
):
    """The second device cannot acquire live nodes; Happ still replaces its old import."""
    import asyncio
    import base64
    from urllib.parse import unquote
    from uuid import UUID
    from sqlalchemy import select
    from app.db.crud.hwid import reset_subscription_hwids
    from app.db.models import Subscription
    from tests.api import TestSession

    headers = auth_headers(access_token)
    original = client.get(POLICY_ENDPOINT, headers=headers)
    assert original.status_code == 200
    snapshot = original.json()
    enabled = client.put(POLICY_ENDPOINT, headers=headers, json={
        'revision': snapshot['revision'],
        'config': {
            'enabled': True,
            'rules': [
                {'id': 'happ-limit', 'name': 'Happ replaces rejected devices', 'ua_match': 'contains',
                 'ua_value': 'Happ', 'reason': 'device_limit', 'mode': 'notice_200'},
                {'id': 'clash-limit', 'name': 'Clash honors 403', 'ua_match': 'contains',
                 'ua_value': 'Clash', 'reason': 'device_limit', 'mode': 'http_error'},
            ],
            'notices': {'device_limit': {
                'ru': {'title': 'Лимит устройств', 'lines': ['Удалите устройство']},
                'en': {'title': 'Device limit', 'lines': ['Remove old device', 'Manage your subscription']},
            }},
            'external_denial_codes': [],
        },
    })
    assert enabled.status_code == 200, enabled.text
    subscription = active_subscription_factory()
    sid = UUID(subscription['uuid'])

    async def set_limit() -> None:
        async with TestSession() as db:
            row = (await db.execute(select(Subscription).where(Subscription.uuid == sid))).scalar_one()
            row.hwid_limit = 1
            await db.commit()

    try:
        asyncio.run(set_limit())
        first = client.get(subscription['url'], headers={'User-Agent': 'Happ/2.0', 'X-HWID': 'device-allowed'})
        assert first.status_code == 200, first.text[:220]

        denied_notice = client.get(subscription['url'], headers={
            'User-Agent': 'Happ/2.0', 'X-HWID': 'device-rejected',
            'Accept-Language': 'en-US,en;q=0.8',
        })
        assert denied_notice.status_code == 200, denied_notice.text[:240]
        assert denied_notice.headers['Cache-Control'] == 'no-store'
        notice_text = base64.b64decode(denied_notice.content).decode('utf-8')
        links = notice_text.splitlines()
        assert len(links) == 2
        assert all(link.startswith('ss://') and '@192.0.2.1:1#' in link for link in links)
        assert unquote(links[0].split('#', 1)[1]) == 'Remove old device'
        assert 'vless://' not in notice_text and 'trojan://' not in notice_text
        assert denied_notice.headers['announce'].startswith('base64:')

        denied_clash = client.get(subscription['url'], headers={
            'User-Agent': 'Clash Meta', 'X-HWID': 'device-rejected',
        })
        assert denied_clash.status_code == 403, denied_clash.text[:220]
        assert 'Device limit reached' in denied_clash.text

        devices = client.get(
            f'/api/subscriptions/{subscription["uuid"]}/devices', headers=headers
        )
        assert devices.status_code == 200, devices.text
        assert devices.json()['count'] == 1
        assert devices.json()['devices'][0]['hwid'] == 'device-allowed'
    finally:
        async def cleanup() -> None:
            async with TestSession() as db:
                await reset_subscription_hwids(db, sid)
        asyncio.run(cleanup())
        latest = client.get(POLICY_ENDPOINT, headers=headers)
        assert latest.status_code == 200, latest.text
        restored = client.put(POLICY_ENDPOINT, headers=headers, json={
            'revision': latest.json()['revision'], 'config': snapshot['config'],
        })
        assert restored.status_code == 200, restored.text
