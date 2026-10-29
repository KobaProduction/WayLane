# WayLane Node

**Project:** WayLane · **Legacy node license:** GPL-3.0 (upstream-derived)

This directory contains two distinct node runtimes: the legacy PasarGuard-compatible Go VPN node and the separate autonomous WayLane Node Agent. The legacy implementation originates from the verified PasarGuard Node snapshot.

**Available today:** Xray-core, WireGuard, gRPC/REST node control, node/user synchronization, health reporting and statistics.

**Existing legacy code:** opt-in AmneziaWG-Go 3.1 UAPI integration and frontend preset are present; real Linux TUN/UDP traffic acceptance remains open (Issue #7). **Planned:** sing-box, custom engines, enhanced failover and pluggable child-container orchestration.

Build: `make build`, or use `docker build -t waylane-node:dev .`. Existing data directories and `PG_NODE_*` settings are temporarily retained to preserve backward compatibility.

For upstream copyright and source commit see [UPSTREAM.md](UPSTREAM.md) and [LICENSE](LICENSE). **Do not run this baseline against production Intens nodes before migration acceptance.**


## AI workflow and verification

Read `AGENTS.md` and the task-specific rules from
[AI Agent Workflow](https://github.com/ArthurKoba/ai-agent-workflow).
On a new checkout enable the tracked hooks with
`git config --local core.hooksPath .githooks`; run `./scripts/verify.sh`
before attempting a push. CI runs the same repository-specific gate.

**Bootstrap limitation:** Passing these checks does not prove runtime
deployment compatibility, or complete the eventual Vue/HA migration.

Licensing note: GitHub root MIT `LICENSE` does not replace the upstream copyleft license applicable to derived code. See `LICENSING.md` and `LICENSE.UPSTREAM`.

Tests: `./scripts/verify.sh` provisions a pinned checksum-verified Xray executable and disposable TLS certs in ignored local directories before executing the existing integration tests. No production credentials are used.

## Autonomous WayLane Node

The new Node is a **single installation** comprising exactly two outer
Compose services: one-shot privileged \`bootstrap\` and non-privileged
\`agent\` (UID/GID 1000). The internal controller runs in a private
rootless Docker environment and can manage child VPN core containers.
No host Docker socket, host systemd or third Docker sidecar is allowed.

**Implementation is one Go module (\`services/node/go.mod\`)**. The legacy
PasarGuard binary builds from \`cmd/node\` and the autonomous controller
from \`cmd/agent\`. Their executables and Docker images are separate.
The one-shot initialization is a small \`bootstrap.sh\`, with
\`bootstrap.Dockerfile\`; the autonomous Node image uses
\`node.Dockerfile\`. These are source files, not deployment entrypoints.

[Discovery and Classic](../../deploy/node/README.md) are registration
modes. Each has its own generic and Coolify Compose under \`deploy/node/\`.
Discovery uses one protected invitation \`DISCOVERY_TOKEN\` to enroll; Classic
uses an individual \`NODE_TOKEN\` issued from the panel. A registered Node
persists its private identity in the \`agent-state\` volume and reads its
name from the host's read-only \`/etc/hostname\`. Never share an identity
volume across two Nodes.

The Node establishes one authenticated outbound WSS connection derived
from \`CONTROL_PANEL_URL\`, using only binary Protobuf \`Envelope\` frames:
hello/ack, desired commands, result/ack, telemetry and ping/pong.
The panel sends commands on the same full-duplex socket. No autonomous
Node gRPC control transport, alternate HTTP control fallback or published
management port is supported. See
[Node control protocol](../../shared/contracts/NODE_CONTROL.md).

DEV supports one Xray child, but complete PasarGuard parity, multiple
core drivers and public TCP/UDP VPN traffic require separate acceptance.
Legacy PasarGuard instances, data and client routing must not be touched
while testing autonomous Node.
