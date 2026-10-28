# WayLane Node Agent — authoritative architecture and rollout gate

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
readiness inspection. DEV now has a single pinned Xray child desired-state
contract (deploy/pull/update/restart/stop and per-inbound user replacement).
Enrollment alone does not authorize Docker operations or production activation.

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
privileges is authorized. The embedded daemon uses `vfs` storage. Its Docker bridge exists,
but Docker Desktop **does not yet support running a child container on that
bridge in this nested sandbox**: interface initialization fails while trying
to disable IPv6 on `eth0`; creating an IPv6-enabled bridge also fails
because `/proc/sys/net/ipv6/conf/default/forwarding` is read-only.
The standalone agent has working outbound HTTPS through the outer Compose
bridge. A disposable child Alpine container was able to use the inner
`--network host` (RootlessKit private network namespace) for outbound HTTPS,
but this is a **QA-only fallback**, not accepted isolation or VPN ingress.

Do not introduce host systemd, a separate Docker daemon service or a bind of
the host's rootful Docker socket to work around these constraints. Future VPN
engine publishing requires a reviewed network/privilege solution and actual
TCP/UDP connectivity checks (child → agent → host). Resource limits are also
not yet enforced for inner containers because the current nested rootless
daemon operates without cgroups.

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

## Agent authentication: two connection modes

The panel supports **two exclusive ways** to connect exactly the same WayLane
Agent. This follows the OpenFlare distinction between a shared discovery
invitation and a dedicated agent credential, without inheriting its VPN
runtime or WebSocket transport.

1. **Discovery:** create a reusable `wl_disc_...` token in the Nodes dashboard.
   Set `WAYLANE_DISCOVERY_TOKEN` on a new Agent. The Agent creates its UUID and
   private identity in `agent-state/identity.json`, enrolls once via
   `POST /api/agent/enroll`, then uses only its individual identity on
   authenticated heartbeat. Admin chooses auto-connect or manual approval.
2. **Direct Agent token:** in Nodes, create a named direct agent. An
   authorized admin receives **once** a `wl_agent_<uuid>_<random>` credential.
   Copy **only** `WAYLANE_AGENT_TOKEN` into Agent settings; the Agent extracts
   the UUID, saves token/UUID in its private persistent identity file and
   immediately begins authenticated heartbeat. No Discovery invitation or
   enrollment API call is needed. Admin may revoke the node, after which
   heartbeats return 403. Only the token hash is persisted server-side.

The two variables must **not** be provided simultaneously. Once an identity
has been registered and safely persisted, neither initial token is required
on a normal restart: a read-only/re-created container can reconnect from
the persistent volume. Existing persisted identities must not silently switch
to new credentials; an explicit identity migration/reset is required when
changing from one registration to a different node UUID. This protects a
valid registered agent from accidental takeover or duplicate registration.
An **unconfirmed** Discovery identity (`Registered=false`) may be replaced by
an explicitly supplied direct Agent token to transition an initial DEV trial
without deleting the entire Docker state volume. An already registered
identity cannot be rebound by changing environment variables.

Every registration is only a **control-plane identity**:
`allowed_operations: []`. Neither token activates VPN cores, grants
Docker management rights, writes legacy host/subscription IP addresses, nor
makes the remote panel reachable.

### DEV-only local HTTP

Production Agent endpoints require HTTPS with verified CA chains and reject
redirects carrying tokens. To test a panel **on the same development computer**
under Docker Desktop only, the opt-in combination
`WAYLANE_PANEL_URL=http://host.docker.internal:<port>` and
`WAYLANE_DEV_ALLOW_HTTP=true` is available. It accepts HTTP only to
`host.docker.internal` or literal loopback hosts; arbitrary LAN/Internet
HTTP addresses are always rejected. Never enable this mode on public,
untrusted or production networks, because credentials are transported without
TLS within the local Docker Desktop host link. The test panel must really
listen on a host-accessible port: an isolated Koba Terminal loopback address
cannot be reached from a laptop container by changing the token.

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

## Development deployment identity

This is **WayLane Node Agent**, not a separately versioned product.
The generic Compose project is `waylane-node`. For isolated development use
`waylane-node-dev` as the Compose project name; keep only the two service
names `bootstrap` and `agent`. Legacy node compatibility is tracked as a
distinct existing implementation, not by adding a version suffix to this
agent. Do not carry old experimental Compose project names into deployments.
Real panel enrollment requires a reachable trusted HTTPS origin and a real
Discovery token: synthetic `.invalid` credentials test only daemon startup.

## Xray DEV user synchronization

Admin `PUT /api/node-discovery/registrations/{uuid}/xray/inbounds/{tag}/users`
replaces accounts only in the named inbound using `expected_generation` for
optimistic concurrency; an unauthorized/revoked Agent, stale generation,
pending update, missing tag or duplicate/invalid user is rejected. Currently
supported layouts: SOCKS `settings.accounts`; VLESS, VMess and Trojan
`settings.clients`. Admin `GET .../xray/inbounds` returns names/counts
without passwords. The Agent validates the updated entire Xray configuration
and replaces the single Xray core; last running core survives invalid input.
This still lacks PasarGuard live SyncUsers, Shadowsocks 2022, statistics,
real region switching, multi-core drivers and accepted public UDP ingress.
See Issue #13 for full parity. Local DEV verified authenticated TCP SOCKS5
end-to-end; public VPN traffic is not accepted.


## Managed TLS certificates and Core Editor routing (DEV contract)

Autonomous Agents reuse **existing** WayLane resources rather than copying
the legacy node/core database or introducing a second certificate vault.

* TLS source of truth: `tls_presets`, with validated X.509 pairs, encrypted
  private keys, preset revision and the existing ACME/webhook rotation flow.
  `node_agent_tls_bindings` associates Agent UUID + exact TLS inbound tag
  with an existing preset. Only an authorized Agent receives decrypted
  certificate/key material through authenticated `GET /api/agent/xray/desired`.
  Admin `GET .../xray/tls` lists fingerprint, revision and expiry but never
  private keys. Bind/unbind and preset rotation increment the Xray desired
  generation to request reconciliation; legacy TLS bindings are unaffected.
* The Agent verifies certificate/key pairing and expiry again, writes the
  material into mode-0600 generation-specific private files under its state
  volume and injects Xray `certificateFile`/`keyFile` references **only in
  a separate runtime config**. The underlying desired JSON, Agent state
  report and admin API do not contain decrypted keys. Private runtime
  directory is mounted read-only into the child Xray container.
* Xray preflight runs before replacing the core; previous runtime config
  and its generation-scoped certificate files remain available for rollback.
  An intentionally stopped Xray is not implicitly restarted by a certificate
  rotation. The operator must explicitly start/update it.
* Routing is synchronized **from a saved Xray Core Editor CoreConfig** through
  `PUT /api/node-discovery/registrations/{uuid}/xray/routing/from-core` with
  `core_id` and `expected_generation`. The same full editor `routing`
  object (rules, domain strategy, balancers) is used. Only `config.routing`
  is changed; other inbounds/outbounds and users remain untouched.
  Invalid routing/unknown outbound references are subject to Agent Xray
  preflight and error reporting; previous core keeps serving traffic.

Both operations are scoped to approved Agents and RBAC permissions, use
optimistic generations, and never touch production legacy Node assignments.
**Still unaccepted:** live client TLS handshake after certificate rotation,
automatic propagation of every Core Editor save, true PasarGuard live routing
API updates, public VPN/UDP ingress and automatic certificate cleanup/expiry
alerts. The local DEV rollout requires a separate controlled test with a
real TLS inbound, not only source or database migration tests.
