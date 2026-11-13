"""UUID identity, revocable access code and commercial state on live HTTP routes."""

import asyncio
import re
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid1, uuid4

import pytest
from sqlalchemy import select

from app.db.models import Subscription
from app.utils.crypto import generate_wireguard_keypair, get_wireguard_public_key
from tests.api import TestSession, client
from tests.api.helpers import (
    auth_headers,
    create_admin,
    create_core,
    create_group,
    create_hosts_for_inbounds,
    create_subscription_template,
    delete_admin,
    delete_core,
    delete_group,
    delete_subscription_template,
    get_inbounds,
    unique_name,
)


def test_subscription_uuid_api_and_no_numeric_user_id_in_public_link(access_token, core_entitlement_headers):
    headers = auth_headers(access_token)
    core = create_core(access_token)
    group = create_group(access_token, inbound_tags=[get_inbounds(access_token)[0]])
    sid = str(uuid4())
    created = client.post(
        "/api/subscriptions",
        headers=headers,
        json={
            "uuid": sid,
            "group_ids": [group["id"]],
        },
    )
    assert created.status_code == 201, created.text
    non_v4 = client.post(
        "/api/subscriptions",
        headers=headers,
        json={"uuid": str(uuid1()), "group_ids": [group["id"]]},
    )
    assert non_v4.status_code == 422, non_v4.text
    try:
        result = client.get(f"/api/subscriptions/{sid}/link", headers=headers)
        assert result.status_code == 200, result.text
        bypass = client.post(
            "/api/subscriptions",
            headers=headers,
            json={"group_ids": [group["id"]], "entitlement": "active"},
        )
        assert bypass.status_code == 422, bypass.text
        token = result.json()["url"].split("/sub/", 1)[-1]
        assert re.fullmatch(r"[0-9a-f-]{36}\.[A-Za-z0-9_-]{43}", token)
        assert token.split(".", 1)[0] == sid
        listed = client.get("/api/subscriptions", headers=headers)
        assert listed.status_code == 200, listed.text
        assert any(item["uuid"] == sid for item in listed.json()["items"])
        details = client.get(f"/api/subscriptions/{sid}", headers=headers).json()
        assert "id" not in details and "username" not in details and "email" not in details
        assert client.get(f"/sub/{token}/info").status_code == 403
        assert client.get(f"/sub/{sid}.{'0' * 43}/info").status_code == 404

        def entitlement(value, revision):
            return client.put(
                f"/api/subscriptions/{sid}/entitlement",
                headers=core_entitlement_headers,
                json={"entitlement": value, "revision": revision},
            )

        staff_entitlement = client.put(
            f"/api/subscriptions/{sid}/entitlement",
            headers=headers,
            json={"entitlement": "active", "revision": 1},
        )
        assert staff_entitlement.status_code == 403, staff_entitlement.text
        assert entitlement("active", 1).status_code == 200
        assert client.get(f"/sub/{token}/info").status_code == 200
        assert entitlement("payment_required", 2).status_code == 200
        assert client.get(f"/sub/{token}/info").status_code == 403
        assert entitlement("active", 2).status_code == 409
        assert entitlement("active", 3).status_code == 200
        rotated = client.post(f"/api/subscriptions/{sid}/rotate-access", headers=headers)
        assert rotated.status_code == 200, rotated.text
        new_token = rotated.json()["url"].split("/sub/", 1)[-1]
        assert new_token != token and new_token.split(".", 1)[0] == sid
        assert client.get(f"/sub/{token}/info").status_code == 404
        assert client.get(f"/sub/{new_token}/info").status_code == 200
    finally:
        assert client.delete(f"/api/subscriptions/{sid}", headers=headers).status_code == 204
        delete_group(access_token, group["id"])
        delete_core(access_token, core["id"])


def test_product_subscription_creation_and_policy_use_uuid_only(access_token, core_entitlement_headers):
    sid = str(uuid4())
    headers = auth_headers(access_token)
    core = create_core(access_token)
    group = create_group(access_token, inbound_tags=[get_inbounds(access_token)[0]])
    created = client.post(
        "/api/subscriptions",
        headers=headers,
        json={"uuid": sid, "group_ids": [group["id"]], "device_limit": 2},
    )
    assert created.status_code == 201, created.text
    assert created.json()["uuid"] == sid
    assert created.json()["entitlement"] == "payment_required"
    assert created.json()["device_limit"] == 2
    assert "id" not in created.json() and "username" not in created.json()

    try:
        link = client.get(f"/api/subscriptions/{sid}/link", headers=headers)
        assert link.status_code == 200, link.text
        token = link.json()["url"].split("/sub/", 1)[-1]
        assert token.startswith(sid + ".")
        assert client.get(f"/sub/{token}/info").status_code == 403

        enabled = client.put(
            f"/api/subscriptions/{sid}/entitlement",
            headers=core_entitlement_headers,
            json={"entitlement": "active", "revision": 1},
        )
        assert enabled.status_code == 200
        assert client.get(f"/sub/{token}/info").status_code == 200
        staff_policy = client.put(
            f"/api/subscriptions/{sid}/policy", headers=headers, json={"device_limit": 3, "revision": 1}
        )
        assert staff_policy.status_code == 403, staff_policy.text
        policy = client.put(
            f"/api/subscriptions/{sid}/policy",
            headers=core_entitlement_headers,
            json={"device_limit": 3, "revision": 1},
        )
        assert policy.status_code == 200, policy.text
        assert policy.json()["device_limit"] == 3
        assert policy.json()["policy_revision"] == 1
        repeat_policy = client.put(
            f"/api/subscriptions/{sid}/policy",
            headers=core_entitlement_headers,
            json={"device_limit": 3, "revision": 1},
        )
        assert repeat_policy.status_code == 200, repeat_policy.text
        conflicting_policy = client.put(
            f"/api/subscriptions/{sid}/policy",
            headers=core_entitlement_headers,
            json={"device_limit": 4, "revision": 1},
        )
        assert conflicting_policy.status_code == 409, conflicting_policy.text
        stale_policy = client.put(
            f"/api/subscriptions/{sid}/policy",
            headers=core_entitlement_headers,
            json={"device_limit": 5, "revision": 0, "enabled": False},
        )
        assert stale_policy.status_code == 409
        assert client.get(f"/api/subscriptions/{sid}", headers=headers).json()["device_limit"] == 3
        assert client.get(f"/sub/{token}/info").status_code == 200
    finally:
        assert client.delete(f"/api/subscriptions/{sid}", headers=headers).status_code == 204
        delete_group(access_token, group["id"])
        delete_core(access_token, core["id"])


