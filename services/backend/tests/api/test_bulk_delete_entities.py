import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import status
from sqlalchemy import func, select, update

from app.db.models import (
    Admin,
    AdminNotificationReminder,
    AdminPasskey,
    AdminUsageLogs,
    APIKey,
    NextPlan,
    PasskeyChallenge,
    ReminderType,
    Subscription,
    inbounds_groups_association,
    subscriptions_groups_association,
    template_group_association,
)
from tests.api import TestSession, client
from tests.api.helpers import (
    auth_headers,
    create_admin,
    create_client_template,
    create_core,
    create_group,
    create_subscription_template,
    delete_subscription_template,
    get_inbounds,
    unique_name,
)

VALID_CERTIFICATE = """-----BEGIN CERTIFICATE-----
MIIBvTCCAWOgAwIBAgIRAIY9Lzn0T3VFedUnT9idYkEwCgYIKoZIzj0EAwIwJjER
MA8GA1UEChMIWHJheSBJbmMxETAPBgNVBAMTCFhyYXkgSW5jMB4XDTIzMDUyMTA4
NDUxMVoXDTMzMDMyOTA5NDUxMVowJjERMA8GA1UEChMIWHJheSBJbmMxETAPBgNV
BAMTCFhyYXkgSW5jMFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEGAmB8CILK7Q1
FG47g5VXg/oX3EFQqlW8B0aZAftYpHGLm4hEYVA4MasoGSxRuborhGu3lDvlt0cZ
aQTLvO/IK6NyMHAwDgYDVR0PAQH/BAQDAgWgMBMGA1UdJQQMMAoGCCsGAQUFBwMB
MAwGA1UdEwEB/wQCMAAwOwYDVR0RBDQwMoILZ3N0YXRpYy5jb22CDSouZ3N0YXRp
Yy5jb22CFCoubWV0cmljLmdzdGF0aWMuY29tMAoGCCqGSM49BAMCA0gAMEUCIQC1
XMIz1XwJrcu3BSZQFlNteutyepHrIttrtsfdd05YsQIgAtCg53wGUSSOYGL8921d
KuUcpBWSPkvH6y3Ak+YsTMg=
-----END CERTIFICATE-----"""


def set_admin_role(username: str, role_id: int) -> None:
    """Set admin role by role_id (2=administrator, 3=operator)."""

    async def _set_flag():
        async with TestSession() as session:
            await session.execute(update(Admin).where(Admin.username == username).values(role_id=role_id))
            await session.commit()

    asyncio.run(_set_flag())


def seed_admin_usage_log(admin_id: int, used_traffic: int = 1024) -> None:
    async def _seed():
        async with TestSession() as session:
            session.add(AdminUsageLogs(admin_id=admin_id, used_traffic_at_reset=used_traffic))
            await session.commit()

    asyncio.run(_seed())


def seed_api_key(admin_id: int) -> int:
    async def _seed():
        async with TestSession() as session:
            db_key = APIKey(
                admin_id=admin_id,
                name=unique_name("bulk_api_key"),
                key_hash=f"test_hash_{uuid4().hex}",
                api_key_trimmed="waylane_ab***xyz",
            )
            session.add(db_key)
            await session.commit()
            return db_key.id

    return asyncio.run(_seed())


def seed_admin_auth_rows(admin_id: int) -> bytes:
    """Give an admin a passkey, a pending passkey challenge and a usage reminder; return the credential id."""
    credential_id = f"cred-{uuid4().hex}".encode()

    async def _seed():
        async with TestSession() as session:
            session.add(AdminPasskey(admin_id=admin_id, credential_id=credential_id, public_key=b"public-key"))
            session.add(
                PasskeyChallenge(
                    challenge=uuid4().bytes,
                    kind="login",
                    expires_at=datetime.now(UTC) + timedelta(minutes=5),
                    admin_id=admin_id,
                )
            )
            session.add(AdminNotificationReminder(admin_id=admin_id, type=ReminderType.data_usage, threshold=80))
            await session.commit()

    asyncio.run(_seed())
    return credential_id


