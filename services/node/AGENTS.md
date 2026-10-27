# WayLane Node — agent routing

Canonical source is `KobaProduction/waylane/services/node`; the standalone
`waylane-node` repository is retained for compatibility and rollback, not
current v2 development. Read root `AGENTS.md` and AI Agent Workflow task skills.

## Two independent node lifecycles

- Legacy `cmd/node/`, `controller/`, `backend/xray/`, `backend/wireguard/`:
  one existing PasarGuard-compatible node process, host networking and
  NET_ADMIN requirements; `DEPLOYMENT.md` owns this runtime's acceptance.
  Kernel WireGuard remains the default; AmneziaWG 3.1 is opt-in and requires
  separate real-traffic verification (Issue #7).
- New `bootstrap/`, `agent/`, `docker-compose.agent.yaml`: experimental
  WayLane Node Agent. **Read the complete `agent/README.md` as its authoritative
  lifecycle and topology contract before changing any of these files.**
  Exactly two Compose services, one-shot privileged bootstrap and autonomous
  non-privileged Go agent supervising its private embedded rootless Docker.
  No host systemd, independent rootless-engine Compose service, or host Docker
  socket. Panel-agent is the only intended management relationship. Single Xray DEV child is implemented; legacy users/stats/routing and
  additional plugin engines are deferred, not accepted.

`common/service.proto` remains the compatibility contract;
`scripts/verify.sh` runs source gates. Preserve provenance/licenses in
`UPSTREAM.md` and `LICENSING.md`. No production node migration without
explicit acceptance.