def test_read_all_does_not_grant_write_all_on_uuid_subscriptions(access_token):
    """An operator able to inspect all subscriptions may change only its own."""
    owner_headers = auth_headers(access_token)
    role = client.post(
        "/api/admin-role",
        headers=owner_headers,
        json={
            "name": unique_name("read_all_update_own"),
            "permissions": {
                "subscriptions": {
                    "read": {"scope": 2},
                    "update": {"scope": 1},
                    "delete": {"scope": 1},
                },
            },
        },
    )
    assert role.status_code == 201, role.text
    admin = None
    core = None
    group = None
    sid = str(uuid4())
    try:
        admin = create_admin(access_token, role_id=role.json()["id"])
        login = client.post(
            "/api/admin/token",
            data={"username": admin["username"], "password": admin["password"], "grant_type": "password"},
        )
        assert login.status_code == 200, login.text
        headers = auth_headers(login.json()["access_token"])
        core = create_core(access_token)
        group = create_group(access_token, inbound_tags=[get_inbounds(access_token)[0]])
        created = client.post(
            "/api/subscriptions", headers=owner_headers, json={"uuid": sid, "group_ids": [group["id"]]}
        )
        assert created.status_code == 201, created.text
        assert client.get(f"/api/subscriptions/{sid}", headers=headers).status_code == 200
        assert (
            client.put(
                f"/api/subscriptions/{sid}/policy", headers=headers, json={"revision": 1, "device_limit": 2}
            ).status_code
            == 403
        )
        forbidden_batch = client.post("/api/subscriptions/bulk/disable", headers=headers, json={"uuids": [sid]})
        assert forbidden_batch.status_code == 404, forbidden_batch.text
        assert client.get(f"/api/subscriptions/{sid}", headers=owner_headers).json()["technical_status"] == "active"
        assert client.post(f"/api/subscriptions/{sid}/rotate-access", headers=headers).status_code == 404
        assert client.delete(f"/api/subscriptions/{sid}", headers=headers).status_code == 404
    finally:
        client.delete(f"/api/subscriptions/{sid}", headers=owner_headers)
        if group is not None:
            delete_group(access_token, group["id"])
        if core is not None:
            delete_core(access_token, core["id"])
        if admin is not None:
            delete_admin(access_token, admin["username"])
        client.delete(f"/api/admin-role/{role.json()['id']}", headers=owner_headers)


def test_subscription_analytics_have_uuid_first_public_paths(access_token):
    """Keep the existing aggregate calculations while retiring old User URLs."""
    end = datetime.now(UTC)
    query = {"period": "hour", "start": (end - timedelta(hours=4)).isoformat(), "end": end.isoformat()}
    headers = auth_headers(access_token)
    for url, expected in (
        ("/api/subscriptions/analytics/usage", "stats"),
        ("/api/subscriptions/analytics/counts/online", "count_during_period"),
        ("/api/subscriptions/analytics/client-updates", "segments"),
    ):
        response = client.get(url, params=query, headers=headers)
        assert response.status_code == 200, (url, response.text)
        assert expected in response.json(), (url, response.json())
        assert client.get(url, params=query).status_code == 401
    assert client.post("/api/subscriptions/traffic/reset-all").status_code == 401
    assert client.post("/api/subscriptions/traffic/reset-all", headers=headers).status_code == 403
    for retired_path in (
        "/api/users/usage",
        "/api/users/counts/online",
        "/api/users/sub_update/chart",
    ):
        assert client.get(retired_path, headers=headers).status_code == 404
    assert client.post("/api/users/reset", headers=headers).status_code == 404