def count_admin_auth_rows(admin_id: int) -> dict[str, int]:
    async def _count():
        async with TestSession() as session:
            counts = {}
            for name, model in (
                ("passkeys", AdminPasskey),
                ("challenges", PasskeyChallenge),
                ("reminders", AdminNotificationReminder),
            ):
                counts[name] = (
                    await session.execute(select(func.count()).select_from(model).where(model.admin_id == admin_id))
                ).scalar_one()
            return counts

    return asyncio.run(_count())


def get_passkey_admin_id(credential_id: bytes) -> int | None:
    async def _get():
        async with TestSession() as session:
            result = await session.execute(
                select(AdminPasskey.admin_id).where(AdminPasskey.credential_id == credential_id)
            )
            return result.scalar_one_or_none()

    return asyncio.run(_get())


def delete_admin_if_present(access_token: str, username: str) -> None:
    response = client.delete(f"/api/admin/{username}", headers=auth_headers(access_token))
    assert response.status_code in (status.HTTP_204_NO_CONTENT, status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND)


def create_owner_admin_row() -> dict:
    async def _create():
        async with TestSession() as session:
            db_admin = Admin(username=unique_name("bulk_owner"), hashed_password="secret", role_id=1)
            session.add(db_admin)
            await session.commit()
            return {"id": db_admin.id, "username": db_admin.username}

    return asyncio.run(_create())


def delete_admin_row(username: str) -> None:
    async def _delete():
        async with TestSession() as session:
            result = await session.execute(select(Admin).where(Admin.username == username))
            db_admin = result.scalar_one_or_none()
            if db_admin is not None:
                await session.delete(db_admin)
                await session.commit()

    asyncio.run(_delete())


def delete_core_if_present(access_token: str, core_id: int) -> None:
    response = client.delete(f"/api/core/{core_id}", headers=auth_headers(access_token))
    assert response.status_code in (status.HTTP_204_NO_CONTENT, status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND)


def delete_group_if_present(access_token: str, group_id: int) -> None:
    response = client.delete(f"/api/group/{group_id}", headers=auth_headers(access_token))
    assert response.status_code in (status.HTTP_204_NO_CONTENT, status.HTTP_404_NOT_FOUND)


def count_admin_usage_logs(admin_id: int) -> int:
    async def _count():
        async with TestSession() as session:
            result = await session.execute(
                select(func.count()).select_from(AdminUsageLogs).where(AdminUsageLogs.admin_id == admin_id)
            )
            return result.scalar_one()

    return asyncio.run(_count())


def count_api_keys(admin_id: int) -> int:
    async def _count():
        async with TestSession() as session:
            result = await session.execute(select(func.count()).select_from(APIKey).where(APIKey.admin_id == admin_id))
            return result.scalar_one()

    return asyncio.run(_count())














def get_group_association_counts(group_id: int) -> dict[str, int]:
    async def _counts():
        async with TestSession() as session:
            return {
                "subscriptions": (
                    await session.execute(
                        select(func.count())
                        .select_from(subscriptions_groups_association)
                        .where(subscriptions_groups_association.c.groups_id == group_id)
                    )
                ).scalar_one(),
                "templates": (
                    await session.execute(
                        select(func.count())
                        .select_from(template_group_association)
                        .where(template_group_association.c.group_id == group_id)
                    )
                ).scalar_one(),
                "inbounds": (
                    await session.execute(
                        select(func.count())
                        .select_from(inbounds_groups_association)
                        .where(inbounds_groups_association.c.group_id == group_id)
                    )
                ).scalar_one(),
            }

    return asyncio.run(_counts())


def create_next_plan(subscription_uuid: UUID, subscription_template_id: int) -> None:
    async def _create():
        async with TestSession() as session:
            session.add(
                NextPlan(
                    subscription_uuid=subscription_uuid,
                    subscription_template_id=subscription_template_id,
                    data_limit=2048,
                    expire=3600,
                    add_remaining_traffic=False,
                )
            )
            await session.commit()

    asyncio.run(_create())


