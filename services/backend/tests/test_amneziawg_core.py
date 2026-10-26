"""AWG3.1 core validation, client export, and old-node fail-closed behavior."""
import base64
import io
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.wireguard import WireGuardConfig
from app.models.subscription import SubscriptionInboundData, TLSConfig, TCPTransportConfig
from app.operation.node import NodeOperation, require_amnezia_node_version
from app.subscription.wireguard import WireGuardConfiguration
from app.utils.crypto import generate_wireguard_keypair


def sample(awg: dict | None = None):
    private, public = generate_wireguard_keypair()
    core = {
        "interface_name": "awg0" if awg is not None else "wg0",
        "private_key": private,
        "listen_port": 51820,
        "address": ["10.90.0.1/24"],
    }
    if awg is not None:
        core["engine"] = "amneziawg"
        core["amnezia"] = awg
    return core, public


def test_wireguard_core_legacy_remains_unchanged():
    config, _ = sample()
    core = WireGuardConfig(config)
    assert core.get("engine") is None
    assert core.inbounds_by_tag["wg0"]["amnezia"] is None
    assert core.inbounds_by_tag["wg0"]["engine"] == "wireguard"


def test_amnezia_core_roundtrips_new_device_features():
    options = {
        "s1": 16, "s2": 16, "s3": 16, "s4": 16,
        "h1": "100-101", "h2": "200", "h3": "300", "h4": "400",
        "i1": "<r 5><t>", "random_trailers": True,
        "header_protection_key": base64.b64encode(b"k" * 32).decode(),
    }
    config, _ = sample(options)
    core = WireGuardConfig(config)
    assert core.inbounds_by_tag["awg0"]["amnezia"] == options
    assert core.inbounds_by_tag["awg0"]["engine"] == "amneziawg"
    assert WireGuardConfig.from_json(core.to_json()).inbounds_by_tag["awg0"]["amnezia"] == options


@pytest.mark.parametrize("options", [
    {"h1": "100-200", "h2": "180-240"},
    {"i1": "<t>\nprivate_key=stolen"},
    {"header_protection_key": "invalid"},
    {"header_protection_key": base64.b64encode(b"k"*32).decode(), "s1": 11},
    {"rekey_timeout": "10-2"},
    {"alien_property": True},
])
def test_amnezia_invalid_options_fail_closed(options):
    config, _ = sample(options)
    with pytest.raises(ValueError):
        WireGuardConfig(config)


def test_old_core_never_accepts_obfuscation_parameters():
    config, _ = sample({"s1": 16})
    config.pop("engine")
    with pytest.raises(ValueError):
        WireGuardConfig(config)


def test_awg_client_config_export_has_all_matching_values():
    options = {
        "s1": 16, "s2": 16, "s3": 16, "s4": 16,
        "h1": "100", "h2": "200", "h3": "300", "h4": "400",
        "header_protection_key": base64.b64encode(b"k"*32).decode(),
        "random_trailers": True, "disable_cookies": True,
        "content_padding_addition": "2-12",
    }
    config, server_public = sample(options)
    core = WireGuardConfig(config)
    inbound = SubscriptionInboundData.model_construct(
        remark="AWG", inbound_tag="awg0", protocol="wireguard",
        address="example.invalid", port=51820, network="udp",
        tls_config=TLSConfig(), transport_config=TCPTransportConfig(),
        wireguard_public_key=server_public,
        wireguard_allowed_ips=["0.0.0.0/0"],
        wireguard_amnezia=core.inbounds_by_tag["awg0"]["amnezia"],
    )
    user_private, _ = generate_wireguard_keypair()
    generator = WireGuardConfiguration()
    generator.add("AWG Client", "example.invalid", inbound,
                  {"private_key": user_private, "peer_ips": ["10.90.0.2/32"]})
    with zipfile.ZipFile(io.BytesIO(generator.render())) as archive:
        text = archive.read(archive.namelist()[0]).decode()
    for key, value in (
        ("S1", "16"), ("H1", "100"),
        ("HeaderProtectionKey", options["header_protection_key"]),
        ("RandomTrailers", "on"), ("DisableCookies", "on"),
        ("ContentPaddingAddition", "2-12"),
    ):
        assert f"{key} = {value}" in text
    inbound.wireguard_amnezia = None
    old_gen = WireGuardConfiguration()
    old_gen.add("Legacy", "example.invalid", inbound,
                {"private_key": user_private, "peer_ips": ["10.90.0.2/32"]})
    with zipfile.ZipFile(io.BytesIO(old_gen.render())) as archive:
        old_text = archive.read(archive.namelist()[0]).decode()
    assert "HeaderProtectionKey" not in old_text
    assert "H1 =" not in old_text


@pytest.mark.parametrize("version,allowed", [
    ("0.5.5", False), ("0.5.9", False), ("0.6.0", True),
    ("0.6.1", True), ("1.0.0", True), ("invalid", False), (None, False),
])
def test_amnezia_node_version_gate(version, allowed):
    if allowed:
        require_amnezia_node_version(version)
    else:
        with pytest.raises(ValueError):
            require_amnezia_node_version(version)


@pytest.mark.asyncio
async def test_awg_start_rejects_old_node_before_mutation():
    core_config, _ = sample({"h1": "100"})
    core = WireGuardConfig(core_config)
    node = SimpleNamespace(info=AsyncMock(return_value=SimpleNamespace(node_version="0.5.5")))
    db_node = SimpleNamespace(id=123, name="old", keep_alive=60)
    with pytest.raises(ValueError, match="0.6.0"):
        await NodeOperation._start_or_attach_node(node, db_node, core, [], 1)
    node.info.assert_awaited_once()



def test_awg_profiles_are_not_misadvertised_as_plain_wireguard():
    from app.subscription.clash import ClashConfiguration, ClashMetaConfiguration
    from app.subscription.links import StandardLinks
    from app.subscription.singbox import SingBoxConfiguration
    from app.subscription.xray import XrayConfiguration

    private, public = generate_wireguard_keypair()
    inbound = SubscriptionInboundData.model_construct(
        remark="AWG", inbound_tag="awg0", protocol="wireguard",
        address="example.invalid", port=51820, network="udp",
        tls_config=TLSConfig(), transport_config=TCPTransportConfig(),
        wireguard_public_key=public,
        wireguard_allowed_ips=["0.0.0.0/0"],
        wireguard_amnezia={"s1": 16, "h1": "100"},
    )
    settings = {"private_key": private, "peer_ips": ["10.0.0.2/32"]}
    for builder in (ClashConfiguration(), ClashMetaConfiguration(), SingBoxConfiguration()):
        assert builder._build_wireguard("AWG", "example.invalid", inbound, settings) is None
    assert XrayConfiguration()._build_wireguard("example.invalid", inbound, settings) == {}
    assert StandardLinks()._build_wireguard("AWG", "example.invalid", inbound, settings) == ""
