import asyncio
from uuid import uuid4

from fastapi import status
from sqlalchemy import func, select, update

from app.db.models import Admin, AdminUsageLogs
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








def set_admin_used_traffic(username: str, used_traffic: int) -> None:
    async def _set():
        async with TestSession() as session:
            await session.execute(update(Admin).where(Admin.username == username).values(used_traffic=used_traffic))
            await session.commit()

    asyncio.run(_set())


def count_admin_usage_logs(admin_id: int) -> int:
    async def _count():
        async with TestSession() as session:
            result = await session.execute(
                select(func.count()).select_from(AdminUsageLogs).where(AdminUsageLogs.admin_id == admin_id)
            )
            return result.scalar_one()

    return asyncio.run(_count())


def get_admin_details(access_token: str, username: str) -> dict:
    response = client.get(
        "/api/admins",
        headers=auth_headers(access_token),
        params={"username": username},
    )
    assert response.status_code == status.HTTP_200_OK
    return next(admin for admin in response.json()["admins"] if admin["username"] == username)


def delete_host_if_present(access_token: str, host_id: int) -> None:
    response = client.delete(f"/api/host/{host_id}", headers=auth_headers(access_token))
    assert response.status_code in (status.HTTP_204_NO_CONTENT, status.HTTP_404_NOT_FOUND)


def valid_group_name(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:8]}"


def test_bulk_disable_and_enable_groups(access_token):
    core = create_core(access_token, name=unique_name("bulk_groups_core"))
    group_one = create_group(access_token, name=valid_group_name("bulkgroupone"))
    group_two = create_group(access_token, name=valid_group_name("bulkgrouptwo"))
    try:
        response = client.post(
            "/api/groups/bulk/disable",
            headers=auth_headers(access_token),
            json={"ids": [group_one["id"], group_two["id"]]},
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["count"] == 2

        for group_id in (group_one["id"], group_two["id"]):
            group_response = client.get(f"/api/group/{group_id}", headers=auth_headers(access_token))
            assert group_response.status_code == status.HTTP_200_OK
            assert group_response.json()["is_disabled"] is True

        response = client.post(
            "/api/groups/bulk/enable",
            headers=auth_headers(access_token),
            json={"ids": [group_one["id"], group_two["id"]]},
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["count"] == 2

        for group_id in (group_one["id"], group_two["id"]):
            group_response = client.get(f"/api/group/{group_id}", headers=auth_headers(access_token))
            assert group_response.status_code == status.HTTP_200_OK
            assert group_response.json()["is_disabled"] is False
    finally:
        delete_group(access_token, group_one["id"])
        delete_group(access_token, group_two["id"])
        delete_core(access_token, core["id"])


def test_bulk_disable_and_enable_hosts(access_token):
    core = create_core(access_token, name=unique_name("bulk_hosts_core"))
    hosts = create_hosts_for_inbounds(access_token)[:2]
    try:
        response = client.post(
            "/api/hosts/bulk/disable",
            headers=auth_headers(access_token),
            json={"ids": [host["id"] for host in hosts]},
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["count"] == len(hosts)

        for host in hosts:
            host_response = client.get(f"/api/host/{host['id']}", headers=auth_headers(access_token))
            assert host_response.status_code == status.HTTP_200_OK
            assert host_response.json()["is_disabled"] is True

        response = client.post(
            "/api/hosts/bulk/enable",
            headers=auth_headers(access_token),
            json={"ids": [host["id"] for host in hosts]},
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["count"] == len(hosts)

        for host in hosts:
            host_response = client.get(f"/api/host/{host['id']}", headers=auth_headers(access_token))
            assert host_response.status_code == status.HTTP_200_OK
            assert host_response.json()["is_disabled"] is False
    finally:
        for host in hosts:
            delete_host_if_present(access_token, host["id"])
        delete_core(access_token, core["id"])


def test_bulk_disable_and_enable_subscription_templates(access_token):
    core = create_core(access_token, name=unique_name("bulk_templates_core"))
    group = create_group(access_token, name=valid_group_name("bulktemplategroup"))
    template = create_subscription_template(
        access_token,
        name=unique_name("bulk_template"),
        group_ids=[group["id"]],
    )
    try:
        response = client.post(
            "/api/subscription_templates/bulk/disable",
            headers=auth_headers(access_token),
            json={"ids": [template["id"]]},
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["count"] == 1

        template_response = client.get(
            f"/api/subscription_template/{template['id']}", headers=auth_headers(access_token)
        )
        assert template_response.status_code == status.HTTP_200_OK
        assert template_response.json()["is_disabled"] is True

        response = client.post(
            "/api/subscription_templates/bulk/enable",
            headers=auth_headers(access_token),
            json={"ids": [template["id"]]},
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["count"] == 1

        template_response = client.get(
            f"/api/subscription_template/{template['id']}", headers=auth_headers(access_token)
        )
        assert template_response.status_code == status.HTTP_200_OK
        assert template_response.json()["is_disabled"] is False
    finally:
        delete_subscription_template(access_token, template["id"])
        delete_group(access_token, group["id"])
        delete_core(access_token, core["id"])




def test_bulk_disable_enable_and_reset_admins(access_token):
    admin = create_admin(access_token)
    try:
        set_admin_used_traffic(admin["username"], 8192)

        response = client.post(
            "/api/admins/bulk/reset",
            headers=auth_headers(access_token),
            json={"ids": [admin["id"]]},
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["count"] == 1
        assert get_admin_details(access_token, admin["username"])["used_traffic"] == 0
        assert count_admin_usage_logs(admin["id"]) == 1

        response = client.post(
            "/api/admins/bulk/disable",
            headers=auth_headers(access_token),
            json={"ids": [admin["id"]]},
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["count"] == 1
        assert get_admin_details(access_token, admin["username"])["status"] == "disabled"

        response = client.post(
            "/api/admins/bulk/enable",
            headers=auth_headers(access_token),
            json={"ids": [admin["id"]]},
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["count"] == 1
        assert get_admin_details(access_token, admin["username"])["status"] == "active"
    finally:
        delete_admin(access_token, admin["username"])
