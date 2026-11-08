import asyncio
import io
import json
import zipfile
from base64 import b64decode
from copy import deepcopy
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import UUID, uuid4

from fastapi import status
from sqlalchemy import select

from app.db.models import Subscription
from app.utils.crypto import generate_wireguard_keypair, get_wireguard_public_key
from tests.api import TestSession, client
from tests.api.helpers import (
    auth_headers,
    create_client_template,
    create_core,
    create_group,
    create_hosts_for_inbounds,
    delete_client_template,
    delete_core,
    delete_group,
    unique_name,
)
from tests.api.sample_data import XRAY_CONFIG


def setup_groups(access_token: str, count: int = 1):
    core = create_core(access_token)
    groups = [create_group(access_token, name=unique_name(f"user_group_{idx}")) for idx in range(count)]
    return core, groups


def cleanup_groups(access_token: str, core: dict, groups: list[dict]):
    for group in groups:
        delete_group(access_token, group["id"])
    delete_core(access_token, core["id"])


def extract_wireguard_config_bodies(response) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(response.content)) as zip_file:
        config_files = [name for name in zip_file.namelist() if name.endswith(".conf")]
        return [zip_file.read(name).decode("utf-8") for name in config_files]


@dataclass(frozen=True)
class RenderableSubscription:
    """Test-only handle; the external API never returns node ID or username."""

    uuid: str
    url: str
    proxy_settings: dict


def _create_renderable_subscription(
    access_token: str, core_entitlement_headers: dict[str, str], *, group_ids: list[int]
) -> RenderableSubscription:
    sid = str(uuid4())
    headers = auth_headers(access_token)
    created = client.post("/api/subscriptions", headers=headers, json={"uuid": sid, "group_ids": group_ids})
    assert created.status_code == 201, created.text
    assert created.json()["entitlement"] == "payment_required"
    entitled = client.put(
        f"/api/subscriptions/{sid}/entitlement",
        headers=core_entitlement_headers,
        json={"entitlement": "active", "revision": 1},
    )
    assert entitled.status_code == 200, entitled.text
    link = client.get(f"/api/subscriptions/{sid}/link", headers=headers)
    assert link.status_code == 200, link.text

    async def load_internal_state():
        async with TestSession() as session:
            row = (await session.execute(select(Subscription).where(Subscription.uuid == UUID(sid)))).scalar_one()
            return dict(row.proxy_settings)

    proxy_settings = asyncio.run(load_internal_state())
    return RenderableSubscription(
        uuid=sid,
        url="/sub/" + link.json()["url"].split("/sub/", 1)[-1],
        proxy_settings=proxy_settings,
    )


def _delete_renderable_subscription(access_token: str, subscription: RenderableSubscription) -> None:
    removed = client.delete(f"/api/subscriptions/{subscription.uuid}", headers=auth_headers(access_token))
    assert removed.status_code == 204, removed.text


def test_subscription_uses_inbound_flow_for_vless_udp443(access_token, core_entitlement_headers):
    """Inbound flow should be used even when user proxy settings do not define one."""
    config = deepcopy(XRAY_CONFIG)
    inbound = next(item for item in config["inbounds"] if item["tag"] == "VLESS TCP REALITY")
    inbound["tag"] = unique_name("vless_flow_udp443")
    inbound["settings"]["flow"] = "xtls-rprx-vision-udp443"

    core = create_core(access_token, name=unique_name("flow_core"), config=config)
    group = create_group(access_token, name=unique_name("flow_group"), inbound_tags=[inbound["tag"]])
    host_response = client.post(
        "/api/host",
        headers=auth_headers(access_token),
        json={
            "remark": unique_name("flow_host"),
            "address": ["127.0.0.1"],
            "port": 443,
            "inbound_tag": inbound["tag"],
            "priority": 1,
            "sni": ["example.com"],
        },
    )
    assert host_response.status_code == status.HTTP_201_CREATED
    host = host_response.json()
    subscription = _create_renderable_subscription(
        access_token,
        core_entitlement_headers,
        group_ids=[group["id"]],
    )

    try:
        assert "flow" not in subscription.proxy_settings["vless"]

        response = client.get(f"{subscription.url}/links")
        assert response.status_code == status.HTTP_200_OK
        assert "flow=xtls-rprx-vision-udp443" in response.text
    finally:
        _delete_renderable_subscription(access_token, subscription)
        client.delete(f"/api/host/{host['id']}", headers=auth_headers(access_token))
        delete_group(access_token, group["id"])
        delete_core(access_token, core["id"])