def test_selected_subscription_bulk_api_uses_uuid_not_node_id(access_token, core_entitlement_headers):
    headers = auth_headers(access_token)
    core = create_core(access_token)
    group = create_group(access_token, inbound_tags=[get_inbounds(access_token)[0]])
    sid = str(uuid4())
    created = client.post(
        "/api/subscriptions",
        headers=headers,
        json={"uuid": sid, "group_ids": [group["id"]]},
    )
    assert created.status_code == 201, created.text
    try:
        batch = "/api/subscriptions/bulk"
        missing = client.post(f"{batch}/disable", headers=headers, json={"uuids": [sid, str(uuid4())]})
        assert missing.status_code == 404, missing.text
        assert client.get(f"/api/subscriptions/{sid}", headers=headers).json()["technical_status"] == "active"
        numeric = client.post(f"{batch}/disable", headers=headers, json={"ids": [1]})
        assert numeric.status_code == 422, numeric.text
        forbidden_uuid = client.post(f"{batch}/disable", headers=headers, json={"uuids": [str(uuid1())]})
        assert forbidden_uuid.status_code == 422, forbidden_uuid.text

        staff_disabled = client.post(f"{batch}/disable", headers=headers, json={"uuids": [sid]})
        assert staff_disabled.status_code == 403, staff_disabled.text
        disabled = client.post(f"{batch}/disable", headers=core_entitlement_headers, json={"uuids": [sid]})
        assert disabled.status_code == 200, disabled.text
        assert disabled.json() == {"uuids": [sid], "count": 1}
        assert client.get(f"/api/subscriptions/{sid}", headers=headers).json()["technical_status"] == "disabled"
        repeated = client.post(f"{batch}/disable", headers=core_entitlement_headers, json={"uuids": [sid]})
        assert repeated.status_code == 200, repeated.text
        assert repeated.json() == {"uuids": [], "count": 0}
        enabled = client.post(f"{batch}/enable", headers=core_entitlement_headers, json={"uuids": [sid]})
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["count"] == 1
        restored = client.get(f"/api/subscriptions/{sid}", headers=headers).json()
        assert restored["technical_status"] == "active"
        assert restored["entitlement"] == "payment_required"
        assert client.post(f"{batch}/disable", json={"uuids": [sid]}).status_code == 401
    finally:
        assert client.delete(f"/api/subscriptions/{sid}", headers=headers).status_code == 204
        delete_group(access_token, group["id"])
        delete_core(access_token, core["id"])


def test_selected_subscription_bulk_reset_reissue_and_delete(access_token, core_entitlement_headers):
    headers = auth_headers(access_token)
    core = create_core(access_token)
    group = create_group(access_token, inbound_tags=[get_inbounds(access_token)[0]])
    sid = str(uuid4())
    created = client.post(
        "/api/subscriptions",
        headers=headers,
        json={"uuid": sid, "group_ids": [group["id"]]},
    )
    assert created.status_code == 201, created.text
    try:
        batch = "/api/subscriptions/bulk"
        original_link = client.get(f"/api/subscriptions/{sid}/link", headers=headers)
        assert original_link.status_code == 200
        staff_reset = client.post(f"{batch}/reset_usage", headers=headers, json={"uuids": [sid]})
        assert staff_reset.status_code == 403, staff_reset.text
        reset = client.post(f"{batch}/reset_usage", headers=core_entitlement_headers, json={"uuids": [sid]})
        assert reset.status_code == 200, reset.text
        assert reset.json()["uuids"] == [sid]
        reissue = client.post(f"{batch}/reissue", headers=headers, json={"uuids": [sid]})
        assert reissue.status_code == 200, reissue.text
        assert reissue.json()["uuids"] == [sid]
        rotated = client.get(f"/api/subscriptions/{sid}/link", headers=headers)
        assert rotated.status_code == 200
        assert rotated.json()["url"] != original_link.json()["url"]
        deletion = client.post(f"{batch}/delete", headers=headers, json={"uuids": [sid]})
        assert deletion.status_code == 200, deletion.text
        assert deletion.json() == {"uuids": [sid], "count": 1}
        assert client.get(f"/api/subscriptions/{sid}", headers=headers).status_code == 404
    finally:
        # Cleanup also covers partial failures so the test is isolated.
        client.delete(f"/api/subscriptions/{sid}", headers=headers)
        delete_group(access_token, group["id"])
        delete_core(access_token, core["id"])


def test_legacy_group_bulk_cannot_mutate_core_owned_subscription(access_token):
    headers = auth_headers(access_token)
    core = create_core(access_token)
    tag = get_inbounds(access_token)[0]
    group = create_group(access_token, inbound_tags=[tag])
    other_group = create_group(access_token, inbound_tags=[tag])
    sid = str(uuid4())
    created = client.post("/api/subscriptions", headers=headers, json={"uuid": sid, "group_ids": [group["id"]]})
    assert created.status_code == 201, created.text
    try:

        # Historical routes must reject an old-style numeric identity; there
        # is no numeric primary key to retrieve from the new schema.
        internal_id = 999
        # Even a privileged old-style numeric ID may not bypass Core revision fencing.
        for action, target_group in (("add", other_group["id"]), ("remove", group["id"])):
            response = client.post(
                f"/api/groups/bulk/{action}",
                headers=headers,
                json={"users": [internal_id], "group_ids": [target_group]},
            )
            assert response.status_code == 404, response.text
            preview = client.post(
                f"/api/groups/bulk/{action}",
                headers=headers,
                json={"users": [internal_id], "group_ids": [target_group], "dry_run": True},
            )
            assert preview.status_code == 404, preview.text
        after = client.get(f"/api/subscriptions/{sid}", headers=headers).json()
        assert after["group_ids"] == [group["id"]]
        assert after["entitlement"] == "payment_required"
        assert after["policy_revision"] == 0
    finally:
        client.delete(f"/api/subscriptions/{sid}", headers=headers)
        delete_group(access_token, other_group["id"])
        delete_group(access_token, group["id"])
        delete_core(access_token, core["id"])


