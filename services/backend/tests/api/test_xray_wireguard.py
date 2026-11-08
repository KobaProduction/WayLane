import asyncio
import io
import zipfile
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from sqlalchemy import select

from app.db.models import Subscription
from app.utils.crypto import generate_wireguard_keypair
from tests.api import TestSession, client
from tests.api.helpers import (
    auth_headers,
    create_core,
    create_group,
    delete_core,
    delete_group,
    get_inbound_details,
    unique_name,
)


def wireguard_config(core_type, tag, secret, addresses):
    if core_type == "wg":
        return {"interface_name": tag, "private_key": secret, "listen_port": 51820, "address": addresses}
    return {
        "inbounds": [
            {
                "tag": tag,
                "protocol": "wireguard",
                "port": 51821,
                "settings": {"secretKey": secret, "address": addresses, "peers": [], "mtu": 1380},
            }
        ],
        "outbounds": [{"tag": "direct", "protocol": "freedom"}],
    }


def test_xray_wireguard_lifecycle_preserves_kernel_allocations(access_token, active_subscription_factory):
    """Native WireGuard IP allocation stays stable when another Xray/WG core changes."""
    headers = auth_headers(access_token)
    secret, public = generate_wireguard_keypair()
    kernel_tag, xray_tag = unique_name("kernel_wg"), unique_name("xray_wg")
    kernel = create_core(access_token, type="wg", config=wireguard_config("wg", kernel_tag, secret, ["10.91.0.1/24"]))
    kernel_group = create_group(access_token, inbound_tags=[kernel_tag])
    kernel_subscription = active_subscription_factory(group_ids=[kernel_group["id"]])
    xray = create_core(access_token, config=wireguard_config("xray", xray_tag, secret, ["10.92.0.1/24", "fd92::1/64"]))
    xray_group = create_group(access_token, inbound_tags=[xray_tag])
    subscription = active_subscription_factory(group_ids=[kernel_group["id"], xray_group["id"]])

    async def _wg_settings(subscription_uuid: str) -> dict:
        async with TestSession() as db:
            sub = (await db.scalars(select(Subscription).where(Subscription.uuid == UUID(subscription_uuid)))).one()
            return dict(sub.proxy_settings.get("wireguard") or {})

    kernel_settings = asyncio.run(_wg_settings(kernel_subscription["uuid"]))
    host = client.post(
        "/api/host",
        headers=headers,
        json={
            "remark": "Xray WG",
            "address": ["198.51.100.20"],
            "inbound_tag": xray_tag,
            "priority": 1,
        },
    )
    try:
        assert host.status_code == 201, host.text
        wg = asyncio.run(_wg_settings(subscription["uuid"]))
        assert wg["peer_ips"] == ["10.91.0.3/32", "10.92.0.2/32", "fd92::2/128"]
        detail = next(item for item in get_inbound_details(access_token) if item["tag"] == xray_tag)
        assert detail["protocol"] == "wireguard"
        assert detail["network"] == "udp"

        response = client.get(f"{subscription['url']}/links")
        assert response.status_code == 200
        link = urlsplit(response.text.strip())
        assert link.port == 51821
        query = parse_qs(link.query)
        assert query["publickey"] == [public]
        assert query["address"] == ["10.92.0.2/32,fd92::2/128"]
        assert query["mtu"] == ["1380"]
        response = client.get(f"{subscription['url']}/wireguard")
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            body = archive.read(archive.namelist()[0]).decode()
        assert "Address = 10.92.0.2/32, fd92::2/128" in body
        assert f"PublicKey = {public}" in body
        assert "Endpoint = 198.51.100.20:51821" in body

        # Moving the Xray subnet must not mutate kernel WireGuard allocation.
        config = deepcopy(xray["config"])
        config["inbounds"][0]["settings"]["address"] = ["10.93.0.1/24"]
        payload = {
            "name": xray["name"],
            "type": "xray",
            "config": config,
            "exclude_inbound_tags": [],
            "fallbacks_inbound_tags": [],
        }
        response = client.put(f"/api/core/{xray['id']}", headers=headers, json=payload, params={"restart_nodes": False})
        assert response.status_code == 200, response.text
        updated = asyncio.run(_wg_settings(subscription["uuid"]))
        assert updated["peer_ips"] == ["10.91.0.3/32", "10.93.0.2/32"]
        assert updated["private_key"] == wg["private_key"]

        # Excluding/restoring one inbound never consumes the kernel peer again.
        payload["exclude_inbound_tags"] = [xray_tag]
        response = client.put(f"/api/core/{xray['id']}", headers=headers, json=payload, params={"restart_nodes": False})
        assert response.status_code == 200, response.text
        assert asyncio.run(_wg_settings(subscription["uuid"]))["peer_ips"] == ["10.91.0.3/32"]
        payload["exclude_inbound_tags"] = []
        response = client.put(f"/api/core/{xray['id']}", headers=headers, json=payload, params={"restart_nodes": False})
        assert response.status_code == 200, response.text
        delete_core(access_token, xray["id"])
        xray = None
        assert asyncio.run(_wg_settings(subscription["uuid"]))["peer_ips"] == ["10.91.0.3/32"]
        assert asyncio.run(_wg_settings(kernel_subscription["uuid"])) == kernel_settings
    finally:
        client.delete(f"/api/subscriptions/{subscription['uuid']}", headers=headers)
        client.delete(f"/api/subscriptions/{kernel_subscription['uuid']}", headers=headers)
        delete_group(access_token, xray_group["id"])
        delete_group(access_token, kernel_group["id"])
        if host.status_code == 201:
            client.delete(f"/api/host/{host.json()['id']}", headers=headers)
        if xray:
            delete_core(access_token, xray["id"])
        delete_core(access_token, kernel["id"])


@pytest.mark.parametrize("addresses", [[], None, ["10.95.0.1"], ["10.95.0.1/32"]])
def test_xray_core_with_unmanaged_wireguard_inbound_saves(access_token, addresses):
    secret, _ = generate_wireguard_keypair()
    tag = unique_name("xray_wg_unmanaged")
    core = create_core(access_token, config=wireguard_config("xray", tag, secret, addresses))
    try:
        response = client.get("/api/inbounds", headers=auth_headers(access_token))
        assert response.status_code == 200, response.text
        assert tag not in response.json()
    finally:
        delete_core(access_token, core["id"])


@pytest.mark.parametrize("first_type,second_type", [("wg", "xray"), ("xray", "wg"), ("xray", "xray")])
def test_wireguard_overlap_validation_across_backends(access_token, first_type, second_type):
    secret, _ = generate_wireguard_keypair()
    first = create_core(
        access_token,
        type=first_type,
        config=wireguard_config(first_type, unique_name("wg_first"), secret, ["10.94.0.1/24"]),
    )
    try:
        config = wireguard_config(second_type, unique_name("wg_second"), secret, ["10.94.0.5/25"])
        response = client.post(
            "/api/core",
            headers=auth_headers(access_token),
            json={
                "name": unique_name("overlap"),
                "type": second_type,
                "config": config,
                "exclude_inbound_tags": [],
                "fallbacks_inbound_tags": [],
            },
        )
        assert response.status_code == 400, response.text
        assert "overlaps" in response.text
        config = wireguard_config(second_type, unique_name("wg_shared"), secret, ["10.94.0.5/24"])
        shared = create_core(access_token, type=second_type, config=config)
        delete_core(access_token, shared["id"])
    finally:
        delete_core(access_token, first["id"])