def test_subscription_info_returns_request_ip(access_token, core_entitlement_headers):
    core, groups = setup_groups(access_token, 1)
    subscription = _create_renderable_subscription(
        access_token,
        core_entitlement_headers,
        group_ids=[groups[0]["id"]],
    )
    try:
        ip = "198.51.100.7"
        response = client.get(f"{subscription.url}/info", headers={"X-Forwarded-For": ip})
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["ip"] == ip
    finally:
        _delete_renderable_subscription(access_token, subscription)
        cleanup_groups(access_token, core, groups)


def test_subscription_applies_rule_response_headers(access_token, core_entitlement_headers):
    """Custom rule response headers should persist and keep subscription requests healthy."""
    settings_response = client.get("/api/settings", headers=auth_headers(access_token))
    assert settings_response.status_code == status.HTTP_200_OK
    original_subscription = settings_response.json()["subscription"]

    updated_subscription = {
        **original_subscription,
        "rules": [
            {
                "pattern": r"^WayLaneRuleHeaderClient$",
                "target": "links",
                "response_headers": {
                    "x-subheader": "Hello {SUBSCRIPTION_UUID}",
                    "profile-title": "Rule Profile {SUBSCRIPTION_UUID}",
                },
            },
            *original_subscription["rules"],
        ],
    }

    update_response = client.put(
        "/api/settings",
        headers=auth_headers(access_token),
        json={"subscription": updated_subscription},
    )
    assert update_response.status_code == status.HTTP_200_OK
    assert update_response.json()["subscription"]["rules"][0]["response_headers"]["x-subheader"] == "Hello {SUBSCRIPTION_UUID}"

    core, groups = setup_groups(access_token, 1)
    hosts = create_hosts_for_inbounds(access_token)
    subscription = _create_renderable_subscription(
        access_token,
        core_entitlement_headers,
        group_ids=[groups[0]["id"]],
    )

    try:
        response = client.get(
            subscription.url,
            headers={"User-Agent": "WayLaneRuleHeaderClient"},
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.text
    finally:
        restore_response = client.put(
            "/api/settings",
            headers=auth_headers(access_token),
            json={"subscription": original_subscription},
        )
        assert restore_response.status_code == status.HTTP_200_OK
        _delete_renderable_subscription(access_token, subscription)
        for host in hosts:
            client.delete(f"/api/host/{host['id']}", headers=auth_headers(access_token))
        cleanup_groups(access_token, core, groups)


def test_subscription_body_start_placement_moves_headers_into_body(
    access_token, mock_settings, core_entitlement_headers
):
    """When header_placement is body_start, subscription metadata moves into #comment lines."""
    settings_response = client.get("/api/settings", headers=auth_headers(access_token))
    assert settings_response.status_code == status.HTTP_200_OK
    original_subscription = settings_response.json()["subscription"]

    updated_subscription = {
        **original_subscription,
        "header_placement": "body_start",
    }

    update_response = client.put(
        "/api/settings",
        headers=auth_headers(access_token),
        json={"subscription": updated_subscription},
    )
    assert update_response.status_code == status.HTTP_200_OK
    assert update_response.json()["subscription"]["header_placement"] == "body_start"

    # tests/api mocks app.settings.get_settings to a static snapshot (see conftest.mock_settings),
    # so runtime subscription generation doesn't observe the PUT above; mirror it in the snapshot.
    original_header_placement = mock_settings["subscription"].get("header_placement", "header")
    mock_settings["subscription"]["header_placement"] = "body_start"

    core, groups = setup_groups(access_token, 1)
    hosts = create_hosts_for_inbounds(access_token)
    subscription = _create_renderable_subscription(
        access_token,
        core_entitlement_headers,
        group_ids=[groups[0]["id"]],
    )

    try:
        response = client.get(subscription.url, headers={"User-Agent": "GenericClient"})
        assert response.status_code == status.HTTP_200_OK
        assert "profile-update-interval" not in response.headers

        decoded = b64decode(response.text).decode()
        lines = [line for line in decoded.splitlines() if line]
        assert lines
        assert any(line.startswith("#profile-update-interval:") for line in lines)
        config_line_index = next(i for i, line in enumerate(lines) if not line.startswith("#"))
        comment_indexes = [i for i, line in enumerate(lines) if line.startswith("#")]
        assert all(i < config_line_index for i in comment_indexes)
    finally:
        mock_settings["subscription"]["header_placement"] = original_header_placement
        restore_response = client.put(
            "/api/settings",
            headers=auth_headers(access_token),
            json={"subscription": original_subscription},
        )
        assert restore_response.status_code == status.HTTP_200_OK
        _delete_renderable_subscription(access_token, subscription)
        for host in hosts:
            client.delete(f"/api/host/{host['id']}", headers=auth_headers(access_token))
        cleanup_groups(access_token, core, groups)


def test_wireguard_subscription_outputs_are_consistent(access_token, core_entitlement_headers):
    interface_private_key, _ = generate_wireguard_keypair()
    interface_public_key = get_wireguard_public_key(interface_private_key)
    interface_name = unique_name("wg_subscription")
    host_remark = "WG {SUBSCRIPTION_UUID}"
    endpoint = "198.51.100.10"

    core = create_core(
        access_token,
        name=unique_name("wireguard_subscription_core"),
        config={
            "interface_name": interface_name,
            "private_key": interface_private_key,
            "listen_port": 51820,
            "address": ["10.30.0.1/24"],
        },
        type="wg",
        fallbacks=[],
    )

    host_response = client.post(
        "/api/host",
        headers=auth_headers(access_token),
        json={
            "remark": host_remark,
            "address": [endpoint],
            "port": 51820,
            "inbound_tag": interface_name,
            "priority": 1,
            "wireguard_overrides": {
                "dns": ["1.1.1.1", "8.8.8.8"],
            },
        },
    )
    assert host_response.status_code == status.HTTP_201_CREATED
    host_id = host_response.json()["id"]

    group = create_group(access_token, name=unique_name("wg_subscription_group"), inbound_tags=[interface_name])
    subscription = _create_renderable_subscription(access_token, core_entitlement_headers, group_ids=[group["id"]])
    expected_remark = f"WG {subscription.uuid}"

    try:
        links_response = client.get(f"{subscription.url}/links")
        wireguard_response = client.get(f"{subscription.url}/wireguard")

        assert links_response.status_code == status.HTTP_200_OK
        assert wireguard_response.status_code == status.HTTP_200_OK

        link = links_response.text.strip()
        assert link.startswith("wireguard://")

        parsed = urlsplit(link)
        query = parse_qs(parsed.query)
        assert unquote(parsed.username or "") == subscription.proxy_settings["wireguard"]["private_key"]
        assert parsed.hostname == endpoint
        assert parsed.port == 51820
        assert query["publickey"] == [interface_public_key]
        assert "address" in query
        dynamic_address = query["address"][0]
        assert dynamic_address == subscription.proxy_settings["wireguard"]["peer_ips"][0]
        assert dynamic_address.endswith("/32")
        assert query["allowedips"] == ["0.0.0.0/0,::/0"]
        assert query["dns"] == ["1.1.1.1,8.8.8.8"]
        assert unquote(parsed.fragment) == expected_remark

        config_bodies = extract_wireguard_config_bodies(wireguard_response)
        assert len(config_bodies) == 1

        body = config_bodies[0]
        assert f"PrivateKey = {subscription.proxy_settings['wireguard']['private_key']}" in body
        assert f"Address = {dynamic_address}" in body
        assert f"PublicKey = {interface_public_key}" in body
        assert "AllowedIPs = 0.0.0.0/0, ::/0" in body
        assert "DNS = 1.1.1.1, 8.8.8.8" in body
        assert f"Endpoint = {endpoint}:51820" in body
    finally:
        _delete_renderable_subscription(access_token, subscription)
        delete_group(access_token, group["id"])
        client.delete(f"/api/host/{host_id}", headers=auth_headers(access_token))
        delete_core(access_token, core["id"])


def test_xray_subscription_includes_wireguard_outbound(access_token, core_entitlement_headers):
    interface_private_key, _ = generate_wireguard_keypair()
    interface_public_key = get_wireguard_public_key(interface_private_key)
    interface_name = unique_name("wg_xray_subscription")
    endpoint = "198.51.100.11"

    core = create_core(
        access_token,
        name=unique_name("wireguard_xray_core"),
        config={
            "interface_name": interface_name,
            "private_key": interface_private_key,
            "listen_port": 51820,
            "address": ["10.30.0.1/24"],
        },
        type="wg",
        fallbacks=[],
    )

    host_response = client.post(
        "/api/host",
        headers=auth_headers(access_token),
        json={
            "remark": "WG Xray {SUBSCRIPTION_UUID}",
            "address": [endpoint],
            "port": 51820,
            "inbound_tag": interface_name,
            "priority": 1,
            "wireguard_overrides": {
                "keepalive_seconds": 25,
            },
        },
    )
    assert host_response.status_code == status.HTTP_201_CREATED
    host_id = host_response.json()["id"]

    group = create_group(access_token, name=unique_name("wg_xray_group"), inbound_tags=[interface_name])
    subscription = _create_renderable_subscription(access_token, core_entitlement_headers, group_ids=[group["id"]])

    try:
        response = client.get(f"{subscription.url}/xray")
        assert response.status_code == status.HTTP_200_OK

        configs = response.json()
        assert isinstance(configs, list)
        assert configs

        wireguard_outbounds = []
        for config in configs:
            for outbound in config.get("outbounds", []):
                if outbound.get("protocol") == "wireguard":
                    wireguard_outbounds.append(outbound)

        assert len(wireguard_outbounds) == 1

        outbound = wireguard_outbounds[0]
        assert outbound["tag"] == "proxy"
        settings = outbound["settings"]
        assert settings["secretKey"] == subscription.proxy_settings["wireguard"]["private_key"]
        assert settings["address"]
        assert settings["address"][0] == subscription.proxy_settings["wireguard"]["peer_ips"][0]
        assert settings["address"][0].startswith("10.")
        assert settings["address"][0].endswith("/32")
        assert settings["domainStrategy"] == "ForceIP"
        assert "mtu" not in settings
        peers = settings["peers"]
        assert len(peers) == 1
        peer = peers[0]
        assert peer["endpoint"] == f"{endpoint}:51820"
        assert peer["publicKey"] == interface_public_key
        assert peer["allowedIPs"] == ["0.0.0.0/0", "::/0"]
        assert peer["keepAlive"] == 25
    finally:
        _delete_renderable_subscription(access_token, subscription)
        delete_group(access_token, group["id"])
        client.delete(f"/api/host/{host_id}", headers=auth_headers(access_token))
        delete_core(access_token, core["id"])


def test_xray_subscription_uses_host_specific_template_override(access_token, core_entitlement_headers):
    # Use a unique inbound tag so other tests' hosts can't affect config count.
    unique_inbound = unique_name("xray_override_inbound")
    core = create_core(
        access_token,
        config={
            "log": {"loglevel": "info"},
            "inbounds": [
                {
                    "tag": unique_inbound,
                    "listen": "0.0.0.0",
                    "port": 2087,
                    "protocol": "vmess",
                    "settings": {"clients": []},
                    "streamSettings": {"network": "ws", "wsSettings": {"path": "/yourpath"}, "security": "none"},
                }
            ],
            "outbounds": [{"protocol": "freedom", "tag": "DIRECT"}, {"protocol": "blackhole", "tag": "BLOCK"}],
        },
        fallbacks=[],
    )
    inbound = unique_inbound
    override_template = create_client_template(
        access_token,
        name=unique_name("xray_host_override_template"),
        template_type="xray_subscription",
        content=json.dumps(
            {
                "log": {"loglevel": "warning"},
                "inbounds": [{"tag": "placeholder", "protocol": "vmess", "settings": {"clients": []}}],
                "outbounds": [{"tag": "template-marker", "protocol": "freedom", "settings": {}}],
            }
        ),
    )

    host_response = client.post(
        "/api/host",
        headers=auth_headers(access_token),
        json={
            "remark": "Override Host {SUBSCRIPTION_UUID}",
            "address": ["198.51.100.50"],
            "port": 443,
            "sni": ["override-template.example.com"],
            "inbound_tag": inbound,
            "priority": 1,
            "subscription_templates": {"xray": override_template["id"]},
        },
    )
    assert host_response.status_code == status.HTTP_201_CREATED
    host_id = host_response.json()["id"]

    group = create_group(access_token, name=unique_name("xray_override_group"), inbound_tags=[inbound])
    subscription = _create_renderable_subscription(access_token, core_entitlement_headers, group_ids=[group["id"]])

    try:
        response = client.get(f"{subscription.url}/xray")
        assert response.status_code == status.HTTP_200_OK

        configs = response.json()
        assert isinstance(configs, list)
        assert len(configs) == 1

        outbounds = configs[0]["outbounds"]
        assert any(outbound["tag"] == "template-marker" for outbound in outbounds)
    finally:
        _delete_renderable_subscription(access_token, subscription)
        delete_group(access_token, group["id"])
        client.delete(f"/api/host/{host_id}", headers=auth_headers(access_token))
        delete_client_template(access_token, override_template["id"])
        delete_core(access_token, core["id"])


def test_xray_subscription_template_override_isolated_per_host(access_token, core_entitlement_headers):
    # Use a unique inbound tag so other tests' hosts can't affect config count.
    unique_inbound = unique_name("xray_isolated_inbound")
    core = create_core(
        access_token,
        config={
            "log": {"loglevel": "info"},
            "inbounds": [
                {
                    "tag": unique_inbound,
                    "listen": "0.0.0.0",
                    "port": 2087,
                    "protocol": "vmess",
                    "settings": {"clients": []},
                    "streamSettings": {"network": "ws", "wsSettings": {"path": "/yourpath"}, "security": "none"},
                }
            ],
            "outbounds": [{"protocol": "freedom", "tag": "DIRECT"}, {"protocol": "blackhole", "tag": "BLOCK"}],
        },
        fallbacks=[],
    )
    inbound = unique_inbound
    override_template = create_client_template(
        access_token,
        name=unique_name("xray_host_isolated_template"),
        template_type="xray_subscription",
        content=json.dumps(
            {
                "log": {"loglevel": "warning"},
                "inbounds": [{"tag": "placeholder", "protocol": "vmess", "settings": {"clients": []}}],
                "outbounds": [{"tag": "template-marker", "protocol": "freedom", "settings": {}}],
            }
        ),
    )

    first_host_response = client.post(
        "/api/host",
        headers=auth_headers(access_token),
        json={
            "remark": "Host With Template {SUBSCRIPTION_UUID}",
            "address": ["198.51.100.60"],
            "port": 443,
            "sni": ["host-template.example.com"],
            "inbound_tag": inbound,
            "priority": 1,
            "subscription_templates": {"xray": override_template["id"]},
        },
    )
    assert first_host_response.status_code == status.HTTP_201_CREATED
    first_host_id = first_host_response.json()["id"]

    second_host_response = client.post(
        "/api/host",
        headers=auth_headers(access_token),
        json={
            "remark": "Host Without Template {SUBSCRIPTION_UUID}",
            "address": ["198.51.100.61"],
            "port": 443,
            "sni": ["host-default.example.com"],
            "inbound_tag": inbound,
            "priority": 2,
        },
    )
    assert second_host_response.status_code == status.HTTP_201_CREATED
    second_host_id = second_host_response.json()["id"]

    group = create_group(access_token, name=unique_name("xray_isolated_group"), inbound_tags=[inbound])
    subscription = _create_renderable_subscription(access_token, core_entitlement_headers, group_ids=[group["id"]])

    try:
        response = client.get(f"{subscription.url}/xray")
        assert response.status_code == status.HTTP_200_OK

        configs = response.json()
        assert isinstance(configs, list)
        assert len(configs) == 2

        marker_count = 0
        for config in configs:
            outbounds = config.get("outbounds", [])
            if any(outbound.get("tag") == "template-marker" for outbound in outbounds):
                marker_count += 1

        assert marker_count == 1
    finally:
        _delete_renderable_subscription(access_token, subscription)
        delete_group(access_token, group["id"])
        client.delete(f"/api/host/{first_host_id}", headers=auth_headers(access_token))
        client.delete(f"/api/host/{second_host_id}", headers=auth_headers(access_token))
        delete_client_template(access_token, override_template["id"])
        delete_core(access_token, core["id"])


def test_singbox_subscription_includes_wireguard_endpoint(access_token, core_entitlement_headers):
    interface_private_key, interface_public_key = generate_wireguard_keypair()
    pre_shared_key, _ = generate_wireguard_keypair()
    interface_name = unique_name("wg_singbox_subscription")
    endpoint = "198.51.100.12"

    core = create_core(
        access_token,
        name=unique_name("wireguard_singbox_core"),
        config={
            "interface_name": interface_name,
            "private_key": interface_private_key,
            "pre_shared_key": pre_shared_key,
            "listen_port": 51820,
            "address": ["10.30.0.1/24"],
        },
        type="wg",
        fallbacks=[],
    )

    host_response = client.post(
        "/api/host",
        headers=auth_headers(access_token),
        json={
            "remark": "WG Singbox {SUBSCRIPTION_UUID}",
            "address": [endpoint],
            "port": 10001,
            "inbound_tag": interface_name,
            "priority": 1,
            "wireguard_overrides": {
                "mtu": 1408,
                "reserved": "0,0,0",
                "keepalive_seconds": 30,
            },
        },
    )
    assert host_response.status_code == status.HTTP_201_CREATED
    host_id = host_response.json()["id"]

    group = create_group(access_token, name=unique_name("wg_singbox_group"), inbound_tags=[interface_name])
    subscription = _create_renderable_subscription(access_token, core_entitlement_headers, group_ids=[group["id"]])
    expected_tag = f"WG Singbox {subscription.uuid}"

    try:
        response = client.get(f"{subscription.url}/sing_box")
        assert response.status_code == status.HTTP_200_OK

        config = response.json()
        wireguard_ep = next(
            (ep for ep in config.get("endpoints", []) if ep.get("type") == "wireguard"),
            None,
        )
        assert wireguard_ep is not None
        assert wireguard_ep["tag"] == expected_tag
        assert wireguard_ep["system"] is True
        assert wireguard_ep["name"] == "wg0"
        assert wireguard_ep["mtu"] == 1408
        assert wireguard_ep["address"]
        assert wireguard_ep["address"][0] == subscription.proxy_settings["wireguard"]["peer_ips"][0]
        assert wireguard_ep["address"][0].startswith("10.")
        assert wireguard_ep["address"][0].endswith("/32")
        assert wireguard_ep["private_key"] == subscription.proxy_settings["wireguard"]["private_key"]

        peers = wireguard_ep["peers"]
        assert len(peers) == 1
        peer = peers[0]
        assert peer["address"] == endpoint
        assert peer["port"] == 10001
        assert peer["public_key"] == interface_public_key
        assert peer["pre_shared_key"] == pre_shared_key
        assert peer["allowed_ips"] == ["0.0.0.0/0", "::/0"]
        assert peer["persistent_keepalive_interval"] == 30
        assert peer["reserved"] == [0, 0, 0]

        selector = next((outbound for outbound in config.get("outbounds", []) if outbound.get("tag") == "proxy"), None)
        if selector is not None:
            assert expected_tag in selector.get("outbounds", [])

        urltest = next(
            (outbound for outbound in config.get("outbounds", []) if outbound.get("type") == "urltest"), None
        )
        if urltest is not None:
            assert expected_tag in urltest.get("outbounds", [])
    finally:
        _delete_renderable_subscription(access_token, subscription)
        delete_group(access_token, group["id"])
        client.delete(f"/api/host/{host_id}", headers=auth_headers(access_token))
        delete_core(access_token, core["id"])


# Tests for /api/users/simple endpoint


# ---------------------------------------------------------------------------
# RBAC scope tests for user operation functions
# ---------------------------------------------------------------------------
