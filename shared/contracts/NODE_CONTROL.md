# WayLane Node control v1 — binary WebSocket + Protobuf

An installation has a stable **Agent** (owns private rootless Docker,
Node installation, upgrades and recovery) and a replaceable **Node** (owns
one outbound Panel WSS and manages separate VPN Cores). Outer Compose
contains only bootstrap plus Agent; Node and Cores are inner containers.
Agent has no VPN key material. The Agent local management API is a private
Unix socket v1, not an additional Panel transport or network listener.

## Transport: one outbound WSS session

The sole control transport is an authenticated **WebSocket with binary
Protobuf frames**, using the checked-in message schema
`shared/contracts/proto/waylane/control/v1/control.proto`.
The control socket is reached at
`wss://<control-panel-host>[:port]/api/node-control/control/ws?node_uuid=<uuid>`.
It requires subprotocol `waylane.pb.v1`; text frames and unrecognized
subprotocols are rejected. The Node derives this URL from the single
`CONTROL_PANEL_URL=https://<control-panel-host>[:port]`.

The Node establishes the connection. It is **full duplex**: the Panel can
push commands at any time over the active session; the Node sends
telemetry, acknowledgements, results and heartbeats over the same session.
No separate inbound connection, gRPC listener, system Docker socket or
published Node management port is required. An offline Node is unreachable
until it reconnects; the Panel retains durable desired state for replay.

Discovery enrollment uses an HTTPS request before the binary channel is
opened. The registered Node presents its individual secret as an
`X-Node-Secret` header, and the server verifies its approved identity
before accepting the WebSocket.

No gRPC Node control RPC, alternative transport selector, plaintext
remote WebSocket, text/JSON control frames, SSE, HTTP desired polling or
legacy credential aliases are supported. Local Docker Desktop-only
`DEV_ALLOW_HTTP=true` permits `ws://` to an explicit local endpoint;
never enable it on a public network.

## Wire semantics

Every Protobuf `Envelope` carries protocol_version, node_uuid,
message_id, kind, optional core_id, generation, operation, typed Struct
payload, status and redacted error_code. Maximum frame size: 2 MiB.
The control session supports:

- `hello → hello_ack`: authenticated session establishment
- `desired`: Panel → Node desired core generation/configuration
- `result → ack`: Node → Panel application result and acknowledgement
- `telemetry`: Node → Panel host/runtime metrics
- `ping → pong`: application-level liveness during idle periods

Bounded server queues protect memory. The Node persists its applied
generation; repeated commands after reconnect must not execute again.
Unknown versions/cores, stale generations, wrong identities, revoked
credentials and text frames are rejected. AmneziaWG 3.1 is the prioritized
next core (with plain WireGuard supported only when obfuscation is disabled),
but `awg/default` is reserved, not advertised or accepted by this Xray-only
Node yet. A source-tested AWG 3.1 UAPI codec and private-socket transport are
present, alongside an experimental AWG child driver and independent AWG
Panel desired state. Normal Nodes never advertise AWG. The driver is only
registered in an explicitly opted-in, local loopback Docker Desktop pilot;
Coolify disables this gate. **AWG is not accepted for production UDP traffic**
until rootless TUN, routing, peer handshake, recovery and bidirectional client
tests pass. The control channel never
carries client VPN traffic and does not publish child core TCP/UDP ports.


## Optional subscription admission for autonomous Xray inbounds

The Core Editor's *public* desired-state JSON is never an entitlement source.
An administrator must explicitly opt an approved Xray Node inbound into
WayLane-managed admission (`/api/node-registry/nodes/{node_uuid}/xray/managed-inbounds/{tag}`).
This is supported only for VLESS, VMess and Trojan, with a registered inbound
in the subscription Groups catalog and an initially empty manual client list.
SOCKS/HTTP test proxies are not enrolled implicitly.

The Panel computes client identities at authenticated binary WSS delivery
from the current durable `Subscription.uuid` and entitlement, group membership,
status, expiry, usage limit and administrator policy. The Xray-specific
`client.email` is always the **canonical subscription UUID**, never a
contact email. Raw client secrets are assembled only in memory for the WSS
`desired` payload; the stored Xray desired config and Node registry read API
contain no injected managed accounts. Subscription writes enqueue new Xray
generations (live reconfigure where supported); stopped Nodes remain stopped.
The WSS result/ack confirms Core configuration acceptance, **not** measured
VPN ingress, paid usage accounting, subscriber traffic or billing correctness.
Those require separate StatsService and ingress acceptance.

Source readiness is **not runtime acceptance**; do not enable this control on
production Nodes until the matching Backend migration and Node version are
validated, including revocation and reconnect under actual client traffic.


## Read-only cumulative Xray traffic observations (not billing)

