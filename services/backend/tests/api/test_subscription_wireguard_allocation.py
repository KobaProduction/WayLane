"""UUID-first WireGuard provisioner, subnet bounds and pool reuse acceptance."""

import asyncio
from uuid import UUID, uuid4

from sqlalchemy import select

from app.db.models import Subscription
from app.utils.crypto import generate_wireguard_keypair, get_wireguard_public_key
from tests.api import TestSession, client
from tests.api.helpers import auth_headers, create_core, create_group, delete_core, delete_group, unique_name


def _wg_config(interface_name: str, address: list[str], port: int = 51820) -> dict:
    private_key, _ = generate_wireguard_keypair()
    return {"interface_name": interface_name, "private_key": private_key, "listen_port": port, "address": address}


def _new_wg_core(access_token: str, name: str, subnet: list[str], *, port: int = 51820) -> dict:
    return create_core(
        access_token,
        name=unique_name("wg_core"),
        config=_wg_config(name, subnet, port),
        type="wg",
        fallbacks=[],
    )


def test_wireguard_core_subnet_validation_remains_independent_of_subscription_uuidentity(access_token):
    headers = auth_headers(access_token)
    missing = client.post(
        "/api/core",
        headers=headers,
        json={
            "name": unique_name("wg_missing"),
            "type": "wg",
            "exclude_inbound_tags": [],
            "fallbacks_inbound_tags": [],
            "config": _wg_config(unique_name("wg_missing_interface"), []),
        },
    )
    assert missing.status_code == 400, missing.text
    assert "IPv4 or IPv6" in missing.json()["detail"]
    outside = _new_wg_core(access_token, unique_name("wg_outside"), ["192.168.5.1/24"])
    inside = _new_wg_core(access_token, unique_name("wg_base"), ["10.77.0.1/24"])
    try:
        for subnet in (["10.77.0.129/25"], ["10.77.0.5/23"]):
            overlapping = client.post(
                "/api/core",
                headers=headers,
                json={
                    "name": unique_name("wg_overlap"),
                    "type": "wg",
                    "exclude_inbound_tags": [],
                    "fallbacks_inbound_tags": [],
                    "config": _wg_config(unique_name("wg_overlap_interface"), subnet),
                },
            )
            assert overlapping.status_code == 400, overlapping.text
            assert "overlaps" in overlapping.json()["detail"]
        twin = _new_wg_core(access_token, unique_name("wg_twin"), ["10.77.0.5/24"], port=51822)
        delete_core(access_token, twin["id"])
    finally:
        delete_core(access_token, inside["id"])
        delete_core(access_token, outside["id"])


def test_uuid_subscription_wireguard_pool_reuses_freed_peer_without_changing_entitlement(access_token):
    name = unique_name("wg_subscription_pool")
    core = _new_wg_core(access_token, name, ["10.88.0.1/24"])
    group = create_group(access_token, inbound_tags=[name])
    headers = auth_headers(access_token)
    uuids = [str(uuid4()) for _ in range(3)]
    created: list[str] = []

    async def stored(sid: str) -> tuple[dict, str]:
        async with TestSession() as db:
            subscription = (await db.execute(select(Subscription).where(Subscription.uuid == UUID(sid)))).scalar_one()
            return dict(subscription.proxy_settings["wireguard"]), subscription.entitlement

    try:
        for sid in uuids[:2]:
            response = client.post(
                "/api/subscriptions", headers=headers, json={"uuid": sid, "group_ids": [group["id"]]}
            )
            assert response.status_code == 201, response.text
            created.append(sid)
        first, first_entitlement = asyncio.run(stored(uuids[0]))
        second, second_entitlement = asyncio.run(stored(uuids[1]))
        assert first_entitlement == second_entitlement == "payment_required"
        assert first["peer_ips"] == ["10.88.0.2/32"]
        assert second["peer_ips"] == ["10.88.0.3/32"]
        assert first["public_key"] == get_wireguard_public_key(first["private_key"])
        assert second["public_key"] == get_wireguard_public_key(second["private_key"])

        removed = client.delete(f"/api/subscriptions/{uuids[0]}", headers=headers)
        assert removed.status_code == 204, removed.text
        created.remove(uuids[0])
        response = client.post(
            "/api/subscriptions", headers=headers, json={"uuid": uuids[2], "group_ids": [group["id"]]}
        )
        assert response.status_code == 201, response.text
        created.append(uuids[2])
        reused, entitlement = asyncio.run(stored(uuids[2]))
        assert reused["peer_ips"] == first["peer_ips"]
        assert entitlement == "payment_required"
    finally:
        for sid in created:
            client.delete(f"/api/subscriptions/{sid}", headers=headers)
        delete_group(access_token, group["id"])
        delete_core(access_token, core["id"])


def test_shared_wireguard_cidr_uses_one_peer_address_in_uuid_subscription_links(access_token, core_entitlement_headers):
    """Multiple WireGuard interfaces sharing one subnet must not allocate duplicate client addresses."""
    from urllib.parse import parse_qs, urlsplit

    headers = auth_headers(access_token)
    first_name = unique_name("wg_shared_primary")
    second_name = unique_name("wg_shared_secondary")
    first_core = _new_wg_core(access_token, first_name, ["10.55.0.1/24"])
    second_core = _new_wg_core(access_token, second_name, ["10.55.0.5/24"], port=51821)
    hosts: list[int] = []
    group = None
    sid = str(uuid4())
    created = False
    try:
        for index, (name, port) in enumerate(((first_name, 51820), (second_name, 51821))):
            response = client.post(
                "/api/host",
                headers=headers,
                json={
                    "remark": unique_name(f"wg_shared_host_{index}"),
                    "address": [f"198.51.100.{31 + index}"],
                    "port": port,
                    "inbound_tag": name,
                    "priority": index + 1,
                },
            )
            assert response.status_code == 201, response.text
            hosts.append(response.json()["id"])
        group = create_group(access_token, inbound_tags=[first_name, second_name])
        response = client.post("/api/subscriptions", headers=headers, json={"uuid": sid, "group_ids": [group["id"]]})
        assert response.status_code == 201, response.text
        created = True
        assert response.json()["entitlement"] == "payment_required"
        enabled = client.put(
            f"/api/subscriptions/{sid}/entitlement",
            headers=core_entitlement_headers,
            json={"entitlement": "active", "revision": 1},
        )
        assert enabled.status_code == 200, enabled.text
        link = client.get(f"/api/subscriptions/{sid}/link", headers=headers)
        assert link.status_code == 200, link.text
        token = link.json()["url"].split("/sub/", 1)[-1]
        formats = client.get(f"/sub/{token}/links")
        assert formats.status_code == 200, formats.text
        addresses = [
            parse_qs(urlsplit(line).query)["address"]
            for line in formats.text.splitlines()
            if line.startswith("wireguard://")
        ]
        assert addresses == [["10.55.0.2/32"], ["10.55.0.2/32"]]
    finally:
        if created:
            client.delete(f"/api/subscriptions/{sid}", headers=headers)
        if group is not None:
            delete_group(access_token, group["id"])
        for host_id in hosts:
            client.delete(f"/api/host/{host_id}", headers=headers)
        delete_core(access_token, second_core["id"])
        delete_core(access_token, first_core["id"])
