# WayLane autonomous Node

The only Node implementation is this Go module, built from `cmd/node`.
Read root `AGENTS.md`, `services/node/README.md`,
`deploy/node/README.md` and relevant AI Agent Workflow skills.

Node installs via `deploy/node/{classic,discovery}/` with exactly two
outer services: one-shot privileged bootstrap, non-root steady-state
agent controller that owns its private rootless Docker and VPN cores.
The control plane is **one outbound authenticated WSS connection** with
binary Protobuf. No secondary management transport, host Docker socket,
independent systemd service or extra Compose rootless sidecar.

`scripts/verify.sh` owns the Go source gate. The Go dependencies are
isolated to the Node's WSS/Protobuf runtime. Validate both generic and
Coolify Compose and the host runtime before declaring deployment ready.
Do not claim production traffic support from source tests alone.
