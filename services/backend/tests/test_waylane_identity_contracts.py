"""WayLane API key and node RPC identities are independent of upstream branding."""

from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from google.protobuf import descriptor_pb2
from grpc_tools import protoc

from app.db.crud.api_key import API_KEY_PREFIX, _generate_api_key
from app.routers import authentication
from app.utils.crypto import hash_api_key, verify_api_key
from waylane_node_bridge.common import service_pb2


def test_new_keys_use_one_waylane_prefix_and_fit_existing_mask_column():
    raw, trimmed = _generate_api_key()
    assert API_KEY_PREFIX == "waylane_"
    assert raw.startswith(API_KEY_PREFIX)
    assert UUID(raw.removeprefix(API_KEY_PREFIX)).version == 4
    assert trimmed.startswith(API_KEY_PREFIX)
    assert len(trimmed) <= 16  # Existing APIKey.api_key_trimmed VARCHAR(16)
    assert "pg_key_" not in raw + trimmed
    assert verify_api_key(raw, hash_api_key(raw))


@pytest.mark.asyncio
async def test_authentication_accepts_only_waylane_key_format(monkeypatch):
    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(authentication, "get_api_key_by_raw_key", lookup)
    uuid = "2fd0909f-ae6a-4fa6-8aae-91633acdce81"
    assert await authentication.get_admin_from_api_key(None, f"pg_key_{uuid}") is None
    lookup.assert_not_awaited()
    assert await authentication.get_admin_from_api_key(None, f"waylane_{uuid}") is None
    lookup.assert_awaited_once_with(None, f"waylane_{uuid}")


def test_python_and_go_node_schemas_are_identical(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    node = backend.parent / "node"
    descriptors = []
    for root, path in (
        (backend, "waylane_node_bridge/common/service.proto"),
        (node, "common/service.proto"),
    ):
        output = tmp_path / (root.name + "-node.pb")
        result = protoc.main(
            [
                "grpc_tools.protoc",
                f"-I{root}",
                f"--descriptor_set_out={output}",
                path,
            ]
        )
        assert result == 0
        file_proto = descriptor_pb2.FileDescriptorSet.FromString(output.read_bytes()).file[0]
        assert file_proto.package == "waylane.node.v1"
        assert file_proto.options.go_package == "github.com/KobaProduction/waylane/services/node/common"
        file_proto.ClearField("name")  # local source location is language-specific
        descriptors.append(file_proto.SerializeToString(deterministic=True))
    assert descriptors[0] == descriptors[1]
    assert service_pb2.DESCRIPTOR.package == "waylane.node.v1"
    assert service_pb2.DESCRIPTOR.name == "waylane_node_bridge/common/service.proto"
    assert service_pb2.DESCRIPTOR.services_by_name["NodeService"].full_name == "waylane.node.v1.NodeService"
    assert "pre_shared_key" in service_pb2.Wireguard.DESCRIPTOR.fields_by_name
    assert "masque" in service_pb2.Proxy.DESCRIPTOR.fields_by_name
