import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import aiocache
import pytest
from sqlalchemy import select

from app.db.models import Admin, Settings
from app.models.admin import hash_password

from . import GetTestDB, TestSession, client
from .helpers import auth_headers, create_core, create_group, delete_core, delete_group, get_inbounds


# Disable caching for all tests
def dummy_cached(*args, **kwargs):
    def wrapper(func):
        return func

    return wrapper


aiocache.cached = dummy_cached


@pytest.fixture(autouse=True)
def mock_db_session(monkeypatch: pytest.MonkeyPatch):
    db_session = MagicMock(spec=TestSession)
    monkeypatch.setattr("app.settings.GetDB", db_session)
    monkeypatch.setattr("app.subscription.client_templates.GetDB", GetTestDB)
    monkeypatch.setattr("app.subscription.sub_update_buffer.GetDB", GetTestDB)
    # get_secret_key is cached, so through the app engine it would leave a pooled
    # connection on a TestClient loop that is closed after the request.
    monkeypatch.setattr("app.utils.jwt.GetDB", GetTestDB)
    return db_session


@pytest.fixture(autouse=True)
def mock_settings(monkeypatch: pytest.MonkeyPatch):
    settings = {
        "webhook": {
            "enable": False,
            "webhooks": [],
            "days_left": [3],
            "usage_percent": [80],
            "timeout": 180,
            "recurrent": 3,
            "proxy_url": None,
        },
        "notification_enable": {
            "admin": {
                "create": True,
                "modify": True,
                "delete": True,
                "reset_usage": True,
                "login": True,
            },
            "core": {
                "create": True,
                "modify": True,
                "delete": True,
            },
            "group": {
                "create": True,
                "modify": True,
                "delete": True,
            },
            "host": {
                "create": True,
                "modify": True,
                "delete": True,
                "modify_hosts": True,
            },
            "node": {
                "create": True,
                "modify": True,
                "delete": True,
                "connect": True,
                "error": True,
            },
            "subscription": {
                "create": True,
                "modify": True,
                "delete": True,
                "status_change": True,
                "reset_data_usage": True,
                "data_reset_by_next": True,
                "subscription_revoked": True,
            },
            "subscription_template": {
                "create": True,
                "modify": True,
                "delete": True,
            },
            "days_left": True,
            "percentage_reached": True,
        },
        "subscription": {
            "url_prefix": "",
            "update_interval": 12,
            "support_url": "https://t.me/",
            "profile_title": "Subscription",
            "host_status_filter": False,
            "randomize_order": False,
            "rules": [
                {
                    "pattern": "^([Cc]lash[\\-\\.]?[Vv]erge|[Cc]lash[\\-\\.]?[Mm]eta|[Ff][Ll][Cc]lash|[Mm]ihomo)",
                    "target": "clash_meta",
                },
                {"pattern": "^([Cc]lash|[Ss]tash)", "target": "clash"},
                {
                    "pattern": "^(SFA|SFI|SFM|SFT|[Kk]aring|[Hh]iddify[Nn]ext)|.*[Ss]ing[\\-b]?ox.*",
                    "target": "sing_box",
                },
                {"pattern": "^(SS|SSR|SSD|SSS|Outline|Shadowsocks|SSconf)", "target": "outline"},
                {"pattern": "^v2rayN", "target": "links_base64"},
                {"pattern": "^v2rayNG", "target": "links_base64"},
                {"pattern": "^[Ss]treisand", "target": "links_base64"},
                {"pattern": "^Happ", "target": "links_base64"},
                {"pattern": "^ktor\\-client", "target": "links_base64"},
                {"pattern": "^.*", "target": "links_base64"},
            ],
            "manual_sub_request": {
                "links": True,
                "links_base64": True,
                "xray": True,
                "wireguard": True,
                "sing_box": True,
                "clash": True,
                "clash_meta": True,
                "outline": True,
            },
            "applications": [
                {
                    "name": "Streisand",
                    "icon_url": "https://is1-ssl.mzstatic.com/image/thumb/Purple211/v4/1e/29/e0/1e29e04f-273b-9186-5f12-9bbe48c0fce2/AppIcon-0-0-1x_U007epad-0-0-0-1-0-85-220.png/460x0w.webp",
                    "import_url": "streisand://import/{url}",
                    "description": {
                        "en": "Flexible proxy client with rule-based setup, multiple protocols, and custom DNS. Supports VLESS(Reality), VMess, Trojan, Shadowsocks, Socks, SSH, Hysteria(V2), TUIC, Wireguard.",
                        "fa": "کلاینت پراکسی انعطاف‌پذیر با قوانین، پشتیبانی از پروتکل‌های متعدد و DNS سفارشی. پشتیبانی از VLESS(Reality)، VMess، Trojan، Shadowsocks، Socks، SSH، Hysteria(V2)، TUIC، WireGuard.",
                        "ru": "Гибкий прокси‑клиент с правилами, поддержкой множества протоколов и кастомным DNS. Поддерживаются VLESS(Reality), VMess, Trojan, Shadowsocks, Socks, SSH, Hysteria(V2), TUIC, Wireguard.",
                        "zh": "灵活的代理客户端，支持基于规则的配置、多种协议以及自定义 DNS。支持 VLESS(Reality)、VMess、Trojan、Shadowsocks、Socks、SSH、Hysteria(V2)、TUIC、Wireguard。",
                    },
                    "recommended": True,
                    "platform": "ios",
                    "download_links": [
                        {
                            "name": "Download",
                            "url": "https://apps.apple.com/us/app/streisand/id6450534064",
                            "language": "en",
                        },
                        {
                            "name": "دانلود",
                            "url": "https://apps.apple.com/us/app/streisand/id6450534064",
                            "language": "fa",
                        },
                        {
                            "name": "Скачать",
                            "url": "https://apps.apple.com/us/app/streisand/id6450534064",
                            "language": "ru",
                        },
                        {
                            "name": "下载",
                            "url": "https://apps.apple.com/us/app/streisand/id6450534064",
                            "language": "zh",
                        },
                    ],
                },
                {
                    "name": "SingBox",
                    "icon_url": "https://raw.githubusercontent.com/SagerNet/sing-box/refs/heads/dev-next/docs/assets/icon.svg",
                    "import_url": "sing-box://import-remote-profile?url={url}",
                    "description": {
                        "en": "A client that provides a platform for routing traffic securely.",
                        "fa": "Sing-box یک کلاینت برای مسیریابی امن ترافیک فراهم می‌کند.",
                        "ru": "Клиент, обеспечивающий безопасную маршрутизацию трафика.",
                        "zh": "提供安全流量路由的平台客户端。",
                    },
                    "recommended": False,
                    "platform": "ios",
                    "download_links": [
                        {
                            "name": "Download",
                            "url": "https://apps.apple.com/us/app/sing-box-vt/id6673731168",
                            "language": "en",
                        },
                        {
                            "name": "دانلود",
                            "url": "https://apps.apple.com/us/app/sing-box-vt/id6673731168",
                            "language": "fa",
                        },
                        {
                            "name": "Скачать",
                            "url": "https://apps.apple.com/us/app/sing-box-vt/id6673731168",
                            "language": "ru",
                        },
                        {
                            "name": "下载",
                            "url": "https://apps.apple.com/us/app/sing-box-vt/id6673731168",
                            "language": "zh",
                        },
                    ],
                },
                {
                    "name": "Shadowrocket",
                    "icon_url": "https://shadowlaunch.com/static/icon.png",
                    "import_url": "",
                    "description": {
                        "en": "A rule-based proxy utility client for iOS.",
                        "fa": "Shadowrocket یک ابزار پروکسی قانون‌محور برای iOS است.",
                        "ru": "Прокси‑клиент для iOS с маршрутизацией по правилам.",
                        "zh": "基于规则的 iOS 代理工具客户端。",
                    },
                    "recommended": False,
                    "platform": "ios",
                    "download_links": [
                        {
                            "name": "Download",
                            "url": "https://apps.apple.com/us/app/shadowrocket/id932747118",
                            "language": "en",
                        },
                        {
                            "name": "دانلود",
                            "url": "https://apps.apple.com/us/app/shadowrocket/id932747118",
                            "language": "fa",
                        },
                        {
                            "name": "Скачать",
                            "url": "https://apps.apple.com/us/app/shadowrocket/id932747118",
                            "language": "ru",
                        },
                        {
                            "name": "下载",
                            "url": "https://apps.apple.com/us/app/shadowrocket/id932747118",
                            "language": "zh",
                        },
                    ],
                },
                {
                    "name": "V2rayNG",
                    "icon_url": "https://raw.githubusercontent.com/2dust/v2rayNG/refs/heads/master/V2rayNG/app/src/main/ic_launcher-web.png",
                    "import_url": "v2rayng://install-config?url={url}",
                    "description": {
                        "en": "A V2Ray client for Android devices.",
                        "fa": "V2rayNG یک کلاینت V2Ray برای دستگاه‌های اندرویدی است.",
                        "ru": "Клиент V2Ray для устройств Android.",
                        "zh": "适用于 Android 设备的 V2Ray 客户端。",
                    },
                    "recommended": True,
                    "platform": "android",
                    "download_links": [
                        {
                            "name": "Download",
                            "url": "https://github.com/2dust/v2rayNG/releases/latest",
                            "language": "en",
                        },
                        {"name": "دانلود", "url": "https://github.com/2dust/v2rayNG/releases/latest", "language": "fa"},
                        {
                            "name": "Скачать",
                            "url": "https://github.com/2dust/v2rayNG/releases/latest",
                            "language": "ru",
                        },
                        {"name": "下载", "url": "https://github.com/2dust/v2rayNG/releases/latest", "language": "zh"},
                    ],
                },
                {
                    "name": "SingBox",
                    "icon_url": "https://raw.githubusercontent.com/SagerNet/sing-box/refs/heads/dev-next/docs/assets/icon.svg",
                    "import_url": "sing-box://import-remote-profile?url={url}",
                    "description": {
                        "en": "A client that provides a platform for routing traffic securely.",
                        "fa": "Sing-box یک کلاینت برای مسیریابی امن ترافیک فراهم می‌کند.",
                        "ru": "Клиент, обеспечивающий безопасную маршрутизацию трафика.",
                        "zh": "提供安全流量路由的平台客户端。",
                    },
                    "recommended": False,
                    "platform": "android",
                    "download_links": [
                        {
                            "name": "Download",
                            "url": "https://play.google.com/store/apps/details?id=io.nekohasekai.sfa&hl=en",
                            "language": "en",
                        },
                        {
                            "name": "دانلود",
                            "url": "https://play.google.com/store/apps/details?id=io.nekohasekai.sfa&hl=en",
                            "language": "fa",
                        },
                        {
                            "name": "Скачать",
                            "url": "https://play.google.com/store/apps/details?id=io.nekohasekai.sfa&hl=en",
                            "language": "ru",
                        },
                        {
                            "name": "下载",
                            "url": "https://play.google.com/store/apps/details?id=io.nekohasekai.sfa&hl=en",
                            "language": "zh",
                        },
                    ],
                },
                {
                    "name": "V2rayN",
                    "icon_url": "https://raw.githubusercontent.com/2dust/v2rayN/refs/heads/master/v2rayN/v2rayN.Desktop/v2rayN.png",
                    "import_url": "",
                    "description": {
                        "en": "A Windows V2Ray client with GUI support.",
                        "fa": "v2rayN یک کلاینت V2Ray برای ویندوز با پشتیبانی از رابط کاربری است.",
                        "ru": "V2Ray клиент для Windows с графическим интерфейсом.",
                        "zh": "带有图形界面的 Windows V2Ray 客户端。",
                    },
                    "recommended": True,
                    "platform": "windows",
                    "download_links": [
                        {
                            "name": "Download",
                            "url": "https://github.com/2dust/v2rayN/releases/latest",
                            "language": "en",
                        },
                        {"name": "دانلود", "url": "https://github.com/2dust/v2rayN/releases/latest", "language": "fa"},
                        {"name": "Скачать", "url": "https://github.com/2dust/v2rayN/releases/latest", "language": "ru"},
                        {"name": "下载", "url": "https://github.com/2dust/v2rayN/releases/latest", "language": "zh"},
                    ],
                },
                {
                    "name": "FlClash",
                    "icon_url": "https://raw.githubusercontent.com/chen08209/FlClash/refs/heads/main/assets/images/icon.png",
                    "import_url": "",
                    "description": {
                        "en": "A cross-platform GUI client for clash core.",
                        "fa": "Flclash یک کلاینت GUI چندسکویی برای clash core است.",
                        "ru": "Кроссплатформенный GUI-клиент для clash core.",
                        "zh": "跨平台 clash core 图形界面客户端。",
                    },
                    "recommended": False,
                    "platform": "windows",
                    "download_links": [
                        {
                            "name": "Download",
                            "url": "https://github.com/chen08209/FlClash/releases/latest",
                            "language": "en",
                        },
                        {
                            "name": "دانلود",
                            "url": "https://github.com/chen08209/FlClash/releases/latest",
                            "language": "fa",
                        },
                        {
                            "name": "Скачать",
                            "url": "https://github.com/chen08209/FlClash/releases/latest",
                            "language": "ru",
                        },
                        {
                            "name": "下载",
                            "url": "https://github.com/chen08209/FlClash/releases/latest",
                            "language": "zh",
                        },
                    ],
                },
                {
                    "name": "FlClash",
                    "icon_url": "https://raw.githubusercontent.com/chen08209/FlClash/refs/heads/main/assets/images/icon.png",
                    "import_url": "",
                    "description": {
                        "en": "A cross-platform GUI client for clash core.",
                        "fa": "Flclash یک کلاینت GUI چندسکویی برای clash core است.",
                        "ru": "Кроссплатформенный GUI-клиент для clash core.",
                        "zh": "跨平台 clash core 图形界面客户端。",
                    },
                    "recommended": True,
                    "platform": "linux",
                    "download_links": [
                        {
                            "name": "Download",
                            "url": "https://github.com/chen08209/FlClash/releases/latest",
                            "language": "en",
                        },
                        {
                            "name": "دانلود",
                            "url": "https://github.com/chen08209/FlClash/releases/latest",
                            "language": "fa",
                        },
                        {
                            "name": "Скачать",
                            "url": "https://github.com/chen08209/FlClash/releases/latest",
                            "language": "ru",
                        },
                        {
                            "name": "下载",
                            "url": "https://github.com/chen08209/FlClash/releases/latest",
                            "language": "zh",
                        },
                    ],
                },
                {
                    "name": "SingBox",
                    "icon_url": "https://raw.githubusercontent.com/SagerNet/sing-box/refs/heads/dev-next/docs/assets/icon.svg",
                    "import_url": "sing-box://import-remote-profile?url={url}",
                    "description": {
                        "en": "A client that provides a platform for routing traffic securely.",
                        "fa": "Sing-box یک کلاینت برای مسیریابی امن ترافیک فراهم می‌کند.",
                        "ru": "Клиент, обеспечивающий безопасную маршрутизацию трафика.",
                        "zh": "提供安全流量路由的平台客户端。",
                    },
                    "recommended": False,
                    "platform": "linux",
                    "download_links": [
                        {
                            "name": "Download",
                            "url": "https://github.com/SagerNet/sing-box/releases/latest",
                            "language": "en",
                        },
                        {
                            "name": "دانلود",
                            "url": "https://github.com/SagerNet/sing-box/releases/latest",
                            "language": "fa",
                        },
                        {
                            "name": "Скачать",
                            "url": "https://github.com/SagerNet/sing-box/releases/latest",
                            "language": "ru",
                        },
                        {
                            "name": "下载",
                            "url": "https://github.com/SagerNet/sing-box/releases/latest",
                            "language": "zh",
                        },
                    ],
                },
            ],
        },
        "hwid": {
            "enabled": True,
            "forced": False,
            "fallback_limit": 3,
            "min_limit": 1,
            "max_limit": 0,
        },
        "general": {"default_method": "chacha20-ietf-poly1305"},
    }
    db_settings = Settings(**settings)

    settings_mock = AsyncMock()
    settings_mock.return_value = db_settings

    monkeypatch.setattr("app.settings.get_settings", settings_mock)
    return settings


