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
automatic registry connection for **new opt-in auto-approve invitations** (or
manual confirmation for older/manual invitations), admin revocation,
authenticated heartbeat, observed public egress IP and local rootless Docker
readiness inspection. Approval does **not** yet grant
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
privileges is authorized. The embedded daemon uses `vfs` storage and the **default rootless Docker bridge/NAT**;
iptables and bridge setup must not be disabled, because future child containers
need their own private network. Outer Compose uses its ordinary isolated bridge
for outbound agent-to-panel HTTPS, rather than Docker Desktop host networking.
A basic nested container test **does not establish VPN routing, host ingress or
core traffic**: publishing a future VPN listener requires two separately
verified mappings (child -> agent container -> Windows host), or an explicitly
accepted host-network alternative on a capable host. Do not expose the private
Docker API socket or invent unused Xray/WireGuard listener ports in the current
node deployment.

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

## Discovery token, automatic enrollment and address changes

The panel creates an invitation with a permanent, copy-once discovery token.
For **new invitations**, auto-connect defaults to enabled; the operator may
disable it per invitation. Existing invitations created before the additive
`b7e10d623aa8` migration retain manual approval. Enrollment is idempotent
for the same node UUID + private secret. Revoked nodes are never automatically
reconnected. In either mode, `allowed_operations` remains empty; discovery
approval NEVER authorizes engine management.

The agent sends an authenticated heartbeat every 15 seconds. At most once a
minute it determines public outbound IP via HTTPS (`api.ipify.org` for IPv4,
`api6.ipify.org` for IPv6) with strict validation and a bounded timeout; a
globally-routable local interface is the fallback. The registry updates IP
automatically when a valid new address arrives. If lookup fails, the
backend **preserves the previous address** instead of clearing it.

**IP caveat:** these are *outbound egress observations*, not proof that a
VPN listener is reachable from the Internet. NAT/CGNAT, an ingress proxy,
local `192.168.x.x` address or port forwarding can make them differ from a
valid public VPN endpoint. Do NOT automatically rewrite existing legacy
`nodes.address`, host allocations, DNS or subscription endpoints from
an unverified heartbeat. Future per-engine endpoint publication requires
approved node binding plus network reachability acceptance.

The agent requires a CA-verified HTTPS panel origin. Plain HTTP on a LAN IP
(e.g. `192.168.1.11`) is intentionally rejected; use a test TLS origin with
a trusted CA instead of bypassing TLS verification.
