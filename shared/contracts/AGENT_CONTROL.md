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

## Target bidirectional protocol (not yet shipped)

The Agent initiates one CA-verified TLS / WebSocket connection to the
Panel. Multiplex both directions over the same versioned control channel:
commands, replies, telemetry, secrets and health. Each command has node UUID,
core ID, operation, monotonically increasing desired generation, unique
message ID, deadline, idempotency key and acknowledgement/result. Commands
to different cores have independent queues and resource budgets. Reconnect
replays only unacknowledged generations, not already applied operations.

The current authenticated HTTPS heartbeat + desired-state polling is a
compatibility transport; it is not yet this persistent WSS stream. Only the
Agent talks to the Panel, including when the Panel initiates a command via
its authenticated connection.

## Management vs client traffic

The unified Agent management connection uses the Panel HTTPS port.
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

Persistent WSS control transport, multiple core IDs and driver plugins,
one-click orchestrator-mediated Agent update, per-core Docker deployment
specification, production-grade networking/port allocation, public TCP/UDP
traffic, hardware-level machine stats, full legacy PasarGuard removal and
automatic editor preset reconciliation remain outstanding. Preserve legacy
rollback until explicit migration acceptance.