def test_core_managed_subscription_rejects_legacy_numeric_policy_bypasses(access_token):
    headers = auth_headers(access_token)
    core = create_core(access_token)
    group = create_group(access_token, inbound_tags=[get_inbounds(access_token)[0]])
    sid = str(uuid4())
    created = client.post("/api/subscriptions", headers=headers, json={"uuid": sid, "group_ids": [group["id"]]})
    assert created.status_code == 201, created.text
    try:

        numeric_id = 999  # Retired numeric subscriber IDs must never resolve.
        attempts = (
            ("PUT", f"/api/user/by-id/{numeric_id}", {"data_limit": 10**15}),
            ("PUT", f"/api/user/by-id/{numeric_id}/disabled", {"disabled": True}),
            ("POST", f"/api/user/by-id/{numeric_id}/reset", None),
            ("POST", "/api/users/bulk/disable", {"ids": [numeric_id]}),
            ("POST", "/api/users/bulk/enable", {"ids": [numeric_id]}),
            ("POST", "/api/users/bulk/reset", {"ids": [numeric_id]}),
            ("POST", "/api/users/bulk/expire", {"users": [numeric_id], "amount": 3600}),
            ("POST", "/api/users/bulk/data_limit", {"users": [numeric_id], "amount": 1024}),
            ("POST", "/api/users/bulk/apply_template", {"ids": [numeric_id], "user_template_id": 9999}),
        )
        for method, path, payload in attempts:
            response = client.request(method, path, headers=headers, json=payload)
            expected_status = 404
            assert response.status_code == expected_status, (path, response.status_code, response.text)
        result = client.get(f"/api/subscriptions/{sid}", headers=headers).json()
        assert result["group_ids"] == [group["id"]]
        assert result["traffic_limit"] is None
        assert result["technical_status"] == "active"
        assert result["entitlement"] == "payment_required"
    finally:
        client.delete(f"/api/subscriptions/{sid}", headers=headers)
        delete_group(access_token, group["id"])
        delete_core(access_token, core["id"])


def test_admin_mass_actions_cannot_reactivate_or_remove_core_owned_subscription(access_token):
    """Admin bulk endpoints cannot bypass VPN Core by mutating subscriptions indirectly."""
    from sqlalchemy import update

    headers = auth_headers(access_token)
    member = create_admin(access_token, role_id=2)
    core = create_core(access_token)
    group = create_group(access_token, inbound_tags=[get_inbounds(access_token)[0]])
    sid = str(uuid4())
    response = client.post("/api/subscriptions", headers=headers, json={"uuid": sid, "group_ids": [group["id"]]})
    assert response.status_code == 201, response.text
    try:

        async def assign_owner():
            async with TestSession() as db:
                await db.execute(
                    update(Subscription).where(Subscription.uuid == UUID(sid)).values(admin_id=member["id"])
                )
                await db.commit()

        asyncio.run(assign_owner())
        member_id = member["id"]
        actions = (
            ("POST", f"/api/admin/by-id/{member_id}/users/disable", None),
            ("POST", f"/api/admin/by-id/{member_id}/users/activate", None),
            ("DELETE", f"/api/admin/by-id/{member_id}/users", None),
            ("POST", "/api/admins/bulk/users/disable", {"ids": [member_id]}),
            ("POST", "/api/admins/bulk/users/activate", {"ids": [member_id]}),
            ("DELETE", "/api/admins/bulk/users", {"ids": [member_id]}),
            ("DELETE", f"/api/admin/by-id/{member_id}", None),
            ("POST", "/api/admins/bulk/delete", {"ids": [member_id]}),
        )
        for method, path, body in actions:
            response = client.request(method, path, headers=headers, json=body)
            expected = 404 if "/users" in path else 409
            assert response.status_code == expected, (method, path, response.status_code, response.text)
            current = client.get(f"/api/subscriptions/{sid}", headers=headers)
            assert current.status_code == 200, current.text
            assert current.json()["technical_status"] == "active"
            assert current.json()["entitlement"] == "payment_required"
    finally:
        client.delete(f"/api/subscriptions/{sid}", headers=headers)
        delete_admin(access_token, member["username"])
        delete_group(access_token, group["id"])
        delete_core(access_token, core["id"])


