"""Values that admins control must stay inert in subscription pages and notifications."""

import re
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.db.models import DataLimitResetStrategy, SubscriptionStatus
from app.models.admin import AdminModify
from app.templates import render_template
from app.utils.helpers import has_markup_unsafe_chars


def _render_subscription_page(**context) -> str:
    user = SimpleNamespace(
        uuid=uuid4(),
        status=SubscriptionStatus.active,
        data_limit=0,
        used_traffic=0,
        data_limit_reset_strategy=DataLimitResetStrategy.no_reset,
        on_hold_expire_duration=None,
        expire=None,
        on_hold_timeout=None,
    )
    return render_template(
        "subscription/index.html",
        {"user": user, "links": [], "announce": "", "announce_url": "", "apps": []} | context,
    )


def test_subscription_page_escapes_links():
    html = _render_subscription_page(links=["vless://id@host:443#a', alert(1), '\"><img src=x onerror=1>"])

    assert "<img src=x" not in html
    assert "copyLink('" not in html
    assert 'data-link="vless://id@host:443#a&#39;, alert(1), &#39;&#34;&gt;&lt;img src=x onerror=1&gt;"' in html
    assert 'onclick="copyLink(this.dataset.link, this)"' in html


def test_subscription_page_escapes_announce_but_keeps_line_breaks():
    html = _render_subscription_page(announce="first line\n<script>alert(1)</script>")

    assert "first line<br>&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_subscription_page_escapes_app_import_url():
    app = SimpleNamespace(
        icon_url="",
        name="App",
        recommended=False,
        platform=None,
        description={"en": ""},
        download_links=[],
        import_url="app://import/');alert(1);//",
    )
    html = _render_subscription_page(apps=[app])

    assert "importConfig('" not in html
    assert 'data-url="app://import/&#39;);alert(1);//" onclick="importConfig(this.dataset.url)"' in html


@pytest.mark.parametrize("value", ["a'b", 'a"b', "a<b", "a>b", "a`b", "a\\b", "a b", "a\tb", "a\nb", "a\x00b"])
def test_has_markup_unsafe_chars_rejects(value):
    assert has_markup_unsafe_chars(value)


@pytest.mark.parametrize("value", ["example.com", "*.example.com", "{SERVER_IP}", "[2001:db8::1]", "пример.рф", ""])
def test_has_markup_unsafe_chars_allows(value):
    assert not has_markup_unsafe_chars(value)


@pytest.mark.parametrize(
    "sub_domain",
    [
        "javascript:alert(1)//",
        "JavaScript:alert(1)",
        "data:text/html,x",
        "https://sub.example.com/'x",
        "https://a b.com",
    ],
)
def test_admin_sub_domain_rejects_unsafe_values(sub_domain):
    with pytest.raises(ValidationError):
        AdminModify(sub_domain=sub_domain)


@pytest.mark.parametrize(
    "sub_domain", ["https://sub.example.com", "https://*.example.com:8443/path", "sub.example.com", "", None]
)
def test_admin_sub_domain_allows_url_prefixes(sub_domain):
    assert AdminModify(sub_domain=sub_domain).sub_domain == sub_domain


_TELEGRAM_TAGS = re.compile(r"</?(b|i|u|s|code|pre|a|blockquote|tg-spoiler)(\s[^>]*)?>")
