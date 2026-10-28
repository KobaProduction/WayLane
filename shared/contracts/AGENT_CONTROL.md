# WayLane Node control plane

## Ownership

A Node is one managed machine with exactly one autonomous WayLane Agent.
The Agent is responsible for machine identity, control-plane connection,
machine telemetry, rootless Docker, Xray/other child-engine lifecycles and
traffic/port policies. Cores are separate workloads inside this Node; they
must not report machine metrics or possess Panel credentials.

The outer Compose remains exactly bootstrap (privileged one-shot) and agent
(unprivileged persistent). The Agent supervises private RootlessKit and
dockerd, never the host Docker socket or host systemd.

## Logical bidirectional control protocol (transport-neutral; not yet shipped)

The **protocol** is the stable, versioned command/event envelope and the
state machine that handles authentication, desired generations, acknowledgments,
results, machine telemetry and secret revisions. A transport merely carries
these messages. **No mandatory WebSocket requirement.** The Agent normally
initiates the outbound connection to the Panel, so no Agent administrative
port must be opened. Panel-initiated instructions travel back on that Agent
session or on the existing authenticated HTTPS compatibility transport.

The session is one *logical* Node identity and control relationship, not
necessarily one TCP socket or one physical HTTP request. A node can have
multiple cores, identified independently by core ID, while the Agent alone
is the authenticated Panel peer. The transport adapter must not change the
meaning, authorization, ordering or idempotency of commands.

### Transport adapters and defaults

| Adapter | Purpose | Communication shape | Status |
| --- | --- | --- | --- |
| **gRPC over HTTP/2** | Preferred direct Agent ↔ Panel control path when both endpoints support gRPC | Agent-initiated bidirectional streaming RPC, both sides send typed messages | **Planned**, not yet implemented |
| **HTTP streaming** | Alternative behind compatible HTTP reverse proxies or when native gRPC is unavailable | Downlink streaming response (e.g. SSE or newline-delimited events) and authenticated POST uplink; HTTP/2 request/response streaming may be used only after end-to-end acceptance | **Planned**, not yet implemented |
| **WebSocket over TLS** | Optional bidirectional adapter when the environment requires or supports it | Agent-initiated WSS upgraded connection carrying identical envelopes | **Optional**, not a prerequisite |
| **HTTPS request/response** | Compatibility and recovery | Existing authenticated heartbeat, desired-state polling and result reporting, with bounded retries | **Implemented** |

SSE is one-way **server → client**; an Agent receiving a stream from the
Panel still sends acknowledgments/telemetry via separate authenticated HTTP
requests. A two-way *logical* protocol is possible across multiple HTTP
requests; do not describe this as a single full-duplex SSE socket.

gRPC uses HTTP/2; some reverse proxies/CDNs may require explicit support for
long-lived streaming, HTTP/2, idle timeouts and buffering. CDN compatibility is
**opt-in**, not a reason to force an extra transport into every deployment.
For a particular CDN endpoint, accept a transport only after validating
authentication, stream delivery, reconnection, cancellation and timeouts.
Cloudflare's gRPC support has product-specific limitations; do not treat
enabling gRPC on a zone as proof that the entire management stack is safe.
See https://grpc.io/docs/what-is-grpc/core-concepts/ and
https://developers.cloudflare.com/network/grpc-connections/ .

Transport choice is explicit in configuration or negotiated from an
authenticated server capability response. A failure must not silently
downgrade security, reuse secrets across unaudited origins or bypass RBAC.
Keep CA-verified TLS, redirect protection, per-Agent identity, revocation,
replay protection and bounded message size/queue limits for every adapter.
An operator may choose an allowed fallback profile after verified failures.

### Shared envelope and state machine

Commands and events use the same schema across every adapter:
`protocol_version`, `message_id`, `node_uuid`, optional `core_id`,
`operation`, desired `generation`, deadline and bounded typed payload.
Replies refer to the original message and generation and report
`accepted`, `applying`, `applied`, `requires_restart`, `failed` or
`rolled_back` without logging private material.

A per-node session handshake authenticates the Agent and negotiates protocol
version, supported transports/capabilities and recovery cursor. The Agent
keeps per-core application state and monotonic generations durably. Reconnect
must not repeat an already applied command. Enforce per-core serialization,
bounded concurrency across cores, backpressure, maximum payload size and
deadlines independent of the underlying framing. Secrets are transported only
to their authorized Node Agent; child engines do not receive Panel credentials.
The Panel never connects directly to child Docker sockets or Xray's local API.

The existing authenticated HTTPS heartbeat/desired-state polling remains
fully valid as a working compatibility and recovery transport while the
streaming adapters are developed. Do **not** remove it or claim gRPC/WSS
runtime acceptance until a real end-to-end test passes.

## Management vs client traffic

The Agent control transport terminates on the Panel's configured HTTPS/gRPC management origin.
Xray, WireGuard and other data-plane client protocols still need separately
approved TCP/UDP listening endpoints on the Node. Do not claim a single
management port can carry every wire protocol without an explicit
multiplexing/proxy design and a proven UDP/TCP deployment.

## Xray runtime implementation

Explicit Xray 'update' pulls the pinned child image and recreates the Xray
container; it is not an update of the outer Agent container. Desired
'reconfigure' attempts internal Xray gRPC HandlerService and RoutingService
operations without restarting the Xray process: routing replacement,
VLESS/VMess/Trojan/Shadowsocks per-user changes and bounded inbound/outbound
handler changes. The management API is private loopback inside the child.
Some inbound handler changes briefly interrupt that listener's connections.
Failures attempt rollback, unsupported changes report requires_restart
instead of silently restarting. These changes require separate real-client
runtime acceptance before production use.

Managed TLS keys are distributed only to the authenticated Agent and
materialized as private 0600 files. Xray's own certificate-file reload
interval is normally up to one hour; immediate TLS switch can require a
controlled inbound handler replacement (without stopping the Xray process).

Node-level machine telemetry comes from the Agent. Values with scope
agent-runtime describe its container-visible Linux runtime, not the
physical Windows computer under Docker Desktop. Child cores report
only their own health, traffic, user and resource usage.

## Deferred parity

Persistent transport-neutral streaming control and optional adapters, multiple core IDs and driver plugins,
one-click orchestrator-mediated Agent update, per-core Docker deployment
specification, production-grade networking/port allocation, public TCP/UDP
traffic, hardware-level machine stats, full legacy PasarGuard removal and
automatic editor preset reconciliation remain outstanding. Preserve legacy
rollback until explicit migration acceptance.