def get_next_plan_state(subscription_uuid: UUID) -> dict | None:
    async def _get():
        async with TestSession() as session:
            result = await session.execute(
                select(NextPlan.id, NextPlan.subscription_template_id).where(NextPlan.subscription_uuid == subscription_uuid)
            )
            row = result.one_or_none()
            return None if row is None else {"exists": True, "subscription_template_id": row.subscription_template_id}

    return asyncio.run(_get())


def get_template_group_link_count(template_id: int) -> int:
    async def _count():
        async with TestSession() as session:
            result = await session.execute(
                select(func.count())
                .select_from(template_group_association)
                .where(template_group_association.c.subscription_template_id == template_id)
            )
            return result.scalar_one()

    return asyncio.run(_count())


def test_bulk_delete_admins_removes_passkeys_challenges_and_reminders(access_token):
    admin = create_admin(access_token)
    credential_id = seed_admin_auth_rows(admin["id"])
    replacement = None
    try:
        response = client.post(
            "/api/admins/bulk/delete",
            headers=auth_headers(access_token),
            json={"ids": [admin["id"]]},
        )

        assert response.status_code == status.HTTP_200_OK
        assert count_admin_auth_rows(admin["id"]) == {"passkeys": 0, "challenges": 0, "reminders": 0}
        assert get_passkey_admin_id(credential_id) is None

        # SQLite can hand the freed id to the next admin; it must not inherit the deleted admin's passkey.
        replacement = create_admin(access_token)
        assert count_admin_auth_rows(replacement["id"]) == {"passkeys": 0, "challenges": 0, "reminders": 0}
        listed = client.get(f"/api/admin/passkey/admins/{replacement['id']}", headers=auth_headers(access_token))
        assert listed.status_code == status.HTTP_200_OK
        assert listed.json() == []
    finally:
        delete_admin_if_present(access_token, admin["username"])
        if replacement is not None:
            delete_admin_if_present(access_token, replacement["username"])


def test_delete_admin_removes_passkeys_challenges_and_reminders(access_token):
    admin = create_admin(access_token)
    credential_id = seed_admin_auth_rows(admin["id"])
    try:
        response = client.delete(f"/api/admin/by-id/{admin['id']}", headers=auth_headers(access_token))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert count_admin_auth_rows(admin["id"]) == {"passkeys": 0, "challenges": 0, "reminders": 0}
        assert get_passkey_admin_id(credential_id) is None
    finally:
        delete_admin_if_present(access_token, admin["username"])