@pytest.fixture
def access_token() -> str:
    response = client.post(
        url="/api/admin/token",
        data={"username": "testadmin", "password": "testadmin", "grant_type": "password"},
    )
    return response.json()["access_token"]


@pytest.fixture
def disable_cache(monkeypatch: pytest.MonkeyPatch):
    def dummy_cached(*args, **kwargs):
        def wrapper(func):
            return func  # bypass caching

        return wrapper

    monkeypatch.setattr("app.settings.cached", dummy_cached)


@pytest.fixture
def core_entitlement_headers(access_token):
    """Use the real restricted API key path, never a staff bearer token."""
    username = f"core_owner_{uuid4().hex[:12]}"
    password = f"CoreOwner9!{uuid4().hex[:18]}"

    async def create_owner() -> None:
        async with TestSession() as db:
            db.add(Admin(username=username, hashed_password=await hash_password(password), role_id=1))
            await db.commit()

    async def delete_owner() -> None:
        async with TestSession() as db:
            owner = (await db.execute(select(Admin).where(Admin.username == username))).scalar_one_or_none()
            if owner is not None:
                await db.delete(owner)
                await db.commit()

    asyncio.run(create_owner())
    try:
        login = client.post(
            "/api/admin/token",
            data={"username": username, "password": password, "grant_type": "password"},
        )
        assert login.status_code == 200, login.text
        key = client.post(
            "/api/api_key",
            headers=auth_headers(login.json()["access_token"]),
            json={
                "name": f"vpn-core-{uuid4().hex[:10]}",
                "inherit_permissions": False,
                "permissions": {
                    "subscriptions": {"read": True, "update": True, "reset_usage": True, "set_entitlement": True}
                },
            },
        )
        assert key.status_code == 201, key.text
        for permission_model in (
            {"inherit_permissions": True},
            {"inherit_permissions": False, "permissions": {"subscriptions": {"read": True, "update": True}}},
        ):
            denied_key = client.post(
                "/api/api_key",
                headers=auth_headers(login.json()["access_token"]),
                json={"name": f"untrusted-{uuid4().hex[:12]}", **permission_model},
            )
            assert denied_key.status_code == 201, denied_key.text
            denial = client.put(
                f"/api/subscriptions/{uuid4()}/entitlement",
                headers={"X-Api-Key": denied_key.json()["api_key"]},
                json={"entitlement": "active", "revision": 1},
            )
            assert denial.status_code == 403, denial.text
        yield {"X-Api-Key": key.json()["api_key"]}
    finally:
        asyncio.run(delete_owner())


