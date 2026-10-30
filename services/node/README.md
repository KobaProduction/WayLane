# Replaceable WayLane Node

The Node is not the installed Agent. The stable
[Agent](../agent/README.md) owns private rootless Docker and deploys or
updates Node as a separate image through a fixed local Unix API.
Node owns the outbound WSS/Protobuf connection and its VPN Core children.
It uses the Agent-owned rootless Docker socket and never starts a
second daemon. Changing a Node version does not restart Agent.

Node and all VPN Cores use host networking on rootless Docker >=29.5,
under an outer Agent with Linux host networking. Core TCP/UDP listen ports
can be changed dynamically without editing Compose or restarting Agent.
Rootless privileged ports below 1024 and real external traffic acceptance
still require separate verification.

The internal `coreDriver` dispatches desired-state Protobuf commands by exact
`core_id` without adding a separate management listener. Only `xray/default`
is registered; unsupported core IDs fail closed.

The AmneziaWG 3.1 preparation code (`amnezia.go` and `amnezia_uapi.go`)
validates the backend's AWG core configuration, constructs private UAPI
`set=1` requests with complete peer replacement, and exchanges those requests
through a bounded Unix socket. Peer keys and the server private key are never
passed as Docker CLI arguments. This is **source-level readiness only**:
there is no activated AWG core driver, child image launcher, peer synchronization
from the Panel, TUN address/route setup or UDP ingress. In particular, UAPI
`set=1` success by itself does not establish an interface route or VPN traffic.
The AmneziaWG-Go 3.1 implementation and license provenance are referenced in
`LICENSE.AMNEZIAWG` (upstream project: https://github.com/amnezia-vpn/amneziawg-go).

The experimental `awgCoreDriver` now has an independently persisted desired
`generation`, last accepted configuration and redacted result status. The
pinned image is only available for `linux/amd64`. Its child launcher requires
NET_ADMIN and `/dev/net/tun` within the **private rootless** Docker daemon;
address/link setup, peer UAPI updates and owned-container stop are implemented.
On replay, health must match the **persisted interface's exact private UAPI
socket**, not merely any socket in the volume. After a verified stop/removal,
only that interface's Unix socket is cleaned; symlinks or unexpected files
are never unlinked. Interface identity, port, MTU or address changes require
an explicit restart instead of silently issuing a partial live update.
The Panel uses a separate durable AWG desired table and sends commands only
to capability-advertising Nodes. **Normal deployments never advertise AWG.**

For a disposable Docker Desktop pilot only, both generic Compose profiles
expose `AWG_EXPERIMENTAL_DEV=false` by default. Opt-in requires that flag,
`DEV_ALLOW_HTTP=true`, an HTTP Panel origin on Docker Desktop/loopback,
`linux/amd64` and a visible character device `/dev/net/tun`. The Coolify
adapters hard-disable both DEV flags. The private Rootless Docker daemon
in Docker Desktop has been observed with Docker Engine 29.5.3 and the outer
Node's `/dev/net/tun` exists, but this is **not** a child capability or UDP
ingress test. The generic outer Compose currently publishes **no AWG UDP
port**; nested `--network host` alone must not be taken as externally
reachable. Real ingress/egress, peer handshake and encrypted packet exchange
remain separate acceptance gates. This remains an unaccepted experiment,
not production routing or a tested VPN service.

**Core priority:** AmneziaWG 3.1 is the next target engine; plain WireGuard
is a compatibility profile, not a second independent control stack. Backend
validation and the Core Editor support AWG configuration, but this Node
currently executes only Xray. Do not assign AWG capabilities or route client
traffic until a rootless TUN/UDP, lifecycle and peer-sync acceptance passes.
Plain WireGuard clients are compatible only when AWG obfuscation is disabled.

The Node initiates the single outbound binary-Protobuf WSS to Panel.
Node identity and Core state persist across updates. Agent controls Node
image lifetime only, through its local versioned Unix socket. See
shared/contracts/NODE_CONTROL.md, services/agent/README.md and
deploy/node/README.md. Live RootlessKit TUN/UDP client testing is pending.