The existing binary WSS permits an authenticated Node to send a Protobuf
`Envelope(kind="usage", core_id="xray/default", generation=0)` after it
advertises `xray.protobuf.binary` and **explicit** `xray.stats.readonly` in
that session. Collection is disabled unless the Node environment sets
`WAYLANE_XRAY_STATS_READONLY=true` (no new monitoring from a Git push).
`payload` is a
Struct with `epoch` (Docker Xray container's RFC3339 StartedAt timestamp) and
bounded `samples`: each sample contains canonical UUIDv4 `subscription_uuid`,
`uplink` and `downlink` cumulative bytes (exact integers, at most 2^53-1).
The Node reads *only* its private child Xray StatsService through a bounded
Docker exec using `xray api statsquery -pattern user>>> -reset=false`. A
missing StatsService or absent counters must not terminate the WSS heartbeat.
No public StatsService port, separate gRPC connection or text frame is added.

The Backend deduplicates consecutive and repeated reports in
`node_agent_xray_usage_cursors` by `(node_uuid, subscription_uuid)` and newer
Xray epoch. Observations for unopted-in/manual Nodes, unknown subscriptions,
older epochs or ambiguous counter resets are ignored rather than billed or
allowed to tear down the control channel. The admin read endpoint
`GET /api/node-registry/nodes/{node_uuid}/xray/usage` explicitly reports
`billing_authoritative=false`; total byte counters are encoded as decimal
strings to preserve 64-bit accuracy in JavaScript.

**Commercial accounting (separate explicit gate):** Backend's
`WAYLANE_XRAY_USAGE_BILLING_ENABLED=true` is opt-in and defaults off.
After accepted live tests of UUID client attribution and authenticated WSS,
each (Node, Subscription) cursor receives a first baseline without back
billing; subsequent cumulative deltas update `Subscription.used_traffic`
and the owning `Admin.used_traffic` in one database transaction.
Idempotent retries, old process epochs and counter resets cannot charge the
same recorded bytes twice. A quota crossing queues a new desired generation
inside the same transaction. Any unowned subscription UUID is never billed.
Turning billing off resets the baseline before a later re-enable.

`node_agent_xray_usage_hours` records per-hour observations by UUID
without a legacy numeric Node ID. Its time bucket represents receipt of
incremental StatsService counters, not the precise moment of client traffic.
These new hourly aggregates are separate from historical PasarGuard traffic.

**Not automatically enabled:** Xray itself requires `stats: {}` and policy
level 0 `statsUserUplink=true`, `statsUserDownlink=true`; these runtime fields
can require a deliberate Xray restart. Source tests do not prove counters are
collecting. Commercial usage updates are gated and do not alter entitlement;
without explicit accepted runtime opt-in, the entire stream remains read-only.
Real-traffic correctness, quota enforcement and multi-Node reconciliation
remain separate deployment acceptance gates.

## Deploy and acceptance

The Panel's existing same-origin reverse proxy must forward
`/api/node-control/control/ws` as a real HTTP/1.1 WebSocket Upgrade
to the backend, with long-lived proxy timeouts and TLS verification.
The backend owns Protobuf interpretation; the Node owns the rootless
Docker daemon. `CONTROL_PANEL_URL` is the **only** externally configured
control address. There is no gRPC endpoint or extra HTTP/2 ingress to
provision for Node control.

Before a first isolated DEV Node pilot verify:
HTTPS enrollment, binary WSS subprotocol negotiation, `hello_ack`,
authentication/revocation, telemetry/host hostname, desired-state push,
result/ack, ping/pong, reconnect and durable generation, and TLS
certificate validation. Real TCP/UDP VPN ingress and core compatibility
must be accepted separately. Never modify existing PasarGuard nodes,
certificates or customer routing as a side effect.

Related work: Issues #13 (VPN parity), #14 (Node lifecycle),
#15 (Node binary control).

## Dynamic Core TCP/UDP ports

The stable Agent outer container runs with Linux host networking. Node and
its rootless Docker 29.5+ child Cores use host networking too. This avoids
static Compose port lists: each Core dynamically binds TCP/UDP ports,
without an Agent restart. Ports below 1024 need a separate unprivileged
binding policy; cloud firewall, IPv6 and VPN client traffic are not
accepted by source/config tests.

## Stable Agent / replaceable Node release protocol

Control always travels as **binary Protobuf on the same authenticated Node WSS**:

- Backend `node_agent_operations` stores one operator-selected operation per
  Node with `operation_id` (UUIDv4), monotonic `generation`, `action` (`update`
  or `reconnect`), pinned approved `image`, `status`, `phase`, percentage and
  sanitized error code. Percentage marks lifecycle milestones, not actual
  downloaded image bytes. Admin endpoints are under `/api/node-registry/nodes/{uuid}/operation`.
- Only a Node currently advertising `node.release.v1` may receive
  `Envelope(kind="node_action",core_id="node/runtime", operation="update")`.
  The payload includes the same UUID operation ID and exact immutable image.
  The Node uses `/var/lib/waylane-agent/control/agent.sock`, not another
  network API or host Docker socket, to ask the stable Agent to update the
  **inner Node container only**.
- The Agent keeps a private operation journal, pulls before cutover, stages
  and replaces the Node, then waits for the new process to authenticate WSS.
  Successful container start is only `awaiting_wss` (85%), not success. On
  timeout or interruption, previous image selection is restored and the
  failure/rollback recorded. A restored previous image also must verify WSS
before the rollback is called successful; otherwise the terminal error is
`rollback_unverified`. If the entire operation remains unresolved beyond
10 minutes, Backend marks it `node_report_timeout` and permits a new explicit
operator action, never infinite retry. `node_action_report` carries sanitized status,
  percent, phase, previous image and code without Docker command output.
  Repeated commands with the same UUID are idempotent. The Backend rejects
  stale generations and terminal-state downgrade reports.
- `reconnect` is a separate `node_action` that gracefully closes the existing
  WSS connection; a *new authenticated hello* confirms completion. No Node or
  VPN Core process is restarted for reconnect.
- The outer Agent has a **different, manual orchestrator-controlled** update
  lifecycle. Neither this protocol nor regular Git pushes replace it. The
  Node GHCR package is built independently from `node-v*` tags; a published
  package is never automatically activated in a running Node installation.

A real release must verify Node heartbeat, private rootless Docker health,
Xray traffic on the existing SOCKS5 test and preservation of identity/volumes.
No automatic periodic update check, background agent self-updater or exposed
Agent TCP listener is introduced.