def test_bulk_delete_admins_rejects_owner_account(access_token):
    """Bulk deleting the owner admin (role_id=1) should be forbidden."""
    owner = create_owner_admin_row()
    try:
        response = client.post(
            "/api/admins/bulk/delete",
            headers=auth_headers(access_token),
            json={"ids": [owner["id"]]},
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

        async def _owner_exists():
            async with TestSession() as session:
                result = await session.execute(select(Admin.id).where(Admin.id == owner["id"]))
                return result.scalar_one_or_none() is not None

        assert asyncio.run(_owner_exists())
    finally:
        delete_admin_row(owner["username"])


def test_bulk_delete_client_templates_reassigns_default(access_token):
    create_client_template(
        access_token,
        name=unique_name("bulk_client_system"),
        template_type="user_agent",
        content='{"list": ["agent-one"]}',
    )
    second = create_client_template(
        access_token,
        name=unique_name("bulk_client_default"),
        template_type="user_agent",
        content='{"list": ["agent-two"]}',
        is_default=True,
    )

    response = client.post(
        "/api/client_templates/bulk/delete",
        headers=auth_headers(access_token),
        json={"ids": [second["id"]]},
    )

    assert response.status_code == status.HTTP_200_OK
    templates_response = client.get(
        "/api/client_templates",
        headers=auth_headers(access_token),
        params={"template_type": "user_agent"},
    )
    assert templates_response.status_code == status.HTTP_200_OK
    remaining = templates_response.json()["templates"]
    assert second["id"] not in {template["id"] for template in remaining}
    assert len([template for template in remaining if template["is_default"]]) == 1


def test_bulk_delete_client_templates_clears_associated_host_overrides(access_token):
    core = create_core(access_token, name=unique_name("bulk_client_host_core"))
    inbounds = get_inbounds(access_token)
    assert inbounds, "No inbounds available for host template bulk cleanup test"
    target = create_client_template(
        access_token,
        name=unique_name("bulk_client_host_cleanup"),
        template_type="xray_subscription",
        content='{"inbounds":[{"tag":"placeholder","protocol":"vmess","settings":{"clients":[]}}],"outbounds":[{"tag":"bulk-cleanup-template-marker","protocol":"freedom","settings":{}}]}',
    )
    host_id = None
    try:
        create_response = client.post(
            "/api/host",
            headers=auth_headers(access_token),
            json={
                "remark": unique_name("bulk_host_template_cleanup"),
                "address": ["127.0.0.1"],
                "port": 443,
                "inbound_tag": inbounds[0],
                "priority": 1,
                "subscription_templates": {"xray": target["id"]},
            },
        )
        assert create_response.status_code == status.HTTP_201_CREATED
        host_id = create_response.json()["id"]

        response = client.post(
            "/api/client_templates/bulk/delete",
            headers=auth_headers(access_token),
            json={"ids": [target["id"]]},
        )

        assert response.status_code == status.HTTP_200_OK
        host_response = client.get(f"/api/host/{host_id}", headers=auth_headers(access_token))
        assert host_response.status_code == status.HTTP_200_OK
        assert host_response.json()["subscription_templates"] is None
    finally:
        if host_id is not None:
            client.delete(f"/api/host/{host_id}", headers=auth_headers(access_token))
        delete_core_if_present(access_token, core["id"])


def test_bulk_delete_client_templates_rejects_system_template(access_token):
    templates_response = client.get("/api/client_templates", headers=auth_headers(access_token))
    assert templates_response.status_code == status.HTTP_200_OK
    system_template = next(
        (template for template in templates_response.json()["templates"] if template["is_system"]),
        None,
    )

    if system_template is None:
        system_template = create_client_template(
            access_token,
            name=unique_name("bulk_system_template"),
            template_type="grpc_user_agent",
            content='{"list": ["grpc-agent"]}',
        )

    response = client.post(
        "/api/client_templates/bulk/delete",
        headers=auth_headers(access_token),
        json={"ids": [system_template["id"]]},
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN








def test_bulk_delete_cores_rejects_default_core(access_token):
    response = client.post(
        "/api/cores/bulk/delete",
        headers=auth_headers(access_token),
        json={"ids": [1]},
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_bulk_delete_hosts_removes_selected_hosts(access_token):
    core = create_core(access_token, name=unique_name("bulk_host_core"))
    inbounds = get_inbounds(access_token)
    host_ids = []
    try:
        for index, inbound in enumerate(inbounds[:2], start=1):
            response = client.post(
                "/api/host",
                headers=auth_headers(access_token),
                json={
                    "remark": unique_name(f"bulk_host_{index}"),
                    "address": ["127.0.0.1"],
                    "port": 443,
                    "sni": [f"bulk-host-{index}.example.com"],
                    "inbound_tag": inbound,
                    "priority": index,
                },
            )
            assert response.status_code == status.HTTP_201_CREATED
            host_ids.append(response.json()["id"])

        response = client.post(
            "/api/hosts/bulk/delete",
            headers=auth_headers(access_token),
            json={"ids": host_ids},
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["count"] == len(host_ids)
        assert set(response.json()["hosts"]) == {str(host_id) for host_id in host_ids}

        hosts_response = client.get("/api/hosts", headers=auth_headers(access_token))
        assert hosts_response.status_code == status.HTTP_200_OK
        assert not ({host["id"] for host in hosts_response.json()} & set(host_ids))
    finally:
        delete_core_if_present(access_token, core["id"])


def seed_internal_subscription_for_usage() -> UUID:
    """Seed an unbound technical subscription to verify internal cascade behavior."""

    async def _seed() -> UUID:
        async with TestSession() as session:
            subscription = Subscription(uuid=uuid4())
            session.add(subscription)
            await session.commit()
            return subscription.uuid

    return asyncio.run(_seed())


def delete_internal_subscription(subscription_uuid: UUID) -> None:
    async def _delete() -> None:
        async with TestSession() as session:
            subscription = await session.get(Subscription, subscription_uuid)
            if subscription is not None:
                await session.delete(subscription)
                await session.commit()

    asyncio.run(_delete())


def test_bulk_delete_admins_clears_usage_logs_and_api_keys(access_token):
    admin = create_admin(access_token)
    try:
        seed_admin_usage_log(admin["id"])
        seed_api_key(admin["id"])
        assert count_admin_usage_logs(admin["id"]) == 1
        assert count_api_keys(admin["id"]) == 1
        response = client.post(
            "/api/admins/bulk/delete", headers=auth_headers(access_token), json={"ids": [admin["id"]]}
        )
        assert response.status_code == status.HTTP_200_OK, response.text
        assert response.json()["count"] == 1
        assert count_admin_usage_logs(admin["id"]) == 0
        assert count_api_keys(admin["id"]) == 0
    finally:
        delete_admin_if_present(access_token, admin["username"])


def test_bulk_delete_unassigned_group_cleans_template_and_inbound_links(access_token):
    core = create_core(access_token, name=unique_name("bulk_group_core"))
    group = create_group(access_token, name=unique_name("bulk_group"))
    template = create_subscription_template(
        access_token, name=unique_name("bulk_group_template"), group_ids=[group["id"]]
    )
    try:
        before = get_group_association_counts(group["id"])
        assert before["subscriptions"] == 0
        assert before["templates"] == 1
        assert before["inbounds"] > 0
        response = client.post(
            "/api/groups/bulk/delete", headers=auth_headers(access_token), json={"ids": [group["id"]]}
        )
        assert response.status_code == status.HTTP_200_OK, response.text
        assert response.json()["count"] == 1
        assert get_group_association_counts(group["id"]) == {"subscriptions": 0, "templates": 0, "inbounds": 0}
    finally:
        delete_subscription_template(access_token, template["id"])
        delete_group_if_present(access_token, group["id"])
        delete_core_if_present(access_token, core["id"])




def test_bulk_delete_subscription_templates_cleans_legacy_next_plan_records(access_token):
    """Until internal NextPlan is retired, deleting its template must delete its plan."""
    core = create_core(access_token, name=unique_name("bulk_template_core"))
    group = create_group(access_token, name=unique_name("bulk_template_group"))
    subscription_uuid = seed_internal_subscription_for_usage()
    template = create_subscription_template(access_token, name=unique_name("bulk_template"), group_ids=[group["id"]])
    create_next_plan(subscription_uuid, template["id"])
    try:
        assert get_template_group_link_count(template["id"]) == 1
        assert get_next_plan_state(subscription_uuid) == {"exists": True, "subscription_template_id": template["id"]}
        response = client.post(
            "/api/subscription_templates/bulk/delete",
            headers=auth_headers(access_token),
            json={"ids": [template["id"]]},
        )
        assert response.status_code == status.HTTP_200_OK, response.text
        assert response.json()["count"] == 1
        assert get_template_group_link_count(template["id"]) == 0
        assert get_next_plan_state(subscription_uuid) is None
    finally:
        delete_internal_subscription(subscription_uuid)
        cleanup_response = client.delete(
            f"/api/subscription_template/{template['id']}", headers=auth_headers(access_token)
        )
        assert cleanup_response.status_code in (204, 404)
        delete_group_if_present(access_token, group["id"])
        delete_core_if_present(access_token, core["id"])
