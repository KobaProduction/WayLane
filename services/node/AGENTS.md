# WayLane Node — agent routing

Canonical source: https://github.com/KobaProduction/WayLane/tree/main/services/node
The deleted standalone node repository is not an active source or release authority.
Read root `AGENTS.md` and the relevant AI Agent Workflow skills.

## Two independent node lifecycles

- Legacy `cmd/node/`, `controller/`, `backend/xray/`, `backend/wireguard/`:
  one existing PasarGuard-compatible node process, host networking and
  NET_ADMIN requirements; `DEPLOYMENT.md` owns this runtime's acceptance.
  Kernel WireGuard remains the default; AmneziaWG 3.1 is opt-in and requires
  separate real-traffic verification (Issue #7).
- New `bootstrap/` and `agent/`: autonomous WayLane Node **source only**.
  Git-backed deployment definitions belong to
  `deploy/node/{classic,discovery}/` (one generic `docker-compose.yaml`,
  one `docker-compose.coolify.yaml` and a README per mode). Read
  `deploy/node/README.md` and the complete `agent/README.md` before
  changing runtime or deployment contracts.
  Exactly two Compose services, one-shot privileged bootstrap and autonomous
  non-privileged Go agent supervising its private embedded rootless Docker.
  No host systemd, independent rootless-engine Compose service, or host Docker
  socket. The panel communicates only with the internal node controller. Single Xray DEV child is implemented, but full legacy user/stats/routing
  parity and additional engines are deferred and not accepted.

`common/service.proto` is now a WayLane-native RPC contract, paired with the bundled Python bridge;
`scripts/verify.sh` runs source gates. Preserve provenance/licenses in
`UPSTREAM.md` and `LICENSING.md`. No production node migration without
explicit acceptance.
