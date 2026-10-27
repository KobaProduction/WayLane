# WayLane Node Agent v2 — authoritative architecture and rollout gate

This **experimental, opt-in** replacement is separate from the currently
working legacy PasarGuard-compatible VPN node. It must never silently change
existing VPN cores, IPs, certificates, hosts, subscriptions or Coolify/system
Docker. Source tests do not authorize a production VPN traffic cutover.

## Required steady state: two Compose services, one embedded daemon

`services/node/docker-compose.agent.yaml` is **one deployment entrypoint**:

1. `bootstrap`: a privileged **one-shot** container. It initializes/checks
   three private named volumes (`agent-state`, `rootless-data`, `rootless-run`)
   owned by the fixed agent UID/GID, then exits successfully. It is never a
   long-running privileged broker. It does **not** install host software,
   create host systemd units or manage the host rootful Docker engine.
2. `agent`: the **only long-running Compose service**. The autonomous Go
   WayLane Agent runs as UID/GID 1000, starts and supervises official
   RootlessKit + dockerd **inside its own container**, and talks to it only
   through `/run/user/1000/docker.sock`. Its Docker data and node identity
   survive agent/container recreation through dedicated volumes.

There is **no third Docker Compose service**, no host systemd or `nsenter`
provisioner, no exposed Docker TCP endpoint and no bind mount of
`/var/run/docker.sock` (or any other host daemon socket). A daemon that is
spawned as an **agent child process** is *not* another Compose service.

## Panel-to-node control plane

The panel speaks to **WayLane Agent**, not directly to child VPN engines or
the internal Docker daemon. The agent is the sole orchestrator for its node.
Today it supports HTTPS discovery enrollment, durable UUID/per-agent secret,
manual admin approval/revocation, authenticated heartbeat, reported IP and
local rootless Docker readiness inspection. Approval does **not** yet grant
container-operation commands; enrollment is not production activation.

**Deferred engine plugin/runtime contract (design, NOT implemented):**
The agent will use its private rootless Docker to create, configure, update,
health-check, restart, stop and roll back separate containers for Xray,
sing-box, WireGuard, AmneziaWG and future external/custom engines. The panel
will express desired configuration/version and receive status **via the agent**.
Each engine/driver must have explicit capabilities, image provenance/version,
privilege/networking limits and independent lifecycle. Do not implement this
future capability as a separate Compose sidecar, systemd service or direct
panel-to-Docker API. Do not claim the original legacy node already implements
this child-container orchestration.

## Security and runtime boundaries

The agent container is **not privileged** and has no host Docker socket. To
run a nested rootless daemon on the Docker Desktop test platform, the current
Compose explicitly grants `SETUID`/`SETGID`, mounts `/dev/net/tun` and
sets `seccomp=unconfined` / `apparmor=unconfined`. These exceptions weaken
isolation despite Docker reporting `rootless`; they are not a general proof
of a safe production multi-tenant boundary. No automatic broadening of host
privileges is authorized. The embedded daemon currently uses `vfs` storage,
disables iptables and the default Docker bridge; therefore a basic nested
container test **does not establish VPN routing, host ingress or core traffic**.

The experimental agent must fail rather than use a rootful/system Docker
socket. Keep discovery credentials separate from node identity secrets. Store
secrets only in scoped runtime storage, never in source, built images or logs.

## Evidence levels and required acceptance

1. Source/CI: both image builds, exactly two Compose services, bootstrap
   completion, unprivileged agent startup and rootless daemon identification.
2. Isolated test host: clean install, repeated bootstrap, controlled restarts,
   persisted identity and Docker data, safe failure, non-destructive rollback.
3. Panel integration: real CA-verified HTTPS enrollment/approval/heartbeat,
   correct IP observations and explicit administrative access controls.
4. **Separate future work:** child VPN engine containers, networking/TUN,
   kernel capability limits, real client traffic, core updates and rollback.
   AmneziaWG end-to-end remains open in Issue #7.
5. Explicit operator approval for migrations. Until then the legacy node and
   its certificate/subscription paths remain untouched.

Current Docker Desktop evidence of a running rootless daemon and a disposable
nested container is a partial runtime probe, not acceptance of the full stack
or of any VPN protocol. The linked source task is WayLane Issue #11 and draft
PR #12; keep those descriptions synchronized with this document.