def test_scheduler_never_reactivates_or_resets_commercial_core_subscription(access_token):
    """WayLane may deny a Core subscription at expiry, not renew or reset paid usage."""
    from app.db.crud.subscription import (
        get_active_to_expire_subscriptions,
        get_on_hold_to_active_subscriptions,
        get_subscriptions_to_reset_data_usage,
    )
    from app.db.models import DataLimitResetStrategy, SubscriptionStatus

    headers = auth_headers(access_token)
    core = create_core(access_token)
    group = create_group(access_token, inbound_tags=[get_inbounds(access_token)[0]])
    sid = str(uuid4())
    created = client.post("/api/subscriptions", headers=headers, json={"uuid": sid, "group_ids": [group["id"]]})
    assert created.status_code == 201, created.text
    try:

        async def assert_scheduler_scope():
            async with TestSession() as db:
                sub = (await db.execute(select(Subscription).where(Subscription.uuid == UUID(sid)))).scalar_one()
                target_id = sub.uuid
                sub.status = SubscriptionStatus.on_hold
                sub.on_hold_timeout = datetime.now(UTC) - timedelta(days=1)
                await db.commit()

            async with TestSession() as db:
                ready = await get_on_hold_to_active_subscriptions(db)
                assert target_id not in {item.uuid for item in ready}
                sub = (await db.execute(select(Subscription).where(Subscription.uuid == UUID(sid)))).scalar_one()
                sub.status = SubscriptionStatus.active
                sub.created_at = datetime.now(UTC) - timedelta(days=5)
                sub.data_limit_reset_strategy = DataLimitResetStrategy.day
                await db.commit()

            async with TestSession() as db:
                resets = await get_subscriptions_to_reset_data_usage(db)
                assert target_id not in {item.uuid for item in resets}
                sub = (await db.execute(select(Subscription).where(Subscription.uuid == UUID(sid)))).scalar_one()
                sub.expire = datetime.now(UTC) - timedelta(minutes=2)
                await db.commit()

            async with TestSession() as db:
                expired = await get_active_to_expire_subscriptions(db)
                assert target_id in {item.uuid for item in expired}, "Fail-closed expiry is still allowed"

        asyncio.run(assert_scheduler_scope())
    finally:
        client.delete(f"/api/subscriptions/{sid}", headers=headers)
        delete_group(access_token, group["id"])
        delete_core(access_token, core["id"])


def test_legacy_user_bulk_and_template_mutation_routes_are_retired():
    """No second public path may bypass commercial Core-owned UUID policy."""
    routes = set(client.app.openapi()["paths"])
    obsolete = {
        "/api/users/bulk/delete",
        "/api/users/bulk/reset",
        "/api/users/bulk/disable",
        "/api/users/bulk/enable",
        "/api/users/bulk/revoke_sub",
        "/api/users/bulk/set_owner",
        "/api/users/bulk/from_template",
        "/api/users/bulk/apply_template",
        "/api/users/bulk/expire",
        "/api/users/bulk/data_limit",
        "/api/users/bulk/proxy_settings",
        "/api/user/from_template",
        "/api/user/from_template/{username}",
        "/api/user/from_template/by-username/{username}",
        "/api/user/from_template/by-id/{user_id}",
        "/api/groups/bulk/add",
        "/api/groups/bulk/remove",
    }
    assert obsolete.isdisjoint(routes), sorted(obsolete & routes)
    # The entire old public User router and its numeric HWID API are gone.
    assert not any(
        path in ("/api/user", "/api/users") or path.startswith(("/api/user/", "/api/users/")) for path in routes
    )
    assert "/api/subscriptions/bulk/{action}" in routes




def test_subscription_template_api_does_not_expose_legacy_user_naming(access_token):
    headers = auth_headers(access_token)
    core = create_core(access_token)
    group = create_group(access_token, inbound_tags=[get_inbounds(access_token)[0]])
    template = create_subscription_template(access_token, group_ids=[group["id"]])
    try:
        item = client.get(f"/api/subscription_template/{template['id']}", headers=headers)
        assert item.status_code == 200, item.text
        assert item.json()["id"] == template["id"]
        listing = client.get("/api/subscription_templates", headers=headers)
        assert listing.status_code == 200, listing.text
        assert template["id"] in {row["id"] for row in listing.json()}
        assert client.get("/api/user_templates", headers=headers).status_code == 404
        assert client.get(f"/api/user_template/{template['id']}", headers=headers).status_code == 404
        assert client.get(f"/api/subscription_template/{template['id']}").status_code == 401
    finally:
        delete_subscription_template(access_token, template["id"])
        delete_group(access_token, group["id"])
        delete_core(access_token, core["id"])


def test_no_admin_mass_subscriptions_or_user_named_system_routes():
    paths = client.app.openapi()["paths"]
    obsolete = (
        "/api/admin/{username}/users/disable",
        "/api/admin/by-username/{username}/users/disable",
        "/api/admin/by-id/{admin_id}/users/disable",
        "/api/admin/{username}/users/activate",
        "/api/admin/by-username/{username}/users/activate",
        "/api/admin/by-id/{admin_id}/users/activate",
        "/api/admin/{username}/users",
        "/api/admin/by-username/{username}/users",
        "/api/admin/by-id/{admin_id}/users",
        "/api/admins/bulk/users/disable",
        "/api/admins/bulk/users/activate",
        "/api/admins/bulk/users",
        "/api/node/user_counts/{metric}",
        "/api/system/users",
    )
    assert not set(obsolete).intersection(paths)
    assert "/api/node/subscription_counts/{metric}" not in paths
    assert "/api/node-registry/analytics/summary" in paths
    assert "/api/node-registry/analytics/usage/hours" in paths
    assert "/api/system/subscriptions" in paths


