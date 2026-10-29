# WayLane Agent binary control v1

One autonomous **Node** is a machine running a single autonomous Agent. The Agent
owns machine telemetry, registration and private core runtime; it can supervise
many independent cores later. Cores never receive Panel credentials or report
host/machine identity.

## Binary wire contract

The authoritative message schema is
`shared/contracts/proto/waylane/control/v1/control.proto`:

- **gRPC/HTTP2**: bidirectional `AgentControl.Connect(stream Envelope)`;
- **WebSocket**: `/api/node-control/control/ws?node_uuid=<uuid>`, subprotocol
  `waylane.pb.v1`, strictly WebSocket **binary frames** carrying the serialized
  exact same Protobuf `Envelope`;
- text frames, JSON over WebSocket, SSE and HTTP response streaming are **not
  supported as control transports**.

Each envelope contains a protocol version, random message ID, Node UUID,
optional core ID, generation, operation, typed Protobuf `Struct` payload,
status and redacted error code. There is no transport-specific command schema.
Xray configuration maps inside `Struct` are serialized as Protobuf binary on
both transports, not JSON on the network.

The Node initiates one outbound connection. In gRPC bidirectional streaming,
both the panel and the Node send independent Protobuf messages over that
single authenticated session; the panel does **not** initiate a separate
network connection to the Node. Management is full-duplex in both cases:
Panel pushes desired generations, Agent sends telemetry, acknowledges and
reports applied/restart-needed/error states. Server authenticates every
session with the existing per-Agent credential and requires approval.
Wrong/revoked identity is rejected. The child never receives that credential.

### Connection configuration

`CONTROL_TRANSPORT=grpc` is the default for a **new Node**.
Both HTTPS registration and bidirectional gRPC use the **one**
`CONTROL_PANEL_URL=https://hostname[:port]`. The Node derives its gRPC
host and port (443 by default) from this URL. The panel ingress MUST route
`/waylane.control.v1.AgentControl/Connect` over HTTP/2 to its gRPC server.
It does not need a separate Node-facing endpoint or a second operator URL.
Use `CONTROL_TRANSPORT=ws` as an explicit binary WebSocket alternative.
The Panel's optional gRPC listener is started with
`CONTROL_GRPC_BIND=host:port` (default off). Remote gRPC listener
**requires TLS** via `CONTROL_GRPC_TLS_CERT` and
`CONTROL_GRPC_TLS_KEY`; plaintext listeners bind loopback only.
Agent gRPC verifies CA certificates by default. Docker Desktop test-only
plaintext requires BOTH `DEV_ALLOW_HTTP=true` and
`GRPC_DEV_INSECURE=true`.
WebSocket derives `wss://` from the configured HTTPS Panel URL; `ws://`
is permitted only with the explicit local development HTTP flag.

Neither transport silently falls back. No legacy HTTP heartbeat/desired
control transport is available; Discovery enrollment uses a separate
authenticated HTTPS registration request.

### Semantics and bounds

- Session: binary `hello → hello_ack → desired`, periodic
  `telemetry`, `result → ack`, optional `ping/pong`.
- Messages are ≤ 2 MiB. The server uses bounded queues and refuses unknown
  versions, cores, status, stale generations and invalid identity.
- Desired generations are pulled from the durable DB and pushed while the
  streaming connection is active. The Agent persists applied state and
  suppresses duplicate execution after reconnect.
- Per-Agent connection is logical; one node can eventually have multiple
  independent cores, each identified by `core_id`. The first version
  currently targets `xray/default` only.
- Secrets are only placed in authorized Protobuf payloads; never expose them
  via telemetry, HTML, admin lists or logs.
- Management links do **not** carry external VPN client traffic. Node-owned
  ingress must explicitly publish applicable TCP/UDP engine ports.

The backend accepts a gRPC listener on a separate local address; an ingress
can route gRPC and WSS behind one public TLS domain/port only after proper
HTTP/2/WebSocket proxy validation. CDN support is optional, not required.

## Acceptance

The v1 source transports and Protobuf codecs are not evidence of a
production-compatible node deployment by themselves. Gate on authenticated
WS and gRPC interoperability, unauthorized/revoked rejection, reconnect
and monotonic generations, real Xray desired-state execution, telemetry
storage, private-key handling, port/traffic acceptance and rollback.

The previous WSS-only and HTTP-stream proposals are superseded. See Issues
#13 (Xray/PasarGuard parity), #14 (outer Agent orchestrator update), and
#15 (multi-core and binary control protocol).
