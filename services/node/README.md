# WayLane Node

**Owner:** Intens · **License:** GPL-3.0 (upstream-derived)

WayLane Node is the Go-based VPN node agent for WayLane Backend. This starting source is based on the verified PasarGuard Node snapshot.

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

## Experimental WayLane Node Agent

An independent opt-in two-service deployment adds a privileged one-shot
`bootstrap` and an autonomous non-privileged `agent`. The agent starts and
supervises its own rootless Docker and persists its private socket/state. The
panel will eventually ask the agent to manage independently packaged Xray,
sing-box, WireGuard, AmneziaWG and extension engines. Today it only provides
discovery, authenticated heartbeat and daemon inspection; child-engine
installation, configuration, upgrades and rollback are deliberately deferred.
Canonical architectural contract: [agent/README.md](agent/README.md).
The old node and its deployment are not replaced.