def test_subscription_list_paginates_uuid_only_and_rejects_legacy_identity_fields(access_token):
    """Replace old list/simple/search API coverage with the actual bounded UUID list contract."""
    headers = auth_headers(access_token)
    core = create_core(access_token)
    group = create_group(access_token, inbound_tags=[get_inbounds(access_token)[0]])
    originals = client.get("/api/subscriptions", headers=headers)
    assert originals.status_code == 200, originals.text
    baseline_total = originals.json()["total"]
    uuids = [str(uuid4()) for _ in range(3)]
    try:
        for sid in uuids:
            created = client.post(
                "/api/subscriptions",
                headers=headers,
                json={"uuid": sid, "group_ids": [group["id"]]},
            )
            assert created.status_code == 201, created.text
            assert created.json()["entitlement"] == "payment_required"
            assert created.json()["uuid"] == sid
            assert not set(created.json()) & {"id", "username", "email", "phone", "admin_id"}

        listed = client.get("/api/subscriptions", headers=headers, params={"limit": 100, "offset": 0})
        assert listed.status_code == 200, listed.text
        assert listed.json()["total"] == baseline_total + len(uuids)
        assert set(uuids) <= {item["uuid"] for item in listed.json()["items"]}
        assert all("id" not in item and "username" not in item for item in listed.json()["items"])

        first = client.get("/api/subscriptions", headers=headers, params={"limit": 1, "offset": 0})
        second = client.get("/api/subscriptions", headers=headers, params={"limit": 1, "offset": 1})
        for page in (first, second):
            assert page.status_code == 200, page.text
            assert len(page.json()["items"]) == 1
            assert page.json()["limit"] == 1
            assert page.json()["total"] == baseline_total + len(uuids)
        assert first.json()["items"][0]["uuid"] != second.json()["items"][0]["uuid"]

        for invalid_params in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
            response = client.get("/api/subscriptions", headers=headers, params=invalid_params)
            assert response.status_code == 422, (invalid_params, response.text)
        assert client.get("/api/subscriptions").status_code == 401

        for legacy_field in ("username", "contact", "status", "entitlement", "admin_id", "proxy_settings"):
            rejected = client.post(
                "/api/subscriptions",
                headers=headers,
                json={"group_ids": [group["id"]], legacy_field: "active"},
            )
            assert rejected.status_code == 422, (legacy_field, rejected.status_code, rejected.text)
        assert client.post("/api/subscriptions", headers=headers, json={"group_ids": []}).status_code == 422
        assert client.get("/api/subscriptions", headers=headers).json()["total"] == baseline_total + len(uuids)
    finally:
        for sid in uuids:
            client.delete(f"/api/subscriptions/{sid}", headers=headers)
        delete_group(access_token, group["id"])
        delete_core(access_token, core["id"])


def test_subscription_formats_are_commercially_fenced_with_uuid_token(access_token, core_entitlement_headers):
    """Replace old /api/user-created format checks with Core-authorized UUID token delivery."""
    headers = auth_headers(access_token)
    core = create_core(access_token)
    group = create_group(access_token, inbound_tags=[get_inbounds(access_token)[0]])
    hosts = create_hosts_for_inbounds(access_token)
    sid = str(uuid4())
    created = client.post("/api/subscriptions", headers=headers, json={"uuid": sid, "group_ids": [group["id"]]})
    assert created.status_code == 201, created.text
    try:
        link = client.get(f"/api/subscriptions/{sid}/link", headers=headers)
        assert link.status_code == 200, link.text
        token = link.json()["url"].split("/sub/", 1)[-1]
        path = f"/sub/{token}"
        for suffix in ("", "/info", "/raw", "/links"):
            blocked = client.get(path + suffix)
            assert blocked.status_code == 403, (suffix, blocked.status_code, blocked.text[:150])

        active = client.put(
            f"/api/subscriptions/{sid}/entitlement",
            headers=core_entitlement_headers,
            json={"entitlement": "active", "revision": 1},
        )
        assert active.status_code == 200, active.text
        for suffix in (
            "",
            "/info",
            "/usage",
            "/apps",
            "/raw",
            "/sing_box",
            "/clash_meta",
            "/clash",
            "/outline",
            "/links",
            "/links_base64",
            "/wireguard",
            "/xray",
        ):
            allowed = client.get(path + suffix, headers={"Accept": "text/html"} if suffix == "" else None)
            assert allowed.status_code == 200, (suffix, allowed.status_code, allowed.text[:150])
        head = client.head(path)
        assert head.status_code == 200 and not head.content

        denied = client.put(
            f"/api/subscriptions/{sid}/entitlement",
            headers=core_entitlement_headers,
            json={"entitlement": "suspended", "revision": 2},
        )
        assert denied.status_code == 200, denied.text
        for suffix in ("", "/info", "/raw", "/links", "/xray"):
            blocked = client.get(path + suffix)
            assert blocked.status_code == 403, (suffix, blocked.status_code, blocked.text[:150])
    finally:
        client.delete(f"/api/subscriptions/{sid}", headers=headers)
        for host in hosts:
            client.delete(f"/api/host/{host['id']}", headers=headers)
        delete_group(access_token, group["id"])
        delete_core(access_token, core["id"])


