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

## WayLane Node — autonomous deployment

A Node is a complete installation: one-shot `bootstrap`, the internal
unprivileged `agent` controller, private rootless Docker and independent VPN
cores. Classic and Discovery are **registration methods**, not separate
Agent products.

Deployment entrypoints are owned by the root
[deploy/node/](../../deploy/node/README.md) tree:

- [Classic](../../deploy/node/classic/README.md)
- [Discovery](../../deploy/node/discovery/README.md) — preferred first isolated DEV pilot

Do not add new Node Compose entrypoints to `services/node/`.
See [Node runtime, enrollment and core ownership](agent/README.md).
The autonomous Node control plane uses one persistent binary WebSocket
with Protobuf frames. (Legacy PasarGuard node RPC is a separate runtime.) Public traffic remains unaccepted. Legacy PasarGuard
nodes are a separate deployment, not an alternate Node-control protocol.
