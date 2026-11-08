"""The generated Node control protobuf must load its transitive descriptors itself."""

import subprocess
import sys


def test_control_proto_import_in_new_python_interpreter():
    snippet = """from app.controlpb import control_pb2 as pb
assert 'Envelope' in pb.DESCRIPTOR.message_types_by_name
"""
    result = subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