def test_uuid_bulk_reissue_rotates_wireguard_keys_without_reallocating_peer_ips(access_token):
    """Preserve the old bulk revoke security regression on the UUID-first API."""
    private, _ = generate_wireguard_keypair()
    name = unique_name("subscription_reissue_wg")
    core = create_core(
        access_token,
        name=unique_name("subscription_reissue_core"),
        config={
            "interface_name": name,
            "private_key": private,
            "listen_port": 51820,
            "address": ["10.46.0.1/24"],
        },
        type="wg",
        fallbacks=[],
    )
    group = create_group(access_token, inbound_tags=[name])
    headers = auth_headers(access_token)
    uuids = [str(uuid4()) for _ in range(2)]

    async def wireguard_for_uuid(uuid: str):
        async with TestSession() as db:
            subscription = (await db.execute(select(Subscription).where(Subscription.uuid == UUID(uuid)))).scalar_one()
            return dict(subscription.proxy_settings["wireguard"]), subscription.entitlement

    try:
        for sid in uuids:
            response = client.post(
                "/api/subscriptions", headers=headers, json={"uuid": sid, "group_ids": [group["id"]]}
            )
            assert response.status_code == 201, response.text
        before = {sid: asyncio.run(wireguard_for_uuid(sid)) for sid in uuids}
        for wireguard, entitlement in before.values():
            assert wireguard["private_key"]
            assert wireguard["public_key"] == get_wireguard_public_key(wireguard["private_key"])
            assert wireguard["peer_ips"]
            assert entitlement == "payment_required"
        response = client.post("/api/subscriptions/bulk/reissue", headers=headers, json={"uuids": uuids})
        assert response.status_code == 200, response.text
        assert response.json() == {"uuids": sorted(uuids), "count": 2}
        after = {sid: asyncio.run(wireguard_for_uuid(sid)) for sid in uuids}
        for sid in uuids:
            old, _ = before[sid]
            new, entitlement = after[sid]
            assert new["private_key"] != old["private_key"]
            assert new["public_key"] != old["public_key"]
            assert new["public_key"] == get_wireguard_public_key(new["private_key"])
            assert new["peer_ips"] == old["peer_ips"]
            assert entitlement == "payment_required"
    finally:
        for sid in uuids:
            client.delete(f"/api/subscriptions/{sid}", headers=headers)
        delete_group(access_token, group["id"])
        delete_core(access_token, core["id"])


def test_uuid_subscription_expiry_timezone_offset_preserves_utc_instant(access_token):
    """An explicit provider expiry must not shift when Core sends a non-UTC offset."""
    headers = auth_headers(access_token)
    core = create_core(access_token)
    group = create_group(access_token, inbound_tags=[get_inbounds(access_token)[0]])
    sid = str(uuid4())
    expiry_utc = datetime.now(UTC).replace(microsecond=0) + timedelta(days=30)
    expiry_local = expiry_utc.astimezone(timezone(timedelta(hours=3, minutes=30)))
    try:
        created = client.post(
            "/api/subscriptions",
            headers=headers,
            json={"uuid": sid, "group_ids": [group["id"]], "expire_at": expiry_local.isoformat()},
        )
        assert created.status_code == 201, created.text
        actual = datetime.fromisoformat(created.json()["expire_at"])
        assert actual.astimezone(UTC) == expiry_utc
        assert created.json()["entitlement"] == "payment_required"
        refreshed = client.get(f"/api/subscriptions/{sid}", headers=headers)
        assert refreshed.status_code == 200, refreshed.text
        assert datetime.fromisoformat(refreshed.json()["expire_at"]).astimezone(UTC) == expiry_utc
    finally:
        client.delete(f"/api/subscriptions/{sid}", headers=headers)
        delete_group(access_token, group["id"])
        delete_core(access_token, core["id"])


def test_group_deletion_cannot_remove_core_managed_subscription_policy(access_token):
    """Provider group removal cannot bypass Core's revision-fenced group assignment."""
    headers = auth_headers(access_token)
    core = create_core(access_token)
    current_group = create_group(access_token, inbound_tags=[get_inbounds(access_token)[0]])
    independent_group = create_group(access_token, inbound_tags=[get_inbounds(access_token)[0]])
    sid = str(uuid4())
    created = client.post("/api/subscriptions", headers=headers, json={"uuid": sid, "group_ids": [current_group["id"]]})
    assert created.status_code == 201, created.text
    try:
        direct = client.delete(f"/api/group/{current_group['id']}", headers=headers)
        assert direct.status_code == 409, direct.text

        combined = client.post(
            "/api/groups/bulk/delete",
            headers=headers,
            json={"ids": [current_group["id"], independent_group["id"]]},
        )
        assert combined.status_code == 409, combined.text
        for group in (current_group, independent_group):
            assert client.get(f"/api/group/{group['id']}", headers=headers).status_code == 200
        after = client.get(f"/api/subscriptions/{sid}", headers=headers)
        assert after.status_code == 200, after.text
        assert after.json()["group_ids"] == [current_group["id"]]
        assert after.json()["policy_revision"] == 0
    finally:
        client.delete(f"/api/subscriptions/{sid}", headers=headers)
        delete_group(access_token, current_group["id"])
        delete_group(access_token, independent_group["id"])
        delete_core(access_token, core["id"])


