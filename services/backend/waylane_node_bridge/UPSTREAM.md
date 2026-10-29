# Source provenance for WayLane Node Bridge
Original implementation: https://github.com/PasarGuard/node_bridge_py
Pinned upstream source: f892a5ccb6cff597ec233854dea7abc46981d1bb (0.9.4)
Original license: GPL-3.0, preserved in LICENSE; copyright attribution retained.
This copy has been renamed and integrated into WayLane and remains GPL-3.0.
The regenerated protobuf sources use the WayLane namespace and file identities.
Both the Go node and Python bridge must be deployed together; pre-WayLane RPC
clients are intentionally incompatible. This is not a runtime PyPI
dependency and no external PasarGuardNodeBridge import is made.
