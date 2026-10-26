# WayLane Node

**Owner:** Intens · **License:** GPL-3.0 (upstream-derived)

WayLane Node is the Go-based VPN node agent for WayLane Backend. This starting source is based on the verified PasarGuard Node snapshot.

**Available today:** Xray-core, WireGuard, gRPC/REST node control, node/user synchronization, health reporting and statistics.

**Planned:** sing-box, AmneziaWG, custom cores and enhanced failover. These features are not yet implemented.

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
