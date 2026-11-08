"""Pure, provider-independent rendering and response-header contracts for subscriptions."""

from base64 import b64decode, b64encode

from app.models.settings import ConfigFormat, HeaderPlacement, SubRule, Subscription as SubscriptionSettings
from app.operation.subscription import SubscriptionOperation
from app.subscription.share import inject_body_placement


def test_format_rule_response_headers_supports_strings_and_json():
    rule = SubRule(
        pattern=r"^TestClient$",
        target=ConfigFormat.links,
        response_headers={
            "x-subheader": "Hello {SUBSCRIPTION_UUID}",
            "x-json": {"enabled": True, "count": 2},
        },
    )

    headers = SubscriptionOperation._format_rule_response_headers(rule, {"SUBSCRIPTION_UUID": "alice"})

    assert headers["x-subheader"] == "Hello alice"
    assert headers["x-json"] == '{"enabled":true,"count":2}'


def test_format_announce_supports_dynamic_variables():
    sub_settings = SubscriptionSettings(rules=[], announce="Hello {SUBSCRIPTION_UUID}, {DATA_LEFT} left")

    announce = SubscriptionOperation._format_announce(
        sub_settings,
        {"SUBSCRIPTION_UUID": "alice", "DATA_LEFT": "1 GB"},
    )

    assert announce == "Hello alice, 1 GB left"


def test_format_announce_url_supports_dynamic_variables():
    sub_settings = SubscriptionSettings(rules=[], announce_url="https://status.example.com/{SUBSCRIPTION_UUID}")

    announce_url = SubscriptionOperation._format_announce_url(
        sub_settings,
        {"SUBSCRIPTION_UUID": "alice"},
    )

    assert announce_url == "https://status.example.com/alice"


def test_detect_client_rule_matches_user_agent():
    rule = SubRule(
        pattern=r"^WayLaneRuleHeaderClient$",
        target=ConfigFormat.links,
        response_headers={"x-subheader": "Hello {SUBSCRIPTION_UUID}"},
    )

    matched_rule = SubscriptionOperation.detect_client_rule("WayLaneRuleHeaderClient", [rule])

    assert matched_rule is not None
    assert matched_rule.target == ConfigFormat.links
    assert matched_rule.response_headers["x-subheader"] == "Hello {SUBSCRIPTION_UUID}"


def test_split_headers_for_placement_keeps_headers_unchanged_by_default():
    headers = {"profile-update-interval": "1", "content-disposition": 'attachment; filename="x"'}

    result_headers, body_lines = SubscriptionOperation._split_headers_for_placement(
        headers, HeaderPlacement.header, ConfigFormat.links
    )

    assert result_headers == headers
    assert body_lines == []


def test_split_headers_for_placement_moves_headers_to_body_for_safe_format():
    headers = {
        "profile-update-interval": "1",
        "content-disposition": 'attachment; filename="x"',
        "cache-control": "no-store",
    }

    result_headers, body_lines = SubscriptionOperation._split_headers_for_placement(
        headers, HeaderPlacement.body_start, ConfigFormat.links
    )

    assert result_headers == {
        "content-disposition": 'attachment; filename="x"',
        "cache-control": "no-store",
    }
    assert body_lines == ["#profile-update-interval: 1"]


def test_split_headers_for_placement_falls_back_to_header_for_json_formats():
    headers = {"profile-update-interval": "1"}

    result_headers, body_lines = SubscriptionOperation._split_headers_for_placement(
        headers, HeaderPlacement.body_start, ConfigFormat.xray
    )

    assert result_headers == headers
    assert body_lines == []


def test_inject_body_placement_prepends_lines_for_plain_text():
    result = inject_body_placement("vless://example", ["#profile-update-interval: 1"], "body_start", False)

    assert result == "#profile-update-interval: 1\nvless://example"


def test_inject_body_placement_appends_lines_for_plain_text():
    result = inject_body_placement("vless://example", ["#profile-update-interval: 1"], "body_end", False)

    assert result == "vless://example\n#profile-update-interval: 1"


def test_inject_body_placement_prepends_lines_for_base64():
    encoded = b64encode(b"vless://example").decode()

    result = inject_body_placement(encoded, ["#profile-update-interval: 1"], "body_start", True)

    decoded = b64decode(result).decode()
    assert decoded == "#profile-update-interval: 1\nvless://example"


def test_inject_body_placement_appends_lines_for_base64():
    encoded = b64encode(b"vless://example").decode()

    result = inject_body_placement(encoded, ["#profile-update-interval: 1"], "body_end", True)

    decoded = b64decode(result).decode()
    assert decoded == "vless://example\n#profile-update-interval: 1"


def test_inject_body_placement_leaves_bytes_untouched():
    original = b"raw-binary-config"

    result = inject_body_placement(original, ["#profile-update-interval: 1"], "body_start", False)

    assert result is original
