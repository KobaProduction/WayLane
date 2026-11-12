# WayLane autonomous Node

The only Node implementation is this Go module, built from the module root.
Read root `AGENTS.md`, `services/node/README.md`,
`deploy/node/README.md` and relevant AI Agent Workflow skills.

Node installs via `deploy/node/{classic,discovery}/` with exactly two
outer services: one-shot privileged bootstrap and stable non-root Agent.
The Agent owns private rootless Docker and upgrades the replaceable inner
Node; the Node owns the WSS session and its child VPN Cores. The inner Node
runs with UID 0 *inside the rootless user namespace*, mapped to the outer
non-root UID 1000. It must keep all capabilities dropped and
no-new-privileges, validate UID mapping at startup, and never use rootful
Docker or world-readable private Agent mounts.
The control plane is **one outbound authenticated WSS connection** with
binary Protobuf. No secondary management transport, host Docker socket,
independent systemd service or extra Compose rootless sidecar.

`scripts/verify.sh` owns the Go source gate. The Go dependencies are
isolated to the Node's WSS/Protobuf runtime. Validate both generic and
Coolify Compose and the host runtime before declaring deployment ready.
Experimental AWG 3.1 is gated by AWG_EXPERIMENTAL_DEV=true,
DEV_ALLOW_HTTP=true, a Docker Desktop/loopback Panel origin, linux/amd64 and
/dev/net/tun. Coolify explicitly disables the DEV flags. Backend independently
stores desired AWG generations; the WSS endpoint handles both core IDs.
Do not claim production traffic support from source tests alone.

## Subscription identity

The Node and Panel use a canonical UUIDv4 `subscription_uuid` for subscription
identity. The Xray adapter may populate its third-party Account.email field,
but that field is never a WayLane user email or alternate numeric identifier.
Do not restore the removed legacy Node RPC or numeric user identifiers.
