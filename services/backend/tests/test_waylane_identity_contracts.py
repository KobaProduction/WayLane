"""WayLane API key and node RPC identities are independent of upstream branding."""

from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from google.protobuf import descriptor_pb2
import grpc_tools
from grpc_tools import protoc

from app.db.crud.api_key import API_KEY_PREFIX, _generate_api_key
from app.routers import authentication
from app.utils.crypto import hash_api_key, verify_api_key
from app.controlpb import control_pb2


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


def test_control_protobuf_is_one_binary_websocket_envelope(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    project = backend.parent.parent
    proto = project / "shared/contracts/proto/waylane/control/v1/control.proto"
    assert proto.is_file()
    output = tmp_path / "control.pb"
    result = protoc.main([
        "grpc_tools.protoc",
        f"-I{proto.parent}",
        f"-I{Path(grpc_tools.__file__).parent / "_proto"}",
        f"--descriptor_set_out={output}",
        proto.name,
    ])
    assert result == 0
    descriptor = descriptor_pb2.FileDescriptorSet.FromString(output.read_bytes()).file[0]
    assert descriptor.package == "waylane.control.v1"
    assert not descriptor.service
    assert control_pb2.DESCRIPTOR.GetOptions().go_package == descriptor.options.go_package
    assert descriptor.options.go_package == (
        "github.com/KobaProduction/waylane/services/node/controlpb;controlpb"
    )
    assert set(control_pb2.Envelope.DESCRIPTOR.fields_by_name) == {
        "protocol_version", "node_uuid", "message_id", "kind",
        "core_id", "generation", "operation", "payload",
        "status", "error_code",
    }
