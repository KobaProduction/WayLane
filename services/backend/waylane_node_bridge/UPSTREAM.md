# Source provenance for WayLane Node Bridge
Original implementation: https://github.com/PasarGuard/node_bridge_py
Pinned upstream source: f892a5ccb6cff597ec233854dea7abc46981d1bb (0.9.4)
Original license: GPL-3.0, preserved in LICENSE; copyright attribution retained.
This copy has been renamed and integrated into WayLane and remains GPL-3.0.
The generated service_pb2.py and service.proto carry frozen original protobuf
file identities, because changing a descriptor without regenerating both the Go
and Python sides could break node interoperability. This is not a runtime PyPI
dependency and no external PasarGuardNodeBridge import is made.