@pytest.mark.parametrize(
    ("subscription_read", "denied_status"),
    [
        pytest.param({"scope": 1}, 404, id="subscription-read-own-hwid-all"),
        pytest.param(None, 403, id="no-subscription-read-hwid-all"),
    ],
)
def test_uuid_device_grants_never_widen_subscription_visibility(access_token, subscription_read, denied_status):
    """Global HWID privileges are limited by the actor's subscription read scope."""
    owner_headers = auth_headers(access_token)
    core = create_core(access_token)
    group = create_group(access_token, inbound_tags=[get_inbounds(access_token)[0]])
    sid = str(uuid4())
    created = client.post("/api/subscriptions", headers=owner_headers, json={"uuid": sid, "group_ids": [group["id"]]})
    assert created.status_code == 201, created.text
    role = None
    actor = None
    try:
        permissions = {"hwids": {"read": True, "delete": True}}
        if subscription_read is not None:
            permissions["subscriptions"] = {"read": subscription_read}
        role_response = client.post(
            "/api/admin-role",
            headers=owner_headers,
            json={"name": unique_name("hwid_scope_intersection"), "permissions": permissions},
        )
        assert role_response.status_code == 201, role_response.text
        role = role_response.json()
        actor = create_admin(access_token, role_id=role["id"])
        login = client.post(
            "/api/admin/token",
            data={"username": actor["username"], "password": actor["password"], "grant_type": "password"},
        )
        assert login.status_code == 200, login.text
        attacker_headers = auth_headers(login.json()["access_token"])
        for method, path in (
            ("GET", f"/api/subscriptions/{sid}/devices"),
            ("POST", f"/api/subscriptions/{sid}/devices/reset"),
            ("DELETE", f"/api/subscriptions/{sid}/devices/some-device"),
        ):
            denied = client.request(method, path, headers=attacker_headers)
            assert denied.status_code == denied_status, (path, denied.status_code, denied.text)
        owner_view = client.get(f"/api/subscriptions/{sid}/devices", headers=owner_headers)
        assert owner_view.status_code == 200, owner_view.text
        assert owner_view.json()["count"] == 0
        assert client.get(f"/api/subscriptions/{sid}", headers=owner_headers).json()["policy_revision"] == 0
    finally:
        if actor is not None:
            delete_admin(access_token, actor["username"])
        if role is not None:
            client.delete(f"/api/admin-role/{role['id']}", headers=owner_headers)
        client.delete(f"/api/subscriptions/{sid}", headers=owner_headers)
        delete_group(access_token, group["id"])
        delete_core(access_token, core["id"])


def test_uuid_subscription_device_registration_limit_and_reset_workflow(access_token, core_entitlement_headers):
    """An entitled UUID subscription enforces the HWID cap through public/administrative UUID paths."""
    headers = auth_headers(access_token)
    core = create_core(access_token)
    group = create_group(access_token, inbound_tags=[get_inbounds(access_token)[0]])
    hosts = create_hosts_for_inbounds(access_token)
    sid = str(uuid4())
    created = client.post("/api/subscriptions", headers=headers, json={"uuid": sid, "group_ids": [group["id"]]})
    assert created.status_code == 201, created.text
    assert created.json()["device_limit"] is None
    try:
        enabled = client.put(
            f"/api/subscriptions/{sid}/entitlement",
            headers=core_entitlement_headers,
            json={"entitlement": "active", "revision": 1},
        )
        assert enabled.status_code == 200, enabled.text
        link = client.get(f"/api/subscriptions/{sid}/link", headers=headers)
        assert link.status_code == 200, link.text
        token = link.json()["url"].split("/sub/", 1)[-1]
        url = f"/sub/{token}"
        first = client.get(
            url,
            headers={
                "X-HWID": "uuid-device-ios-123",
                "X-Device-OS": "iOS",
                "X-Ver-OS": "16.5",
                "X-Device-Model": "iPhone 14",
            },
        )
        assert first.status_code == 200, first.text[:200]
        devices_url = f"/api/subscriptions/{sid}/devices"
        listing = client.get(devices_url, headers=headers)
        assert listing.status_code == 200, listing.text
        assert listing.json()["count"] == 1
        assert listing.json()["devices"][0]["hwid"] == "uuid-device-ios-123"
        assert listing.json()["devices"][0]["device_os"] == "iOS"
        assert listing.json()["devices"][0]["os_version"] == "16.5"
        assert listing.json()["devices"][0]["device_model"] == "iPhone 14"
        assert "id" not in listing.json()["devices"][0]

        for device in ("uuid-device-2", "uuid-device-3"):
            result = client.get(url, headers={"X-HWID": device})
            assert result.status_code == 200, (device, result.status_code, result.text[:200])
        assert client.get(devices_url, headers=headers).json()["count"] == 3
        fourth = client.get(url, headers={"X-HWID": "uuid-device-4"})
        assert fourth.status_code == 403, fourth.text[:200]

        removed = client.delete(f"{devices_url}/uuid-device-ios-123", headers=headers)
        assert removed.status_code == 200, removed.text
        assert client.get(devices_url, headers=headers).json()["count"] == 2
        allowed_after_delete = client.get(url, headers={"X-HWID": "uuid-device-4"})
        assert allowed_after_delete.status_code == 200, allowed_after_delete.text[:200]
        reset = client.post(f"{devices_url}/reset", headers=headers)
        assert reset.status_code == 200, reset.text
        assert client.get(devices_url, headers=headers).json()["count"] == 0
        details = client.get(f"/api/subscriptions/{sid}", headers=headers)
        assert details.status_code == 200
        assert details.json()["entitlement"] == "active"
        assert details.json()["policy_revision"] == 0
    finally:
        client.delete(f"/api/subscriptions/{sid}", headers=headers)
        for host in hosts:
            client.delete(f"/api/host/{host['id']}", headers=headers)
        delete_group(access_token, group["id"])
        delete_core(access_token, core["id"])
