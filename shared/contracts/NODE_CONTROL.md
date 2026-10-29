# WayLane Node control v1 — binary WebSocket + Protobuf

The installed **Node** contains the one-shot bootstrap, an internal non-root
`agent` controller and private independently managed VPN cores. Only the
controller connects to the Control Panel; child VPN cores never receive
panel credentials or the host identity.

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
credentials and text frames are rejected. The control channel never
carries client VPN traffic and does not publish child core TCP/UDP ports.

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
