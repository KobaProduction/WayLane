"""Regression tests for RBAC permission leaks."""

import asyncio
from uuid import uuid4

import pytest
from fastapi import status
from sqlalchemy import select

from app.db.models import Admin
from app.models.admin import hash_password
from tests.api import TestSession, client
from tests.api.helpers import (
    auth_headers,
    create_admin,
    create_core,
    create_group,
    create_subscription_template,
    delete_admin,
    delete_core,
    delete_group,
    strong_password,
    unique_name,
)

SCOPE_OWN = {"scope": 1}
SCOPE_ALL = {"scope": 2}


def _login(username: str, password: str) -> str:
    response = client.post(
        "/api/admin/token",
        data={"username": username, "password": password, "grant_type": "password"},
    )
    assert response.status_code == status.HTTP_200_OK
    return response.json()["access_token"]


def _create_role(access_token: str, permissions: dict, access: dict | None = None) -> dict:
    response = client.post(
        "/api/admin-role",
        headers=auth_headers(access_token),
        json={
            "name": unique_name("role_leak"),
            "permissions": permissions,
            "limits": {},
            "features": {},
            "access": access or {},
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


def _delete_role(access_token: str, role_id: int) -> None:
    client.delete(f"/api/admin-role/{role_id}", headers=auth_headers(access_token))


def _create_owner_admin() -> dict:
    username = unique_name("owner_key")
    password = strong_password("OwnerKey")

    async def _create() -> int:
        async with TestSession() as session:
            db_admin = Admin(username=username, hashed_password=await hash_password(password), role_id=1)
            session.add(db_admin)
            await session.commit()
            await session.refresh(db_admin)
            return db_admin.id

    return {"id": asyncio.run(_create()), "username": username, "password": password}


def _delete_admin_row(username: str) -> None:
    async def _delete() -> None:
        async with TestSession() as session:
            db_admin = (await session.execute(select(Admin).where(Admin.username == username))).scalar_one_or_none()
            if db_admin is not None:
                await session.delete(db_admin)
                await session.commit()

    asyncio.run(_delete())


def _create_api_key(token: str, **payload) -> str:
    response = client.post(
        "/api/api_key",
        headers=auth_headers(token),
        json={"name": unique_name("api_key"), **payload},
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["api_key"]


# --- API keys with custom (non-inherited) permissions ---


def test_api_key_with_empty_custom_permissions_grants_nothing(access_token):
    admin = create_admin(access_token, role_id=2)
    try:
        raw_key = _create_api_key(
            _login(admin["username"], admin["password"]),
            inherit_permissions=False,
            permissions={},
        )

        response = client.get("/api/subscriptions", headers={"X-Api-Key": raw_key})

        assert response.status_code == status.HTTP_403_FORBIDDEN
    finally:
        delete_admin(access_token, admin["username"])


def test_owner_api_key_with_empty_custom_permissions_is_not_owner(access_token):
    owner = _create_owner_admin()
    try:
        raw_key = _create_api_key(
            _login(owner["username"], owner["password"]),
            inherit_permissions=False,
            permissions={},
        )

        response = client.get("/api/admin-roles", headers={"X-Api-Key": raw_key})

        assert response.status_code == status.HTTP_403_FORBIDDEN
    finally:
        _delete_admin_row(owner["username"])


# --- UUID subscription RBAC action scope must not leak foreign subscriptions ---

# Can inspect all UUID subscriptions but mutate only those owned by this admin.
READ_ALL_WRITE_OWN = {
    "subscriptions": {
        "create": True,
        "read": SCOPE_ALL,
        "update": SCOPE_OWN,
        "delete": SCOPE_OWN,
        "reset_usage": SCOPE_OWN,
        "revoke_sub": SCOPE_OWN,
    }
}


@pytest.fixture
def scoped_actor(access_token):
    """One foreign Core-managed UUID and an actor with caller-provided RBAC grants."""
    core = create_core(access_token)
    group = create_group(access_token)
    created = []

    def _build(permissions: dict) -> dict:
        role = _create_role(access_token, permissions)
        actor = create_admin(access_token, role_id=role["id"])
        sid = str(uuid4())
        response = client.post(
            "/api/subscriptions",
            headers=auth_headers(access_token),
            json={"uuid": sid, "group_ids": [group["id"]]},
        )
        assert response.status_code == status.HTTP_201_CREATED, response.text
        victim = {"uuid": sid}
        created.append((role, actor, victim))
        return {
            "actor": actor,
            "token": _login(actor["username"], actor["password"]),
            "victim": victim,
            "group_id": group["id"],
        }

    yield _build

    for role, actor, victim in created:
        client.delete(f"/api/subscriptions/{victim['uuid']}", headers=auth_headers(access_token))
        delete_admin(access_token, actor["username"])
        _delete_role(access_token, role["id"])
    delete_group(access_token, group["id"])
    delete_core(access_token, core["id"])


def _api_key_actor(scoped_actor):
    return scoped_actor(
        {
            "subscriptions": {"read": SCOPE_OWN, "read_simple": SCOPE_OWN},
            "api_keys": {"create": True, "read": True, "update": True},
        }
    )


def test_api_key_patch_cannot_carry_permissions_beyond_the_admin(scoped_actor):
    context = _api_key_actor(scoped_actor)
    headers = auth_headers(context["token"])
    created = client.post("/api/api_key", headers=headers, json={"name": unique_name("api_key")})
    assert created.status_code == status.HTTP_201_CREATED, created.text
    key_id, raw_key = created.json()["id"], created.json()["api_key"]

    # Permissions sent while the key still inherits are accepted but not stored...
    patched = client.patch(
        f"/api/api_key/{key_id}",
        headers=headers,
        json={"permissions": {"subscriptions": {"read": True, "read_simple": True}}},
    )
    assert patched.status_code == status.HTTP_200_OK, patched.text
    stored = client.get(f"/api/api_key/{key_id}", headers=headers).json()["permissions"]
    assert all(value is None for value in stored.values()), stored

    # ...so switching inheritance off afterwards leaves a key with no permissions at all.
    switched = client.patch(f"/api/api_key/{key_id}", headers=headers, json={"inherit_permissions": False})
    assert switched.status_code == status.HTTP_200_OK, switched.text
    response = client.get("/api/subscriptions", headers={"X-Api-Key": raw_key})

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_api_key_patch_with_null_permissions_keeps_the_key_working(scoped_actor):
    context = _api_key_actor(scoped_actor)
    headers = auth_headers(context["token"])
    created = client.post(
        "/api/api_key",
        headers=headers,
        json={
            "name": unique_name("api_key"),
            "inherit_permissions": False,
            "permissions": {"subscriptions": {"read": SCOPE_OWN}},
        },
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    key_id, raw_key = created.json()["id"], created.json()["api_key"]

    renamed = client.patch(
        f"/api/api_key/{key_id}", headers=headers, json={"name": unique_name("renamed"), "permissions": None}
    )
    assert renamed.status_code == status.HTTP_200_OK, renamed.text
    assert renamed.json()["permissions"] == created.json()["permissions"]

    assert client.get("/api/admin", headers={"X-Api-Key": raw_key}).status_code == status.HTTP_200_OK


# --- Group / template access lists ---


@pytest.fixture
def restricted_catalog(access_token):
    """Two groups and two templates; a restricted admin may only see the first of each."""
    core = create_core(access_token)
    allowed_group = create_group(access_token)
    hidden_group = create_group(access_token)
    allowed_template = create_subscription_template(access_token, group_ids=[allowed_group["id"]])
    hidden_template = create_subscription_template(access_token, group_ids=[hidden_group["id"]])
    role = _create_role(
        access_token,
        {
            "groups": {"read": True, "read_simple": True},
            "templates": {"read": True, "read_simple": True, "update": True, "delete": True},
        },
        access={
            "allowed_group_ids": [allowed_group["id"]],
            "allowed_template_ids": [allowed_template["id"]],
        },
    )
    admin = create_admin(access_token, role_id=role["id"])
    try:
        yield {
            "token": _login(admin["username"], admin["password"]),
            "allowed_group": allowed_group,
            "hidden_group": hidden_group,
            "allowed_template": allowed_template,
            "hidden_template": hidden_template,
        }
    finally:
        delete_admin(access_token, admin["username"])
        _delete_role(access_token, role["id"])
        for template in (allowed_template, hidden_template):
            client.delete(f"/api/subscription_template/{template['id']}", headers=auth_headers(access_token))
        for group in (allowed_group, hidden_group):
            delete_group(access_token, group["id"])
        delete_core(access_token, core["id"])


@pytest.mark.parametrize(
    ("path", "item", "list_key"),
    [
        pytest.param("/api/groups", "hidden_group", "groups", id="groups"),
        pytest.param("/api/groups/simple", "hidden_group", "groups", id="groups-simple"),
        pytest.param("/api/subscription_templates", "hidden_template", None, id="templates"),
        pytest.param("/api/subscription_templates/simple", "hidden_template", "templates", id="templates-simple"),
    ],
)
def test_listing_only_disallowed_ids_returns_nothing(restricted_catalog, path, item, list_key):
    response = client.get(
        path,
        headers=auth_headers(restricted_catalog["token"]),
        params={"ids": [restricted_catalog[item]["id"]]},
    )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    items = body if list_key is None else body[list_key]
    assert items == []
    if list_key is not None:
        assert body["total"] == 0


@pytest.mark.parametrize("action", ["delete", "disable"])
def test_bulk_template_action_on_disallowed_ids_touches_nothing(access_token, restricted_catalog, action):
    response = client.post(
        f"/api/subscription_templates/bulk/{action}",
        headers=auth_headers(restricted_catalog["token"]),
        json={"ids": [restricted_catalog["hidden_template"]["id"]]},
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    for key in ("allowed_template", "hidden_template"):
        template = client.get(
            f"/api/subscription_template/{restricted_catalog[key]['id']}",
            headers=auth_headers(access_token),
        )
        assert template.status_code == status.HTTP_200_OK
        assert template.json()["is_disabled"] is False


def test_role_with_empty_group_allowlist_lists_no_groups(access_token):
    core = create_core(access_token)
    group = create_group(access_token)
    role = _create_role(
        access_token,
        {"groups": {"read": True}},
        access={"allowed_group_ids": []},
    )
    admin = create_admin(access_token, role_id=role["id"])
    try:
        response = client.get("/api/groups", headers=auth_headers(_login(admin["username"], admin["password"])))

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["groups"] == []
        assert response.json()["total"] == 0
    finally:
        delete_admin(access_token, admin["username"])
        _delete_role(access_token, role["id"])
        delete_group(access_token, group["id"])
        delete_core(access_token, core["id"])


@pytest.mark.parametrize(
    ("kind", "bulk"),
    [
        pytest.param("group", False, id="group"),
        pytest.param("group", True, id="group-bulk"),
        pytest.param("template", False, id="template"),
        pytest.param("template", True, id="template-bulk"),
    ],
)
def test_deleting_group_or_template_drops_it_from_role_allowlists(access_token, kind, bulk):
    core = create_core(access_token)
    kept_group = create_group(access_token)
    created = [("group", kept_group["id"])]

    def create_item() -> dict:
        if kind == "group":
            item = create_group(access_token)
        else:
            item = create_subscription_template(access_token, group_ids=[kept_group["id"]])
        created.append((kind, item["id"]))
        return item

    kept = kept_group if kind == "group" else create_item()
    target = create_item()  # the newest row, so SQLite hands its id to the next insert
    field = "allowed_group_ids" if kind == "group" else "allowed_template_ids"
    resource = "groups" if kind == "group" else "templates"
    role = _create_role(
        access_token, {resource: {"read": True, "read_simple": True}}, access={field: [kept["id"], target["id"]]}
    )
    only_target_role = _create_role(access_token, {resource: {"read": True}}, access={field: [target["id"]]})
    admin = create_admin(access_token, role_id=role["id"])
    try:
        token = _login(admin["username"], admin["password"])
        item_path = "/api/group" if kind == "group" else "/api/subscription_template"
        bulk_path = "/api/groups/bulk/delete" if kind == "group" else "/api/subscription_templates/bulk/delete"
        if bulk:
            response = client.post(bulk_path, headers=auth_headers(access_token), json={"ids": [target["id"]]})
            assert response.status_code == status.HTTP_200_OK
        else:
            response = client.delete(f"{item_path}/{target['id']}", headers=auth_headers(access_token))
            assert response.status_code == status.HTTP_204_NO_CONTENT
        created.remove((kind, target["id"]))

        def allowlist(role_id: int) -> list[int] | None:
            stored = client.get(f"/api/admin-role/{role_id}", headers=auth_headers(access_token)).json()
            return stored["access"][field]

        assert allowlist(role["id"]) == [kept["id"]]
        # an allowlist left empty still allows nothing; it must not turn into "everything"
        assert allowlist(only_target_role["id"]) == []

        newcomer = create_item()
        assert client.get(f"{item_path}/{newcomer['id']}", headers=auth_headers(token)).status_code != 200
        assert client.get(f"{item_path}/{kept['id']}", headers=auth_headers(token)).status_code == 200
    finally:
        delete_admin(access_token, admin["username"])
        _delete_role(access_token, role["id"])
        _delete_role(access_token, only_target_role["id"])
        for item_kind, item_id in reversed(created):
            path = "/api/group" if item_kind == "group" else "/api/subscription_template"
            client.delete(f"{path}/{item_id}", headers=auth_headers(access_token))
        delete_core(access_token, core["id"])


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        pytest.param("DELETE", "/api/subscriptions/{uuid}", None, id="delete-own-only"),
        pytest.param("POST", "/api/subscriptions/{uuid}/rotate-access", None, id="rotate-own-only"),
        pytest.param("POST", "/api/subscriptions/{uuid}/reissue", None, id="reissue-own-only"),
        pytest.param("POST", "/api/subscriptions/bulk/disable", "selection", id="disable-bulk-own-only"),
        pytest.param("POST", "/api/subscriptions/bulk/delete", "selection", id="delete-bulk-own-only"),
    ],
)
def test_uuid_subscription_mutations_do_not_cross_admin_action_scope(scoped_actor, method, path, body):
    context = scoped_actor(READ_ALL_WRITE_OWN)
    sid = context["victim"]["uuid"]
    payload = {"uuids": [sid]} if body == "selection" else None
    response = client.request(method, path.format(uuid=sid), headers=auth_headers(context["token"]), json=payload)
    assert response.status_code == 404, response.text
    # This admin can still inspect, but could not change the foreign record.
    details = client.get(f"/api/subscriptions/{sid}", headers=auth_headers(context["token"]))
    assert details.status_code == 200
    assert details.json()["technical_status"] == "active"
    assert details.json()["entitlement"] == "payment_required"
    assert details.json()["policy_revision"] == 0


def test_scoped_admin_can_rotate_reissue_and_delete_only_own_uuid_subscription(scoped_actor):
    context = scoped_actor(READ_ALL_WRITE_OWN)
    sid = str(uuid4())
    headers = auth_headers(context["token"])
    try:
        created = client.post(
            "/api/subscriptions",
            headers=headers,
            json={"uuid": sid, "group_ids": [context["group_id"]]},
        )
        assert created.status_code == 201, created.text
        assert created.json()["entitlement"] == "payment_required"
        rotated = client.post(f"/api/subscriptions/{sid}/rotate-access", headers=headers)
        assert rotated.status_code == 200, rotated.text
        reissued = client.post(f"/api/subscriptions/{sid}/reissue", headers=headers)
        assert reissued.status_code == 200, reissued.text
        details = client.get(f"/api/subscriptions/{sid}", headers=headers)
        assert details.status_code == 200, details.text
        assert details.json()["entitlement"] == "payment_required"
        deleted = client.delete(f"/api/subscriptions/{sid}", headers=headers)
        assert deleted.status_code == 204, deleted.text
        assert client.get(f"/api/subscriptions/{sid}", headers=headers).status_code == 404
    finally:
        client.delete(f"/api/subscriptions/{sid}", headers=headers)


@pytest.mark.parametrize(
    ("read_scope", "simple_scope", "victim_visible"),
    [
        pytest.param(SCOPE_OWN, SCOPE_ALL, False, id="read-own-simple-all"),
        pytest.param(SCOPE_OWN, None, False, id="read-own-no-simple"),
        pytest.param(SCOPE_ALL, SCOPE_OWN, True, id="read-all-simple-own"),
    ],
)
def test_uuid_subscription_list_uses_read_permission_scope_not_simple_scope(
    scoped_actor, read_scope, simple_scope, victim_visible
):
    permissions = {"subscriptions": {"read": read_scope}}
    if simple_scope is not None:
        permissions["subscriptions"]["read_simple"] = simple_scope
    context = scoped_actor(permissions)
    response = client.get("/api/subscriptions", headers=auth_headers(context["token"]), params={"limit": 100})
    assert response.status_code == 200, response.text
    uuids = {item["uuid"] for item in response.json()["items"]}
    assert (context["victim"]["uuid"] in uuids) is victim_visible
    assert all("id" not in item and "username" not in item for item in response.json()["items"])
