# WayLane Node

A single autonomous Go program at `cmd/node/` manages the Node and its
independent VPN core containers using a private rootless Docker daemon.

## Runtime

- `bootstrap.sh` and `bootstrap.Dockerfile`: one-shot privileged
  initialization of private persistent volumes (UID/GID 1000).
- `node.Dockerfile`: one unprivileged long-lived Node with its own
  RootlessKit and private Docker socket.
- `go.mod`: minimal dependencies for binary WebSocket and Protobuf.
- `cmd/node/`: Node registration, telemetry, desired state, Xray child
  lifecycle and persistent identity.

The Node initiates a single full-duplex WSS session to the Control Panel
using the configured `CONTROL_PANEL_URL`. Commands and telemetry use
binary Protobuf `Envelope` messages. The Node name comes from a read-only
mount of the host's `/etc/hostname`; no `NODE_NAME` is needed.

**Deployment definitions are separate from source:**
[deploy/node/README.md](../../deploy/node/README.md). Choose either
Discovery (Node enrolls with a protected invitation) or Classic
(panel provisions its individual token). Each has one standalone
`docker-compose.yaml` and one `docker-compose.coolify.yaml`.

## Verification and limits

Run `./scripts/verify.sh` with Go 1.27+, or let CI run its equivalent.
The source and Compose checks do not prove runtime RootlessKit
compatibility, real TCP/UDP VPN traffic, multiple engines, or completed
initial enrollment. Pilot on an isolated host before using client traffic.
No management HTTP port or host Docker socket should be exposed.