@pytest.fixture
def active_subscription_factory(access_token, core_entitlement_headers):
    """Real UUID subscription API and scoped Core entitlement, with isolated VPN group."""
    core: dict | None = None
    group: dict | None = None
    created: list[str] = []

    def create(
        *,
        creator_token: str | None = None,
        device_limit: int | None = None,
        group_ids: list[int] | None = None,
    ):
        nonlocal core, group
        if group_ids is None:
            if group is None:
                core = create_core(access_token)
                group = create_group(access_token, inbound_tags=[get_inbounds(access_token)[0]])
            group_ids = [group["id"]]
        sid = str(uuid4())
        headers = auth_headers(creator_token or access_token)
        body = {"uuid": sid, "group_ids": group_ids}
        if device_limit is not None:
            body["device_limit"] = device_limit
        response = client.post("/api/subscriptions", headers=headers, json=body)
        assert response.status_code == 201, response.text
        created.append(sid)
        active = client.put(
            f"/api/subscriptions/{sid}/entitlement",
            headers=core_entitlement_headers,
            json={"entitlement": "active", "revision": 1},
        )
        assert active.status_code == 200, active.text
        link = client.get(f"/api/subscriptions/{sid}/link", headers=headers)
        assert link.status_code == 200, link.text
        return {"uuid": sid, "url": link.json()["url"], "device_limit": response.json()["device_limit"]}

    try:
        yield create
    finally:
        for sid in reversed(created):
            removal = client.delete(f"/api/subscriptions/{sid}", headers=auth_headers(access_token))
            assert removal.status_code in (204, 404), removal.text
        if group is not None:
            delete_group(access_token, group["id"])
        if core is not None:
            delete_core(access_token, core["id"])
